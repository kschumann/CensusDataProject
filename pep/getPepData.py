import json
import boto3
import urllib.request
import urllib.parse
'''Lambda Script that gets pep/population and pep/components from Census API and loads into S3'''


S3_BUCKET = "census-raw-data-417995075318-us-east-1-an"
S3_PREFIX = "pep"
REGION = "us-east-1"
# Add this mapping dict at module level
PERIOD_TO_DATE_CODE = {
    "1": "2",
    "2": "3",
    "3": "4",
    "4": "5",
    "5": "6",
    "6": "7",
    "7": "8",
    "8": "9",
    "9": "10",
    "10": "11"
}

def add_date_code(rows):
    """
    Insert DATE_CODE column after PERIOD_CODE by mapping PERIOD_CODE -> DATE_CODE.
    Rows without a mapping (unexpected PERIOD_CODE values) get DATE_CODE of empty string.
    """
    header = rows[0]
    period_idx = header.index("PERIOD_CODE")
    
    # Insert DATE_CODE header after PERIOD_CODE
    new_header = header[:period_idx+1] + ["DATE_CODE"] + header[period_idx+1:]
    new_rows = [new_header]
    
    for row in rows[1:]:
        period_val = str(row[period_idx])
        date_code = PERIOD_TO_DATE_CODE.get(period_val, "")
        new_row = row[:period_idx+1] + [date_code] + row[period_idx+1:]
        new_rows.append(new_row)
    
    return new_rows


def get_api_key():
    ssm = boto3.client("ssm", region_name=REGION)
    response = ssm.get_parameter(Name="/census/api_key", WithDecryption=True)
    return response["Parameter"]["Value"]

def fetch_pep_population(api_key):
    variables = "NAME,POP,DENSITY,STATE,DATE_CODE,DATE_DESC"
    base_url = "https://api.census.gov/data/2019/pep/population"
    params = urllib.parse.urlencode({
        "get": variables,
        "for": "county:*",
        "key": api_key
    })
    url = f"{base_url}?{params}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))

def fetch_pep_components(api_key):
    variables = (
        "NAME,STATE,PERIOD_CODE,PERIOD_DESC,"
        "BIRTHS,DEATHS,NATURALINC,"
        "INTERNATIONALMIG,DOMESTICMIG,NETMIG,"
        "RESIDUAL,"
        "RBIRTH,RDEATH,RNATURALINC,"
        "RINTERNATIONALMIG,RDOMESTICMIG,RNETMIG"
    )
    base_url = "https://api.census.gov/data/2019/pep/components"
    params = urllib.parse.urlencode({
        "get": variables,
        "for": "county:*",
        "key": api_key
    })
    url = f"{base_url}?{params}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))

def rows_to_csv(rows):
    lines = []
    for row in rows:
        escaped = []
        for field in row:
            field = str(field) if field is not None else ""
            if "," in field or '"' in field:
                field = '"' + field.replace('"', '""') + '"'
            escaped.append(field)
        lines.append(",".join(escaped))
    return "\n".join(lines)

def upload_csv(s3, rows, s3_key):
    csv_content = rows_to_csv(rows)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=csv_content.encode("utf-8"),
        ContentType="text/csv"
    )
    return len(rows) - 1

def lambda_handler(event, context):
    api_key = get_api_key()
    s3 = boto3.client("s3", region_name=REGION)
    results = {}

    # --- Population ---
    print("Fetching PEP population data...")
    try:
        rows = fetch_pep_population(api_key)
        count = upload_csv(s3, rows, f"{S3_PREFIX}/pep_county_2010_2019.csv")
        print(f"Population: saved {count} rows")
        results["population"] = {"status": "success", "rows": count}
    except Exception as e:
        print(f"Population ERROR: {str(e)}")
        results["population"] = {"status": "error", "error": str(e)}

    # --- Components ---
    print("Fetching PEP components data...")
    try:
        rows = fetch_pep_components(api_key)
        rows = add_date_code(rows)  
        count = upload_csv(s3, rows, f"{S3_PREFIX}/pep_county_components_2010_2019.csv")
        print(f"Components: saved {count} rows")
        results["components"] = {"status": "success", "rows": count}
    except Exception as e:
        print(f"Components ERROR: {str(e)}")
        results["components"] = {"status": "error", "error": str(e)}

    any_error = any(v["status"] == "error" for v in results.values())
    return {
        "statusCode": 500 if any_error else 200,
        "body": json.dumps(results)
    }
