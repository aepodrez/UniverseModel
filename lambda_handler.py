import json
import os
from pathlib import Path

import boto3

import universe


def _norm_prefix(value: str | None, default: str) -> str:
    raw = (value or default).strip("/")
    return raw or default.strip("/")


def lambda_handler(event, context):
    event = event or {}
    bucket = event.get("s3_bucket") or os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("S3 bucket is required (event.s3_bucket or S3_BUCKET env)")

    s3_prefix = _norm_prefix(event.get("s3_prefix"), os.getenv("S3_PREFIX", "universe/"))
    output_key = event.get("output_key") or f"{s3_prefix}/universe.csv"
    mirrored_key = event.get("data_ingress_universe_key") or "data-ingress/Static/universe.csv"

    tickers = universe.fetch_all_tickers()
    tickers = universe.filter_by_exchange(tickers)
    universe.enrich_sic(tickers)
    tickers = universe.filter_common_stocks(tickers)

    local_path = Path("/tmp/universe.csv")
    universe.save(tickers, str(local_path))

    s3 = boto3.client("s3")
    s3.upload_file(str(local_path), bucket, output_key)
    if mirrored_key and mirrored_key != output_key:
        s3.upload_file(str(local_path), bucket, mirrored_key)

    return {
        "statusCode": 200,
        "s3_output_path": output_key,
        "row_count": len(tickers),
        "mirrored_universe_path": mirrored_key,
        "message": json.dumps({"bucket": bucket, "output_key": output_key}),
    }
