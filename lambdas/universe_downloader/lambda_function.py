"""Universe downloader — step 1 of 3.

Downloads company_tickers_exchange.json from SEC EDGAR, filters to major US
exchanges, loads the existing SIC cache from S3, splits CIKs into two chunks,
writes a work manifest to S3, then asynchronously invokes universe_sic_worker
with chunk_index=0 to begin SIC enrichment.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from universe_reader import load_current_universe

log = logging.getLogger()
log.setLevel(logging.INFO)

S3_BUCKET              = os.environ["S3_BUCKET"]
UNIVERSE_KEY           = os.environ.get("UNIVERSE_KEY", "data-ingress/Static/universe.csv")
UNIVERSE_PREFIX        = os.environ.get("UNIVERSE_PREFIX", "universe")
MANIFEST_PREFIX        = os.environ.get("MANIFEST_PREFIX", "universe/work")
SIC_UPDATES_PREFIX     = os.environ.get("SIC_UPDATES_PREFIX", "universe/pending_sic_updates")
SIC_WORKER_FUNCTION    = os.environ["SIC_WORKER_FUNCTION_NAME"]
EDGAR_IDENTITY         = os.environ.get("EDGAR_IDENTITY", "EuclideanResearch contact@example.com")

US_EXCHANGES = frozenset({"Nasdaq", "NYSE", "NYSE Arca", "NYSE American", "Cboe BZX", "BATS"})

MAX_RETRIES     = 3
RETRY_BACKOFF_S = 2.0

s3      = boto3.client("s3")
lambda_ = boto3.client("lambda")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": EDGAR_IDENTITY, "Accept-Encoding": "gzip"},
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode())
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_S * attempt)


def _fetch_all_tickers() -> list[dict]:
    log.info("Downloading company_tickers_exchange.json from SEC EDGAR")
    data = _get_json("https://www.sec.gov/files/company_tickers_exchange.json")
    idx = {name: i for i, name in enumerate(data["fields"])}
    tickers = [
        {
            "cik":      str(row[idx["cik"]]).zfill(10),
            "name":     row[idx["name"]],
            "ticker":   row[idx["ticker"]],
            "exchange": row[idx["exchange"]],
        }
        for row in data["data"]
    ]
    log.info("Total SEC-registered tickers: %d", len(tickers))
    return tickers


def _filter_by_exchange(tickers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for t in tickers:
        if t["exchange"] in US_EXCHANGES and t["ticker"] not in seen:
            seen.add(t["ticker"])
            out.append(t)
    log.info("Tickers on major US exchanges: %d", len(out))
    return out


def _load_existing_sic_cache() -> tuple[dict[str, str], str | None, str | None]:
    try:
        current = load_current_universe(s3, S3_BUCKET, UNIVERSE_PREFIX)
        if current:
            data = current["data"]
            base_run_id = current["run_id"]
            base_pointer_etag = current["pointer_etag"]
            log.info("Loaded verified universe run %s", base_run_id)
        else:
            # One-time migration path before the first immutable run is published.
            obj = s3.get_object(Bucket=S3_BUCKET, Key=UNIVERSE_KEY)
            data = obj["Body"].read()
            base_run_id = base_pointer_etag = None
            log.info("Loaded legacy universe compatibility key")
        text = data.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        cache: dict[str, str] = {}
        for row in reader:
            cik = (row.get("cik") or "").strip().zfill(10)
            sic = (row.get("sic") or "").strip()
            if cik and sic:
                cache[cik] = sic
        log.info("Loaded existing SIC cache: %d entries", len(cache))
        return cache, base_run_id, base_pointer_etag
    except (s3.exceptions.NoSuchKey, ClientError) as exc:
        if isinstance(exc, ClientError) and exc.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise
        log.info("No existing universe.csv found — cold start")
        return {}, None, None
    except Exception as e:
        log.error("Failed to load verified universe.csv: %s", e)
        raise


def _load_pending_sic_updates() -> tuple[dict[str, str], list[str]]:
    updates: dict[str, str] = {}
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{SIC_UPDATES_PREFIX.rstrip('/')}/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            payload = json.loads(s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read())
            for cik, sic in payload.get("updates", {}).items():
                updates[str(cik).zfill(10)] = str(sic).zfill(4)
            keys.append(key)
    log.info("Loaded %d pending SIC corrections from %d objects", len(updates), len(keys))
    return updates, keys


def lambda_handler(event, context):
    event = event or {}
    run_id = event.get("run_id") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    all_tickers   = _fetch_all_tickers()
    filtered      = _filter_by_exchange(all_tickers)
    existing_sic, base_run_id, base_pointer_etag = _load_existing_sic_cache()
    pending_sic, pending_sic_keys = _load_pending_sic_updates()
    existing_sic.update(pending_sic)

    ciks = [t["cik"] for t in filtered]
    mid  = len(ciks) // 2
    chunk_0 = ciks[:mid]
    chunk_1 = ciks[mid:]
    log.info("Split: chunk_0=%d CIKs, chunk_1=%d CIKs", len(chunk_0), len(chunk_1))

    manifest = {
        "all_tickers":  filtered,   # [{cik, ticker, name, exchange}]
        "existing_sic": existing_sic,
        "chunk_0":      chunk_0,
        "chunk_1":      chunk_1,
        "run_id":       run_id,
        "base_run_id":  base_run_id,
        "base_pointer_etag": base_pointer_etag,
        "pending_sic_keys": pending_sic_keys,
    }
    manifest_key = f"{MANIFEST_PREFIX}/runs/{run_id}/manifest.json"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=manifest_key,
        Body=json.dumps(manifest, separators=(",", ":")),
        ContentType="application/json",
    )
    log.info("Wrote manifest to s3://%s/%s", S3_BUCKET, manifest_key)

    lambda_.invoke(
        FunctionName=SIC_WORKER_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps({"chunk_index": 0, "run_id": run_id}),
    )
    log.info("Invoked %s with chunk_index=0", SIC_WORKER_FUNCTION)

    return {
        "status":        "ok",
        "total_tickers": len(filtered),
        "chunk_0_size":  len(chunk_0),
        "chunk_1_size":  len(chunk_1),
        "cache_size":    len(existing_sic),
        "run_id":        run_id,
    }
