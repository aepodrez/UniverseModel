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
import os
import re
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

# SIC codes to drop — non-operating / pooled-investment / blank-check vehicles.
#   6726  Investment Offices, NEC
#   6770  Blank Checks
EXCLUDE_SIC = frozenset({"6726", "6770"})

# Non-common suffixes used in listed ticker symbols.
_NON_COMMON_HYPHEN_SUFFIXES = frozenset(
    {"W", "WS", "WT", "R", "RT", "RI", "U", "UN", "UT", "P", "PR", "PFD"}
)
_NON_COMMON_HYPHEN_SUFFIX_RE = re.compile(r"^P[A-Z0-9]{1,3}$")

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
    """Drop non-operating entities and non-common ticker classes."""

    ticker_set = {t["ticker"] for t in tickers}

    def is_non_common_ticker_symbol(symbol: str) -> bool:
        ticker = (symbol or "").upper().strip()
        if not ticker:
            return True

        # Hyphenated forms frequently encode non-common classes:
        # warrants (-WT/-W), rights (-R/-RI), units (-U/-UN), preferred (-PA/-PB).
        if "-" in ticker:
            suffix = ticker.rsplit("-", 1)[-1]
            if suffix in _NON_COMMON_HYPHEN_SUFFIXES:
                return True
            if _NON_COMMON_HYPHEN_SUFFIX_RE.match(suffix):
                return True

        # 5-char Nasdaq convention for derivatives when base common exists.
        # Examples: ABVEW, AACBR, AACBU.
        if len(ticker) == 5 and ticker[-1] in {"W", "R", "U", "Z"}:
            if ticker[:-1] in ticker_set:
                return True

        # 6-char derivative-style suffixes when base common exists.
        if len(ticker) == 6 and ticker[-2:] in {"WS", "WT", "RW", "RT", "RU"}:
            if ticker[:-2] in ticker_set:
                return True

        return False

    before = len(tickers)
    excluded_missing_or_sic = 0
    excluded_non_common_symbol = 0
    out: list[dict] = []

    for t in tickers:
        sic = t.get("sic")
        if not sic or sic in EXCLUDE_SIC:
            excluded_missing_or_sic += 1
            continue
        if is_non_common_ticker_symbol(t.get("ticker", "")):
            excluded_non_common_symbol += 1
            continue
        out.append(t)

    print(f"  Excluded {excluded_missing_or_sic:,} missing-SIC / excluded-SIC entries")
    print(f"  Excluded {excluded_non_common_symbol:,} non-common ticker symbols")
    print(f"  Total excluded: {before - len(out):,}")
    print(f"  {len(out):,} common stocks remaining")
    return out


def _fetch_alpaca_assets(api_key: str, api_secret: str, base_url: str) -> dict[str, bool]:
    """Return {symbol: shortable} for all active Alpaca-tradable assets."""
    url = f"{base_url.rstrip('/')}/v2/assets?status=active"
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        assets = json.loads(resp.read().decode())
    return {a["symbol"]: bool(a.get("shortable", False)) for a in assets}


def enrich_alpaca_shortability(tickers: list[dict]) -> None:
    """Add a shortable field to each ticker using Alpaca asset data.

    Requires ALPACA_API_KEY and ALPACA_API_SECRET env vars.
    Falls back to shortable=True for all tickers if credentials are absent
    or the API call fails, so the pipeline is not blocked.
    """
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")

    if not api_key or not api_secret:
        print("  ALPACA_API_KEY / ALPACA_API_SECRET not set — marking all tickers shortable")
        for t in tickers:
            t["shortable"] = True
        return

    print(f"Fetching Alpaca asset shortability from {base_url} …")
    try:
        shortable_map = _fetch_alpaca_assets(api_key, api_secret, base_url)
    except Exception as exc:
        print(f"  Warning: Alpaca assets fetch failed ({exc}) — marking all tickers shortable")
        for t in tickers:
            t["shortable"] = True
        return

    shortable_count = 0
    for t in tickers:
        # CRSP uses '-' for dual-class shares (e.g. BRK-B); Alpaca uses '.' (BRK.B)
        alpaca_sym = t["ticker"].replace("-", ".")
        shortable = shortable_map.get(alpaca_sym, shortable_map.get(t["ticker"], False))
        t["shortable"] = shortable
        if shortable:
            shortable_count += 1

    not_shortable = len(tickers) - shortable_count
    print(f"  {shortable_count:,} shortable, {not_shortable:,} not shortable on Alpaca")


def save(tickers: list[dict], path: str) -> None:
    """Write the final universe to CSV (ticker, cik, sic, shortable)."""
    tickers.sort(key=lambda t: t["ticker"])
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ticker", "cik", "sic", "shortable"])
        writer.writeheader()
        for t in tickers:
            writer.writerow({
                "ticker": t["ticker"],
                "cik": t["cik"],
                "sic": t["sic"],
                "shortable": t.get("shortable", True),
            })
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
    enrich_alpaca_shortability(tickers)
    save(tickers, args.output)


if __name__ == "__main__":
    main()
