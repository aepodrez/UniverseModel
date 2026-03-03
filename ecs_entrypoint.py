import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import boto3

import universe


def _norm_prefix(value: str | None, default: str) -> str:
    raw = (value or default).strip("/")
    return raw or default.strip("/")


def _load_ecs_task_metadata() -> dict:
    base_uri = os.getenv("ECS_CONTAINER_METADATA_URI_V4", "").strip() or os.getenv(
        "ECS_CONTAINER_METADATA_URI", ""
    ).strip()
    if not base_uri:
        return {}
    try:
        with urllib.request.urlopen(f"{base_uri}/task", timeout=1.5) as response:
            payload = response.read().decode("utf-8")
        metadata = json.loads(payload)
        if isinstance(metadata, dict):
            return metadata
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    return {}


def _task_id_from_arn(task_arn: str | None) -> str | None:
    if not task_arn or "/" not in task_arn:
        return None
    return task_arn.rsplit("/", 1)[-1]


def run() -> dict:
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET env var is required")

    s3_prefix = _norm_prefix(os.getenv("S3_PREFIX"), "universe/")
    output_key = os.getenv("OUTPUT_KEY") or f"{s3_prefix}/universe.csv"
    mirrored_key = os.getenv("DATA_INGRESS_UNIVERSE_KEY") or "data-ingress/Static/universe.csv"
    execution_id = os.getenv("EXECUTION_ID", "").strip() or None
    state_name = os.getenv("STEP_FUNCTION_STATE_NAME", "").strip() or None
    metadata = _load_ecs_task_metadata()
    task_arn = metadata.get("TaskARN") if isinstance(metadata.get("TaskARN"), str) else None
    task_id = _task_id_from_arn(task_arn)
    context = {
        "event": "task_context",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
        "state_name": state_name,
        "ecs_task_arn": task_arn,
        "ecs_task_id": task_id,
    }
    print(json.dumps(context), flush=True)

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

    execution_name = execution_id.rsplit(":", 1)[-1] if execution_id and ":" in execution_id else (execution_id or "no-execution")
    task_segment = task_id or "no-task"
    log_key = f"{s3_prefix}/logs/{execution_name}/{task_segment}/run.json"

    result = {
        "statusCode": 200,
        "s3_output_path": output_key,
        "row_count": len(tickers),
        "mirrored_universe_path": mirrored_key,
        "execution_id": execution_id,
        "state_name": state_name,
        "task_id": task_id,
        "task_arn": task_arn,
        "task_log_path": log_key,
    }
    s3.put_object(
        Bucket=bucket,
        Key=log_key,
        Body=json.dumps(result, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    run()
