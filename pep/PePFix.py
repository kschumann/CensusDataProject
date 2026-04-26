import json

import json
import io
import boto3
import pandas as pd
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = "census-raw-data-417995075318-us-east-1-an"
REGION    = "us-east-1"
KEY       = "pep/pep_county_merged_master.csv"

# 2020-2024 PERIOD_CODE values (1-5) map to continuation values (11-15)
PERIOD_CODE_MAP = {1: 11, 2: 12, 3: 13, 4: 14, 5: 15}


def lambda_handler(event, context):
    s3 = boto3.client("s3", region_name=REGION)

    # --- Read master ---
    logger.info(f"Reading s3://{S3_BUCKET}/{KEY}")
    obj = s3.get_object(Bucket=S3_BUCKET, Key=KEY)
    master = pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=str)
    logger.info(f"Master rows: {len(master)}")

    # --- Identify 2020-2024 rows by PERIOD_CODE values 1-5 ---
    # 2010-2019 rows already use 1-10, so we scope the remap to rows
    # where DATE_CODE >= 2 (i.e., 2020+) to avoid touching 2010-2019
    # rows that also have PERIOD_CODE 1-5.
    master["DATE_CODE"]   = pd.to_numeric(master["DATE_CODE"],   errors="coerce")
    master["PERIOD_CODE"] = pd.to_numeric(master["PERIOD_CODE"], errors="coerce")

    is_2020_2024 = master["DATE_CODE"] >= 2  # DATE_CODE 2-6 = 2020-2024 estimates
    # But DATE_CODE 2-6 also exists in 2010-2019 (DATE_CODE 2=2011 ... 11=2019 base).
    # Use the fact that 2010-2019 PERIOD_CODE maxes at 10; new rows have 1-5.
    # Safe discriminator: rows where PERIOD_CODE <= 5 AND DATE_CODE <= 6
    # would still be ambiguous. Instead, use PERIOD_DESC year content.
    # Simplest unambiguous discriminator: DATE_DESC contains "2020"-"2024".
    is_2020_2024 = master["DATE_DESC"].str.contains(
        "2020|2021|2022|2023|2024", na=False
    )

    before = master.loc[is_2020_2024, "PERIOD_CODE"].value_counts().to_dict()

    master.loc[is_2020_2024, "PERIOD_CODE"] = (
        master.loc[is_2020_2024, "PERIOD_CODE"]
        .map(PERIOD_CODE_MAP)
    )

    after = master.loc[is_2020_2024, "PERIOD_CODE"].value_counts().to_dict()
    logger.info(f"PERIOD_CODE before remap: {before}")
    logger.info(f"PERIOD_CODE after remap:  {after}")

    unmapped = master.loc[is_2020_2024, "PERIOD_CODE"].isna().sum()
    if unmapped > 0:
        logger.warning(f"{unmapped} rows in 2020-2024 could not be remapped")

    # --- Write back ---
    buf = io.StringIO()
    master.to_csv(buf, index=False)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=KEY,
        Body=buf.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )
    logger.info(f"Written to s3://{S3_BUCKET}/{KEY}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "total_rows": len(master),
            "rows_remapped": int(is_2020_2024.sum()),
            "unmapped_rows": int(unmapped),
        }),
    }
