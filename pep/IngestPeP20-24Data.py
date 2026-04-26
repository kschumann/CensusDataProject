import json

import json
import io
import boto3
import urllib.request
import urllib.parse
import csv

"""
Lambda: Pull Census PEP 2020-2024 county data and write long-format CSVs to S3.

Source: Single FTP flat file (CO-EST2024-ALLDATA.csv) which contains both
population totals and components of change. We split it into two outputs
mirroring the 2010-2019 schema:
  - pep_county_2020_2024.csv       (population)
  - pep_county_components_2020_2024.csv  (components)

DENSITY is not available in the 2020-2024 flat file and is omitted.
"""

S3_BUCKET = "census-raw-data-417995075318-us-east-1-an"
S3_PREFIX = "pep"
REGION = "us-east-1"

FTP_URL = (
    "https://www2.census.gov/programs-surveys/popest/datasets/"
    "2020-2024/counties/totals/co-est2024-alldata.csv"
)

# Years covered in the flat file
YEARS = [2020, 2021, 2022, 2023, 2024]

# DATE_CODE mapping for 2020s series:
# 1 = April 1 2020 base; 2 = July 1 2020; ... 6 = July 1 2024
# POPESTIMATE2020 corresponds to DATE_CODE 2, etc.
YEAR_TO_DATE_CODE = {
    2020: 2,
    2021: 3,
    2022: 4,
    2023: 5,
    2024: 6,
}
YEAR_TO_DATE_DESC = {
    2020: "7/1/2020 population estimate",
    2021: "7/1/2021 population estimate",
    2022: "7/1/2022 population estimate",
    2023: "7/1/2023 population estimate",
    2024: "7/1/2024 population estimate",
}

# Rate fields are not available for 2020 (partial period Apr-Jun only).
# RBIRTH2020 etc. do not exist in the file; rates start at 2021.
RATE_YEARS = [2021, 2022, 2023, 2024]


def get_api_key():
    ssm = boto3.client("ssm", region_name=REGION)
    response = ssm.get_parameter(Name="/census/api_key", WithDecryption=True)
    return response["Parameter"]["Value"]


def fetch_flat_file():
    """Download the Census FTP flat file and return parsed rows as list of dicts."""
    print(f"Fetching flat file: {FTP_URL}")
    with urllib.request.urlopen(FTP_URL, timeout=120) as response:
        raw = response.read().decode("latin-1")  # Census files often use latin-1
    reader = csv.DictReader(io.StringIO(raw))
    rows = [r for r in reader if r.get("SUMLEV", "").strip() == "050"]
    print(f"Flat file: {len(rows)} county rows parsed")
    return rows


def build_population_rows(flat_rows):
    """
    Melt wide flat file into long population rows mirroring 2010-2019 schema:
    NAME, POP, STATE, DATE_CODE, DATE_DESC, state, county
    Note: DENSITY omitted (not in source).
    """
    header = ["NAME", "POP", "STATE", "DATE_CODE", "DATE_DESC", "state", "county"]
    out = [header]
    for row in flat_rows:
        state = row["STATE"].zfill(2)
        county = row["COUNTY"].zfill(3)
        name = f"{row['CTYNAME']}, {row['STNAME']}"
        for year in YEARS:
            pop_col = f"POPESTIMATE{year}"
            if pop_col not in row:
                continue
            out.append([
                name,
                row[pop_col],
                state,
                str(YEAR_TO_DATE_CODE[year]),
                YEAR_TO_DATE_DESC[year],
                state,
                county,
            ])
    return out


