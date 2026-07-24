"""Verified reader for the immutable UniverseModel dataset."""
from __future__ import annotations

import hashlib
import json

from botocore.exceptions import ClientError


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_current_universe(s3, bucket: str, prefix: str = "universe") -> dict | None:
    pointer_key = f"{prefix.rstrip('/')}/_runs/universe/current.json"
    try:
        pointer_obj = s3.get_object(Bucket=bucket, Key=pointer_key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NoSuchBucket"):
            return None
        raise
    pointer = json.loads(pointer_obj["Body"].read())
    manifest_obj = s3.get_object(Bucket=bucket, Key=pointer["manifest_key"])
    manifest_bytes = manifest_obj["Body"].read()
    if pointer.get("manifest_sha256") and _sha256(manifest_bytes) != pointer["manifest_sha256"]:
        raise RuntimeError("Universe manifest failed SHA-256 validation")
    manifest = json.loads(manifest_bytes)
    if manifest.get("status") != "complete" or manifest.get("run_id") != pointer.get("run_id"):
        raise RuntimeError("Universe pointer does not reference a complete run")
    output = manifest["output"]
    data = s3.get_object(Bucket=bucket, Key=output["key"])["Body"].read()
    if len(data) != output["size"] or _sha256(data) != output["sha256"]:
        raise RuntimeError("Universe CSV failed SHA-256 validation")
    return {"data": data, "run_id": manifest["run_id"], "quality": manifest.get("quality"),
            "pointer_etag": pointer_obj.get("ETag")}
