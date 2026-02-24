import json
import os
from pathlib import Path

import boto3

import universe


def _norm_prefix(value: str | None, default: str) -> str:
    raw = (value or default).strip("/")
    return raw or default.strip("/")


def run() -> dict:
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET env var is required")

    s3_prefix = _norm_prefix(os.getenv("S3_PREFIX"), "universe/")
    output_key = os.getenv("OUTPUT_KEY") or f"{s3_prefix}/universe.csv"
    mirrored_key = os.getenv("DATA_INGRESS_UNIVERSE_KEY") or "data-ingress/Static/universe.csv"

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

    result = {
        "statusCode": 200,
        "s3_output_path": output_key,
        "row_count": len(tickers),
        "mirrored_universe_path": mirrored_key,
    }
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    run()