def build_components_rows(flat_rows):
    """
    Melt wide flat file into long components rows mirroring 2010-2019 schema.
    PERIOD_CODE maps to the estimate year (1=2020, 2=2021, ... 5=2024).
    DATE_CODE mirrors YEAR_TO_DATE_CODE.
    Rate columns (RBIRTH, etc.) are NULL for 2020 (not published).
    Renames: NATURALCHG->NATURALINC, RNATURALCHG->RNATURALINC to match 2010-2019.
    """
    header = [
        "NAME", "STATE", "PERIOD_CODE", "PERIOD_DESC",
        "DATE_CODE",
        "BIRTHS", "DEATHS", "NATURALINC",
        "INTERNATIONALMIG", "DOMESTICMIG", "NETMIG",
        "RESIDUAL",
        "RBIRTH", "RDEATH", "RNATURALINC",
        "RINTERNATIONALMIG", "RDOMESTICMIG", "RNETMIG",
        "state", "county",
    ]
    out = [header]
    for row in flat_rows:
        state = row["STATE"].zfill(2)
        county = row["COUNTY"].zfill(3)
        name = f"{row['CTYNAME']}, {row['STNAME']}"
        for i, year in enumerate(YEARS, start=1):
            period_desc = (
                f"4/1/2020 to 6/30/2020" if year == 2020
                else f"7/1/{year-1} to 6/30/{year}"
            )

            def g(col):
                """Get column value or empty string if not present."""
                return row.get(col, "")

            # Rates not published for 2020
            if year in RATE_YEARS:
                rbirth = g(f"RBIRTH{year}")
                rdeath = g(f"RDEATH{year}")
                rnaturalinc = g(f"RNATURALCHG{year}")
                rinternationalmig = g(f"RINTERNATIONALMIG{year}")
                rdomesticmig = g(f"RDOMESTICMIG{year}")
                rnetmig = g(f"RNETMIG{year}")
            else:
                rbirth = rdeath = rnaturalinc = ""
                rinternationalmig = rdomesticmig = rnetmig = ""

            out.append([
                name,
                state,
                str(i),                              # PERIOD_CODE 1-5
                period_desc,
                str(YEAR_TO_DATE_CODE[year]),
                g(f"BIRTHS{year}"),
                g(f"DEATHS{year}"),
                g(f"NATURALCHG{year}"),               # renamed to NATURALINC
                g(f"INTERNATIONALMIG{year}"),
                g(f"DOMESTICMIG{year}"),
                g(f"NETMIG{year}"),
                g(f"RESIDUAL{year}"),
                rbirth,
                rdeath,
                rnaturalinc,                          # renamed to RNATURALINC
                rinternationalmig,
                rdomesticmig,
                rnetmig,
                state,
                county,
            ])
    return out


def rows_to_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    return buf.getvalue()


def upload_csv(s3, rows, s3_key):
    csv_content = rows_to_csv(rows)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=csv_content.encode("utf-8"),
        ContentType="text/csv",
    )
    return len(rows) - 1  # exclude header


def lambda_handler(event, context):
    s3 = boto3.client("s3", region_name=REGION)
    results = {}

    try:
        flat_rows = fetch_flat_file()
    except Exception as e:
        print(f"FATAL: Could not fetch flat file: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    # --- Population ---
    try:
        pop_rows = build_population_rows(flat_rows)
        count = upload_csv(s3, pop_rows, f"{S3_PREFIX}/pep_county_2020_2024.csv")
        print(f"Population: saved {count} rows")
        results["population"] = {"status": "success", "rows": count}
    except Exception as e:
        print(f"Population ERROR: {e}")
        results["population"] = {"status": "error", "error": str(e)}

    # --- Components ---
    try:
        comp_rows = build_components_rows(flat_rows)
        count = upload_csv(s3, comp_rows, f"{S3_PREFIX}/pep_county_components_2020_2024.csv")
        print(f"Components: saved {count} rows")
        results["components"] = {"status": "success", "rows": count}
    except Exception as e:
        print(f"Components ERROR: {e}")
        results["components"] = {"status": "error", "error": str(e)}

    any_error = any(v["status"] == "error" for v in results.values())
    return {
        "statusCode": 500 if any_error else 200,
        "body": json.dumps(results),
    }
