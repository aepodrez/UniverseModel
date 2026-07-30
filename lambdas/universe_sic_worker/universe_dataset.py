"""Immutable publication, integrity checks, and AI-assisted DQ for universe.csv."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError


DATASET = "universe"
CONTRACT_VERSION = 1
REQUIRED_COLUMNS = ["ticker", "cik", "sic", "naics", "naics_tier"]
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")
_CIK_RE = re.compile(r"^\d{10}$")
_SIC_RE = re.compile(r"^\d{4}$")
_NAICS_RE = re.compile(r"^\d{6}$")
_OBSERVED_NAICS_TIERS = {"exact_weighted", "exact_bridge"}
_ALLOWED_NAICS_TIERS = _OBSERVED_NAICS_TIERS | {"unresolved"}


class UniverseQualityError(RuntimeError):
    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _root(prefix: str, run_id: str) -> str:
    return f"{prefix.rstrip('/')}/_runs/{DATASET}/runs/{run_id}"


def current_key(prefix: str) -> str:
    return f"{prefix.rstrip('/')}/_runs/{DATASET}/current.json"


def _try_get(s3, bucket: str, key: str):
    try:
        return s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NoSuchBucket"):
            return None
        raise


def load_current_universe(s3, bucket: str, prefix: str = "universe") -> dict | None:
    """Load one verified current snapshot and its pointer ETag."""
    pointer_obj = _try_get(s3, bucket, current_key(prefix))
    if pointer_obj is None:
        return None
    pointer_bytes = pointer_obj["Body"].read()
    pointer = json.loads(pointer_bytes)
    manifest_obj = s3.get_object(Bucket=bucket, Key=pointer["manifest_key"])
    manifest_bytes = manifest_obj["Body"].read()
    if pointer.get("manifest_sha256") and _sha256(manifest_bytes) != pointer["manifest_sha256"]:
        raise RuntimeError("Universe manifest failed SHA-256 validation")
    manifest = json.loads(manifest_bytes)
    if (manifest.get("status") != "complete" or manifest.get("dataset") != DATASET
            or manifest.get("run_id") != pointer.get("run_id")):
        raise RuntimeError("Universe current pointer does not reference a complete run")
    output = manifest["output"]
    data_obj = s3.get_object(Bucket=bucket, Key=output["key"])
    data = data_obj["Body"].read()
    if len(data) != output["size"] or _sha256(data) != output["sha256"]:
        raise RuntimeError("Universe CSV failed SHA-256 validation")
    return {"data": data, "run_id": manifest["run_id"], "quality": manifest.get("quality"),
            "pointer_etag": pointer_obj.get("ETag")}


def parse_csv(data: bytes) -> list[dict[str, str]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(data.decode("utf-8")))]


def _profile(rows: list[dict]) -> dict:
    columns = list(rows[0]) if rows else []
    null_counts = {column: sum(not (row.get(column) or "").strip() for row in rows)
                   for column in columns}
    tiers: dict[str, int] = {}
    for row in rows:
        tier = (row.get("naics_tier") or "").strip() or "missing"
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "rows": len(rows),
        "columns": columns,
        "null_counts": null_counts,
        "null_ratios": {key: round(value / max(len(rows), 1), 8)
                        for key, value in null_counts.items()},
        "unique_tickers": len({row.get("ticker") for row in rows}),
        "unique_ciks": len({row.get("cik") for row in rows}),
        "naics_coverage": round(
            sum(bool((row.get("naics") or "").strip()) for row in rows) / max(len(rows), 1), 8
        ),
        "naics_tiers": tiers,
    }


def _pseudonym(column: str, value: Any) -> str:
    digest = hashlib.sha256(f"universe\0{column}\0{value}".encode()).hexdigest()
    return f"id:{digest[:16]}"


def _sanitize(row: dict) -> dict:
    return {
        column: (_pseudonym(column, value) if column in ("ticker", "cik") else value)
        for column, value in row.items()
    }


def _findings(rows: list[dict], profile: dict, previous_profile: dict | None) -> tuple[list[dict], list[tuple[str, int, dict]]]:
    findings: list[dict] = []
    evidence: list[tuple[str, int, dict]] = []
    missing = sorted(set(REQUIRED_COLUMNS) - set(profile["columns"]))
    if missing:
        findings.append({"severity": "error", "code": "missing_columns", "details": missing})
    if profile["rows"] < int(os.getenv("UNIVERSE_DQ_MIN_ROWS", "1000")):
        findings.append({"severity": "error", "code": "row_count_too_small",
                         "details": {"rows": profile["rows"]}})
    if profile["unique_tickers"] != profile["rows"]:
        findings.append({"severity": "error", "code": "duplicate_ticker",
                         "details": {"duplicates": profile["rows"] - profile["unique_tickers"]}})
    invalid = {"ticker": 0, "cik": 0, "sic": 0, "naics": 0}
    invalid_tier = inconsistent_tier = 0
    for index, row in enumerate(rows):
        row_invalid = []
        ticker, cik, sic, naics = (
            (row.get(key) or "").strip() for key in ("ticker", "cik", "sic", "naics")
        )
        if not _TICKER_RE.fullmatch(ticker): row_invalid.append("ticker")
        if not _CIK_RE.fullmatch(cik): row_invalid.append("cik")
        if not _SIC_RE.fullmatch(sic): row_invalid.append("sic")
        if naics and not _NAICS_RE.fullmatch(naics): row_invalid.append("naics")
        tier = (row.get("naics_tier") or "").strip()
        if tier not in _ALLOWED_NAICS_TIERS:
            invalid_tier += 1
            row_invalid.append("naics_tier")
        if (tier in _OBSERVED_NAICS_TIERS) != bool(naics):
            inconsistent_tier += 1
            row_invalid.append("naics_tier_consistency")
        for column in row_invalid:
            if column in invalid:
                invalid[column] += 1
        if row_invalid and len(evidence) < 12:
            evidence.append(("invalid_" + "_".join(row_invalid), index, row))
    for column, count in invalid.items():
        if count:
            findings.append({"severity": "error", "code": f"invalid_{column}",
                             "details": {"rows": count}})
    if invalid_tier:
        findings.append({"severity": "error", "code": "invalid_naics_tier",
                         "details": {"rows": invalid_tier}})
    if inconsistent_tier:
        findings.append({"severity": "error", "code": "inconsistent_naics_tier",
                         "details": {"rows": inconsistent_tier}})
    minimum_observed_coverage = float(
        os.getenv("UNIVERSE_DQ_MIN_OBSERVED_NAICS_COVERAGE", "0.70")
    )
    if profile["naics_coverage"] < minimum_observed_coverage:
        findings.append({
            "severity": "error", "code": "observed_naics_coverage_too_low",
            "details": {"coverage": profile["naics_coverage"],
                        "minimum": minimum_observed_coverage},
        })
    if previous_profile and previous_profile.get("rows"):
        drop = (previous_profile["rows"] - profile["rows"]) / previous_profile["rows"]
        if drop > float(os.getenv("UNIVERSE_DQ_MAX_ROW_DROP", "0.15")):
            findings.append({"severity": "error", "code": "row_count_drop",
                             "details": {"previous": previous_profile["rows"],
                                         "current": profile["rows"], "fraction": round(drop, 6)}})
        coverage_drop = previous_profile.get("naics_coverage", 0) - profile["naics_coverage"]
        if coverage_drop > float(os.getenv("UNIVERSE_DQ_MAX_NAICS_DROP", "0.05")):
            findings.append({"severity": "error", "code": "naics_coverage_drop",
                             "details": {"previous": previous_profile.get("naics_coverage"),
                                         "current": profile["naics_coverage"]}})
    return findings, evidence


def _row_evidence(rows: list[dict], previous_rows: list[dict], violations: list[tuple[str, int, dict]]) -> list[dict]:
    limit = int(os.getenv("DQ_EVIDENCE_ROWS", "48"))
    selected: list[tuple[str, str, int, dict]] = []
    count = min(16, len(rows), limit)
    positions = {((len(rows) - 1) * i // max(count - 1, 1)) for i in range(count)}
    selected.extend(("systematic_sample", "evenly_spaced_across_file", i, rows[i]) for i in sorted(positions))
    selected.extend(("rule_violation", reason, index, row) for reason, index, row in violations)

    if previous_rows:
        old = {row.get("ticker"): row for row in previous_rows}
        new = {row.get("ticker"): row for row in rows}
        for ticker in sorted(new.keys() - old.keys())[:6]:
            selected.append(("membership_change", "added_ticker", rows.index(new[ticker]), new[ticker]))
        for ticker in sorted(old.keys() - new.keys())[:6]:
            selected.append(("membership_change", "removed_ticker", -1, old[ticker]))
        for ticker in sorted(old.keys() & new.keys()):
            changed = [column for column in ("sic", "naics", "naics_tier")
                       if old[ticker].get(column) != new[ticker].get(column)]
            if changed:
                selected.append(("classification_change", "changed_" + "_".join(changed),
                                 rows.index(new[ticker]), new[ticker]))
            if sum(kind == "classification_change" for kind, *_ in selected) >= 8:
                break

    evidence = []
    seen = set()
    for kind, reason, row_index, row in selected:
        fingerprint = (kind, reason, json.dumps(row, sort_keys=True))
        if fingerprint in seen or len(evidence) >= limit:
            continue
        seen.add(fingerprint)
        evidence.append({"id": f"universe.csv:E{len(evidence)+1:03d}", "file": "universe.csv",
                         "kind": kind, "reason": reason, "row_index": row_index,
                         "values": _sanitize(row)})
    return evidence


_AI_SCHEMA = {"name": "universe_quality_review", "strict": True, "schema": {
    "type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["pass", "warn", "fail"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string"},
        "anomalies": {"type": "array", "items": {"type": "object", "properties": {
            "code": {"type": "string"}, "evidence": {"type": "string"},
            "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
            "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        }, "required": ["code", "evidence", "severity", "evidence_ids"],
            "additionalProperties": False}},
    }, "required": ["verdict", "confidence", "summary", "anomalies"],
    "additionalProperties": False}}


def _ai_review(profile: dict, previous_profile: dict | None, evidence: list[dict]) -> dict:
    mode = os.getenv("DQ_AI_MODE", "advisory").lower()
    key = os.getenv("OPENROUTER_API_KEY", "")
    if mode == "off": return {"status": "disabled", "mode": mode}
    if not key: return {"status": "unavailable", "mode": mode, "error": "missing_api_key"}
    model = os.getenv("OPENROUTER_DQ_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    facts = {"profile": profile, "previous_profile": previous_profile, "row_evidence": evidence}
    prompt = ("Conservatively review a US-listed common-stock universe. Detect membership, "
              "identifier, SIC/NAICS mapping, coverage, or schema anomalies. Identifiers are "
              "stable pseudonyms. NAICS is published only for exact SIC4 matches in source "
              "crosswalks; unresolved values must remain blank rather than be inferred from "
              "broader SIC groups. Every anomaly must cite supplied evidence IDs; invent nothing.\n\n"
              + json.dumps(facts, sort_keys=True, separators=(",", ":")))
    body = _json_bytes({"model": model, "temperature": 0,
        "max_tokens": int(os.getenv("DQ_AI_MAX_TOKENS", "2000")),
        "reasoning": {"effort": "none", "exclude": True},
        "provider": {"require_parameters": True},
        "plugins": [{"id": "response-healing"}],
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": _AI_SCHEMA}})
    request = Request("https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Title": "Euclidean Universe Data Quality"})
    try:
        with urlopen(request, timeout=int(os.getenv("DQ_AI_TIMEOUT_SECONDS", "45"))) as response:
            payload = json.load(response)
        content = payload["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenRouter returned no structured response content")
        review = json.loads(content)
        valid = {item["id"] for item in evidence}
        invalid = sorted({eid for item in review.get("anomalies", [])
                          for eid in item.get("evidence_ids", []) if eid not in valid})
        if invalid: raise ValueError(f"AI cited unknown evidence IDs: {invalid}")
        return {"status": "complete", "mode": mode, "model": payload.get("model", model),
                "evidence": evidence, "review": review}
    except Exception as exc:
        return {"status": "unavailable", "mode": mode,
                "error": f"{type(exc).__name__}: {exc}"[:500]}


def evaluate_universe(data: bytes, previous_data: bytes | None, previous_quality: dict | None) -> dict:
    rows = parse_csv(data)
    previous_rows = parse_csv(previous_data) if previous_data else []
    profile = _profile(rows)
    previous_profile = (previous_quality or {}).get("profile")
    findings, violations = _findings(rows, profile, previous_profile)
    evidence = _row_evidence(rows, previous_rows, violations)
    ai = _ai_review(profile, previous_profile, evidence)
    report = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "status": "fail" if findings else "pass", "profile": profile,
              "deterministic_findings": findings, "ai": ai}
    if findings:
        raise UniverseQualityError("Universe deterministic quality checks failed", report)
    if ai.get("mode") == "enforce":
        if ai.get("status") != "complete":
            raise UniverseQualityError("Required universe AI review unavailable", report)
        review = ai["review"]
        if review["verdict"] == "fail" and review["confidence"] >= float(os.getenv("DQ_AI_FAIL_CONFIDENCE", "0.90")):
            report["status"] = "fail"
            raise UniverseQualityError("Universe AI quality review rejected publication", report)
    if ai.get("status") == "unavailable" or (
        ai.get("status") == "complete" and (ai.get("review") or {}).get("verdict") != "pass"
    ):
        report["status"] = "warn"
    return report


def publish_universe(
    s3, bucket: str, prefix: str, compatibility_key: str, run_id: str, data: bytes,
    quality: dict, expected_current_etag: str | None,
) -> dict:
    """Seal a new universe run and CAS-promote it, then refresh the legacy alias."""
    root = _root(prefix, run_id)
    output_key = f"{root}/outputs/universe.csv"
    s3.put_object(Bucket=bucket, Key=output_key, Body=data, ContentType="text/csv",
                  IfNoneMatch="*")
    output = {"name": "universe.csv", "key": output_key, "size": len(data),
              "sha256": _sha256(data)}
    manifest = {"contract_version": CONTRACT_VERSION, "dataset": DATASET, "run_id": run_id,
                "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
                "output": output, "quality": quality}
    manifest_bytes = _json_bytes(manifest)
    manifest_key = f"{root}/manifest.json"
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=manifest_bytes,
                  ContentType="application/json", IfNoneMatch="*")
    pointer = {"contract_version": CONTRACT_VERSION, "dataset": DATASET, "run_id": run_id,
               "manifest_key": manifest_key, "manifest_sha256": _sha256(manifest_bytes)}
    kwargs = {"IfMatch": expected_current_etag} if expected_current_etag else {"IfNoneMatch": "*"}
    try:
        s3.put_object(Bucket=bucket, Key=current_key(prefix), Body=_json_bytes(pointer),
                      ContentType="application/json", **kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("PreconditionFailed", "412"):
            raise RuntimeError("Universe changed concurrently; refusing stale-base publication") from exc
        raise
    s3.put_object(Bucket=bucket, Key=compatibility_key, Body=data, ContentType="text/csv")
    return manifest
