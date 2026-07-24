"""Universe SIC worker — steps 2 and 3 of 3.

Invoked twice sequentially (by universe_downloader then by itself):

  chunk_index=0: enriches SIC for first half of CIKs, writes result_0.json,
                 then self-invokes with chunk_index=1.

  chunk_index=1: enriches SIC for second half, merges both results, applies
                 common-stock filtering, validates the dataset, and atomically
                 promotes an immutable run before refreshing the legacy alias.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

from sic_naics_crosswalk import sic4_to_naics6
from universe_dataset import evaluate_universe, load_current_universe, publish_universe

log = logging.getLogger()
log.setLevel(logging.INFO)

S3_BUCKET       = os.environ["S3_BUCKET"]
UNIVERSE_KEY    = os.environ.get("UNIVERSE_KEY", "data-ingress/Static/universe.csv")
UNIVERSE_PREFIX = os.environ.get("UNIVERSE_PREFIX", "universe")
MANIFEST_PREFIX = os.environ.get("MANIFEST_PREFIX", "universe/work")
EDGAR_IDENTITY  = os.environ.get("EDGAR_IDENTITY", "EuclideanResearch contact@example.com")
SELF_FUNCTION   = os.environ["AWS_LAMBDA_FUNCTION_NAME"]

EXCLUDE_SIC = frozenset({"6726", "6770"})
_NON_COMMON_HYPHEN_SUFFIXES = frozenset(
    {"W", "WS", "WT", "R", "RT", "RI", "U", "UN", "UT", "P", "PR", "PFD"}
)
_NON_COMMON_HYPHEN_SUFFIX_RE = re.compile(r"^P[A-Z0-9]{1,3}$")

BATCH_SIZE      = 8
BATCH_PAUSE_S   = 1.0
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
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode())
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_S * attempt)


def _fetch_sic(cik: str) -> str:
    try:
        data = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        return str(data.get("sic", "") or "")
    except Exception:
        return ""


def _enrich_sic_chunk(ciks: list[str], existing_sic: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    to_fetch = [cik for cik in ciks if cik not in existing_sic]
    cached   = {cik: existing_sic[cik] for cik in ciks if cik in existing_sic}

    log.info("SIC enrichment: %d cached, %d to fetch", len(cached), len(to_fetch))

    fetched: dict[str, str] = {}
    for i in range(0, len(to_fetch), BATCH_SIZE):
        batch = to_fetch[i : i + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futs = {pool.submit(_fetch_sic, cik): cik for cik in batch}
            for fut in as_completed(futs):
                fetched[futs[fut]] = fut.result()
        if i % 500 < BATCH_SIZE or i + BATCH_SIZE >= len(to_fetch):
            log.info("  fetched %d / %d", min(i + BATCH_SIZE, len(to_fetch)), len(to_fetch))
        time.sleep(BATCH_PAUSE_S)

    result.update(cached)
    result.update(fetched)
    return result


def _is_non_common(symbol: str, ticker_set: set[str]) -> bool:
    ticker = (symbol or "").upper().strip()
    if not ticker:
        return True
    if "-" in ticker:
        suffix = ticker.rsplit("-", 1)[-1]
        if suffix in _NON_COMMON_HYPHEN_SUFFIXES:
            return True
        if _NON_COMMON_HYPHEN_SUFFIX_RE.match(suffix):
            return True
    if len(ticker) == 5 and ticker[-1] in {"W", "R", "U", "Z"} and ticker[:-1] in ticker_set:
        return True
    if len(ticker) == 6 and ticker[-2:] in {"WS", "WT", "RW", "RT", "RU"} and ticker[:-2] in ticker_set:
        return True
    return False


def _filter_common_stocks(tickers: list[dict]) -> list[dict]:
    ticker_set = {t["ticker"] for t in tickers}
    out = []
    excl_sic = excl_sym = 0
    for t in tickers:
        sic = t.get("sic", "")
        if not sic or sic in EXCLUDE_SIC:
            excl_sic += 1
            continue
        if _is_non_common(t.get("ticker", ""), ticker_set):
            excl_sym += 1
            continue
        out.append(t)
    log.info("filter_common_stocks: excluded %d SIC, %d symbol → %d remaining",
             excl_sic, excl_sym, len(out))
    return out


def _work_key(run_id: str, name: str) -> str:
    return f"{MANIFEST_PREFIX}/runs/{run_id}/{name}"


def _load_manifest(run_id: str) -> dict:
    obj = s3.get_object(Bucket=S3_BUCKET, Key=_work_key(run_id, "manifest.json"))
    return json.loads(obj["Body"].read())


def _load_result_0(run_id: str) -> dict[str, str]:
    obj = s3.get_object(Bucket=S3_BUCKET, Key=_work_key(run_id, "result_0.json"))
    return json.loads(obj["Body"].read())


def _write_universe_csv(tickers: list[dict]) -> bytes:
    tickers_sorted = sorted(tickers, key=lambda t: t["ticker"])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["ticker", "cik", "sic", "naics", "naics_tier"])
    writer.writeheader()
    for t in tickers_sorted:
        writer.writerow({
            "ticker": t["ticker"],
            "cik": t["cik"],
            "sic": t["sic"],
            "naics": t["naics"] or "",
            "naics_tier": t["naics_tier"],
        })
    return buf.getvalue().encode("utf-8")


def _handle_chunk_0(manifest: dict, run_id: str) -> dict:
    sic_result = _enrich_sic_chunk(manifest["chunk_0"], manifest["existing_sic"])

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=_work_key(run_id, "result_0.json"),
        Body=json.dumps(sic_result, separators=(",", ":")),
        ContentType="application/json",
    )
    log.info("Wrote result_0.json (%d entries)", len(sic_result))

    lambda_.invoke(
        FunctionName=SELF_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps({"chunk_index": 1, "run_id": run_id}),
    )
    log.info("Self-invoked with chunk_index=1")

    return {"chunk_index": 0, "count": len(sic_result)}


def _handle_chunk_1(manifest: dict, run_id: str) -> dict:
    result_0   = _load_result_0(run_id)
    sic_result = _enrich_sic_chunk(manifest["chunk_1"], manifest["existing_sic"])

    merged_sic = {**result_0, **sic_result}
    log.info("Merged SIC: %d total entries", len(merged_sic))

    enriched = []
    for t in manifest["all_tickers"]:
        cik = t["cik"]
        sic = merged_sic.get(cik, "")
        naics_result = sic4_to_naics6(sic) if sic else None
        naics = naics_result.naics6 if naics_result else None
        naics_tier = naics_result.tier if naics_result else "unresolved"
        enriched.append({
            "ticker": t["ticker"],
            "cik": cik,
            "sic": sic,
            "naics": naics,
            "naics_tier": naics_tier,
        })

    final = _filter_common_stocks(enriched)

    csv_bytes = _write_universe_csv(final)
    current = load_current_universe(s3, S3_BUCKET, UNIVERSE_PREFIX)
    current_run_id = current["run_id"] if current else None
    if current_run_id != manifest.get("base_run_id"):
        raise RuntimeError(
            f"Universe base changed during run: started={manifest.get('base_run_id')} "
            f"current={current_run_id}; refusing stale-base publication"
        )
    quality = evaluate_universe(
        csv_bytes,
        current["data"] if current else None,
        current.get("quality") if current else None,
    )
    published = publish_universe(
        s3, S3_BUCKET, UNIVERSE_PREFIX, UNIVERSE_KEY, run_id, csv_bytes, quality,
        current.get("pointer_etag") if current else None,
    )
    log.info("Published immutable universe run %s: %d rows, quality=%s",
             run_id, len(final), quality["status"])
    for key in manifest.get("pending_sic_keys", []):
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=key)
        except Exception as exc:
            log.warning("Published run but failed to remove applied SIC update %s: %s", key, exc)
    return {"chunk_index": 1, "row_count": len(final), "run_id": run_id,
            "manifest_status": published["status"], "quality_status": quality["status"]}


def lambda_handler(event, context):
    event = event or {}
    chunk_index = int(event.get("chunk_index", 0))
    run_id = str(event.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    log.info("universe_sic_worker starting: chunk_index=%d run_id=%s", chunk_index, run_id)

    manifest = _load_manifest(run_id)
    if manifest.get("run_id") != run_id:
        raise RuntimeError("Work manifest run_id mismatch")

    if chunk_index == 0:
        return _handle_chunk_0(manifest, run_id)
    else:
        return _handle_chunk_1(manifest, run_id)
