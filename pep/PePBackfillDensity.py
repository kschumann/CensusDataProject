"""
pep_density_backfill.py
Lambda function to backfill missing DENSITY values in pep_county_merged_master.csv.

APPROACH:
  1. Build a per-county area lookup from rows where both POP and DENSITY are
     already populated: area = POP / DENSITY (averaged across all valid rows
     for that county to smooth rounding variance). SURFACE_AREA is used directly
     where available, as it is more stable than the derived figure.
  2. For any row where DENSITY is null but POP is populated, compute:
       DENSITY = POP / area
  3. Write the updated master back to S3.

This is safe to run multiple times — it only fills null DENSITY values and
will not overwrite rows that already have DENSITY populated.
"""

import io
import json
import logging

import boto3
import pandas as pd

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET  = "census-raw-data-417995075318-us-east-1-an"
REGION     = "us-east-1"
KEY_MASTER = "pep/pep_county_merged_master.csv"


def read_s3_csv(s3, key):
    logger.info(f"Reading s3://{S3_BUCKET}/{key}")
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=str)


def write_s3_csv(s3, df, key):
    logger.info(f"Writing {len(df)} rows to s3://{S3_BUCKET}/{key}")
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=buf.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )


def build_area_lookup(df):
    """
    Build (state, county) -> area (sq miles) lookup.

    Priority:
      1. Mean of SURFACE_AREA across all rows where it is populated.
      2. Fallback: mean of POP / DENSITY across rows where both are valid
         and non-zero (for counties missing SURFACE_AREA entirely).
    """
    df = df.copy()
    df["POP"]          = pd.to_numeric(df["POP"],          errors="coerce")
    df["DENSITY"]      = pd.to_numeric(df["DENSITY"],      errors="coerce")
    df["SURFACE_AREA"] = pd.to_numeric(df["SURFACE_AREA"], errors="coerce")

    # Source 1: SURFACE_AREA column
    sa_lookup = (
        df[df["SURFACE_AREA"].notna() & (df["SURFACE_AREA"] > 0)]
        .groupby(["state", "county"])["SURFACE_AREA"]
        .mean()
        .to_dict()
    )

    # Source 2: POP / DENSITY for counties with no SURFACE_AREA
    derived = df[
        (df["DENSITY"] > 0) & (df["POP"] > 0) & df["SURFACE_AREA"].isna()
    ].copy()
    derived["_area"] = derived["POP"] / derived["DENSITY"]
    derived_lookup = (
        derived.groupby(["state", "county"])["_area"]
        .mean()
        .to_dict()
    )

    # Merge: prefer SURFACE_AREA-derived, fall back to POP/DENSITY-derived
    lookup = {**derived_lookup, **sa_lookup}
    logger.info(
        f"Area lookup: {len(sa_lookup)} from SURFACE_AREA, "
        f"{len(derived_lookup)} from POP/DENSITY fallback, "
        f"{len(lookup)} total counties"
    )
    return lookup


def lambda_handler(event, context):
    s3 = boto3.client("s3", region_name=REGION)

    # 1. Read master
    df = read_s3_csv(s3, KEY_MASTER)
    logger.info(f"Master rows: {len(df)}")

    total_missing_before = df["DENSITY"].isna().sum() if "DENSITY" in df.columns else len(df)
    logger.info(f"Rows missing DENSITY before backfill: {total_missing_before}")

    # 2. Build area lookup from all rows in the file
    area_lookup = build_area_lookup(df)

    # 3. Convert POP and DENSITY to numeric for computation
    df["POP"]     = pd.to_numeric(df["POP"],     errors="coerce")
    df["DENSITY"] = pd.to_numeric(df["DENSITY"], errors="coerce")

    # 4. Fill missing DENSITY values
    filled = 0
    no_area = 0

    for idx, row in df[df["DENSITY"].isna() & df["POP"].notna()].iterrows():
        key  = (row["state"], row["county"])
        area = area_lookup.get(key)

        if area and area > 0 and row["POP"] > 0:
            df.at[idx, "DENSITY"] = round(row["POP"] / area, 4)
            filled += 1
        else:
            no_area += 1

    logger.info(f"DENSITY filled: {filled}, skipped (no area): {no_area}")

    total_missing_after = df["DENSITY"].isna().sum()
    logger.info(f"Rows missing DENSITY after backfill: {total_missing_after}")

    # 5. Convert DENSITY back to string to match master dtype convention
    df["DENSITY"] = df["DENSITY"].where(df["DENSITY"].isna(), df["DENSITY"].astype(str))

    # 6. Write updated master back to S3
    write_s3_csv(s3, df, KEY_MASTER)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "output":               f"s3://{S3_BUCKET}/{KEY_MASTER}",
            "master_rows":          len(df),
            "density_missing_before": int(total_missing_before),
            "density_filled":       filled,
            "density_skipped_no_area": no_area,
            "density_missing_after": int(total_missing_after),
        }),
    }
