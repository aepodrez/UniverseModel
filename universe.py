#!/usr/bin/env python3
"""
universe.py — Build a universe of US-exchange-traded common stocks.

Data sources
────────────
  1. SEC EDGAR  company_tickers_exchange.json   (bulk ticker → CIK + exchange)
  2. SEC EDGAR  submissions API                 (SIC code per CIK)

Pipeline
────────
  fetch all SEC-registered tickers
  → filter to major US exchanges (NYSE, Nasdaq, …)
  → fetch SIC codes from EDGAR submissions API
  → exclude non-operating entities (ETFs, investment cos, blank checks)
  → write universe.csv

Usage
─────
  python3 universe.py                   # writes universe.csv
  python3 universe.py -o my_univ.csv    # custom output path
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── configuration ────────────────────────────────────────────────────────────

# SEC EDGAR requires a User-Agent with contact info.
SEC_USER_AGENT = "UniverseBuilder research@example.com"

# Exchange names as they appear in SEC EDGAR data.
US_EXCHANGES = frozenset(
    {"Nasdaq", "NYSE", "NYSE Arca", "NYSE American", "Cboe BZX", "BATS"}
)

# SIC codes to drop — non-operating / pooled-investment vehicles.
#   6726  Investment Offices, NEC  (ETFs, closed-end funds, SPACs, blank checks)
EXCLUDE_SIC = frozenset({"6726"})

# SEC allows ≤10 req/s. We fire batches of 8 with a 1 s pause between them.
BATCH_SIZE = 8
BATCH_PAUSE_S = 1.0

MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

OUTPUT_DEFAULT = "universe.csv"

# ── helpers ──────────────────────────────────────────────────────────────────


def _get_json(url: str) -> dict:
    """GET *url* with the required SEC headers and return parsed JSON."""
    req = urllib.request.Request(
        url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip"}
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


# ── pipeline stages ─────────────────────────────────────────────────────────


def fetch_all_tickers() -> list[dict]:
    """Download every SEC-registered ticker with CIK, name, and exchange."""
    print("Downloading ticker list from SEC EDGAR …")
    data = _get_json("https://www.sec.gov/files/company_tickers_exchange.json")
    idx = {name: i for i, name in enumerate(data["fields"])}
    tickers = [
        {
            "cik": str(row[idx["cik"]]).zfill(10),
            "name": row[idx["name"]],
            "ticker": row[idx["ticker"]],
            "exchange": row[idx["exchange"]],
        }
        for row in data["data"]
    ]
    print(f"  {len(tickers):,} total SEC-registered tickers")
    return tickers


def filter_by_exchange(tickers: list[dict]) -> list[dict]:
    """Keep only tickers on major US exchanges (first occurrence wins)."""
    seen: set[str] = set()
    out: list[dict] = []
    for t in tickers:
        if t["exchange"] in US_EXCHANGES and t["ticker"] not in seen:
            seen.add(t["ticker"])
            out.append(t)
    print(f"  {len(out):,} tickers on major US exchanges")
    return out


def _fetch_sic(cik: str) -> str:
    """Return the SIC code for *cik*, or '' on failure."""
    try:
        data = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        return data.get("sic", "")
    except Exception:
        return ""


def enrich_sic(tickers: list[dict]) -> None:
    """Fetch SIC codes for every ticker via batched concurrent requests."""
    total = len(tickers)
    print(f"Fetching SIC codes for {total:,} tickers …")
    done = 0
    for i in range(0, total, BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futs = {pool.submit(_fetch_sic, t["cik"]): t for t in batch}
            for fut in as_completed(futs):
                futs[fut]["sic"] = fut.result()
        done = min(i + BATCH_SIZE, total)
        if done % 500 < BATCH_SIZE or done == total:
            print(f"  {done:,} / {total:,}")
        time.sleep(BATCH_PAUSE_S)


def filter_common_stocks(tickers: list[dict]) -> list[dict]:
    """Drop non-operating entities (ETFs, investment cos, blank checks)."""
    before = len(tickers)
    out = [t for t in tickers if t.get("sic") and t["sic"] not in EXCLUDE_SIC]
    print(f"  Excluded {before - len(out):,} non-operating / missing-SIC entries")
    print(f"  {len(out):,} common stocks remaining")
    return out


def save(tickers: list[dict], path: str) -> None:
    """Write the final universe to CSV (ticker, cik, sic)."""
    tickers.sort(key=lambda t: t["ticker"])
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ticker", "cik", "sic"])
        writer.writeheader()
        for t in tickers:
            writer.writerow({"ticker": t["ticker"], "cik": t["cik"], "sic": t["sic"]})
    print(f"\nWrote {len(tickers):,} rows → {path}")


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="Build US common-stock universe")
    ap.add_argument(
        "-o", "--output", default=OUTPUT_DEFAULT, help="Output CSV path (default: %(default)s)"
    )
    args = ap.parse_args()

    tickers = fetch_all_tickers()
    tickers = filter_by_exchange(tickers)
    enrich_sic(tickers)
    tickers = filter_common_stocks(tickers)
    save(tickers, args.output)


if __name__ == "__main__":
    main()

