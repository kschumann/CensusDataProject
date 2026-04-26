import boto3
import pandas as pd
import io
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = "census-raw-data-417995075318-us-east-1-an"
POP_KEY = "pep/pep_county_2010_2019.csv"
COMP_KEY = "pep/pep_county_components_2010_2019.csv"
OUTPUT_KEY = "pep/pep_county_merged_2010_2019.csv"

def lambda_handler(event, context):
    s3 = boto3.client("s3")

    def read_csv(key):
        logger.info(f"Reading s3://{BUCKET}/{key}")
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return pd.read_csv(io.BytesIO(obj["Body"].read()))

    pop = read_csv(POP_KEY)
    comp = read_csv(COMP_KEY)

    # Map PERIOD_CODE 1–10 → DATE_CODE 2–11
    comp = comp.copy()
    comp["DATE_CODE"] = comp["PERIOD_CODE"] + 1

    # Ensure join keys are same type
    for df in [pop, comp]:
        df["state"] = df["state"].astype(str).str.zfill(2)
        df["county"] = df["county"].astype(str).str.zfill(3)
        df["DATE_CODE"] = df["DATE_CODE"].astype(int)

    logger.info(f"pop rows: {len(pop)}, comp rows: {len(comp)}")

    merged = pop.merge(
        comp,
        on=["state", "county", "DATE_CODE"],
        how="inner",
        suffixes=("_pop", "_comp")
    )

    logger.info(f"merged rows: {len(merged)}")

    # Drop duplicate NAME column from components side
    if "NAME_comp" in merged.columns:
        merged.drop(columns=["NAME_comp"], inplace=True)
        merged.rename(columns={"NAME_pop": "NAME"}, inplace=True)

    # Write output
    buf = io.StringIO()
    merged.to_csv(buf, index=False)
    s3.put_object(Bucket=BUCKET, Key=OUTPUT_KEY, Body=buf.getvalue().encode("utf-8"))
    logger.info(f"Written to s3://{BUCKET}/{OUTPUT_KEY}")

    return {
        "statusCode": 200,
        "body": f"Merged {len(merged)} rows → s3://{BUCKET}/{OUTPUT_KEY}"
    }
