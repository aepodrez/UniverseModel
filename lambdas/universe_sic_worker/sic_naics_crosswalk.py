"""
SIC (1987) to NAICS (2002) crosswalk with tiered fallback for codes absent
from the primary source tables.

Data sources (sic_naics_crosswalk_data/, bundled in this lambda's zip asset):
  - schaller_sic4_to_naics6.csv: Schaller & DeCelles weighted crosswalk
    (QCEW-1997-weighted, ICPSR E145101). Covers ~57% of SIC4 codes seen in
    our universe, but omits SIC divisions 10-19 (mining/construction) and
    scattered NEC/group-header codes.
  - census_bridge_naics6_sic6_pairs.csv: unweighted NAICS6<->SIC6 pairs
    extracted directly from the Census Bureau's "Bridge Between NAICS and
    SIC" source PDF. Covers the mining/construction gap the Schaller table
    lacks.

Resolution tiers, in order:
  1. Exact SIC4 match in the weighted Schaller table (establishment-weighted).
  2. Exact SIC4 match in the unweighted Census bridge table (occurrence-weighted).
  3. SIC4 not found as an exact code because it's a group-header/NEC code
     (e.g. 2860, 7370): roll up to the 3-digit industry-group prefix and take
     the establishment-weighted dominant NAICS across that prefix.
  4. SIC4 not found because it's a 2-digit-division placeholder used by
     SEC/EDGAR filers (e.g. 1000, 6500, ending in "00"): roll up to the
     2-digit division prefix.
  5. SIC4 == "0000" or nothing resolves at any tier: unresolved. Callers
     should not guess -- leave naics blank and let downstream fall back
     to whatever industry classification it already has (e.g. naicsh).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).resolve().parent / "sic_naics_crosswalk_data"
_SCHALLER_CSV = _DATA_DIR / "schaller_sic4_to_naics6.csv"
_BRIDGE_CSV = _DATA_DIR / "census_bridge_naics6_sic6_pairs.csv"

UNRESOLVED_SENTINELS = {"0000"}


@dataclass(frozen=True)
class CrosswalkResult:
    naics6: Optional[str]
    tier: str  # "exact_weighted" | "exact_bridge" | "rollup_group" | "rollup_division" | "unresolved"
    sic4_used: Optional[str]  # the SIC4 (or prefix) that actually produced the match


def _load_weighted_by_sic4() -> dict[str, list[tuple[str, float]]]:
    by_sic4: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with open(_SCHALLER_CSV, encoding="latin-1") as f:
        for row in csv.DictReader(f):
            sic4 = row["SIC4"].strip()
            naics6 = row["NAICS6"].strip()
            weight = float(row["Establishments"] or 0)
            by_sic4[sic4].append((naics6, weight))
    return by_sic4


def _load_bridge_by_sic4() -> dict[str, list[tuple[str, float]]]:
    by_sic4: dict[str, list[tuple[str, float]]] = defaultdict(list)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    with open(_BRIDGE_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sic4 = row["SIC6"].strip()[:4]
            naics6 = row["NAICS6"].strip()
            counts[(sic4, naics6)] += 1
    for (sic4, naics6), n in counts.items():
        by_sic4[sic4].append((naics6, float(n)))
    return by_sic4


def _dominant(pairs: list[tuple[str, float]]) -> str:
    totals: dict[str, float] = defaultdict(float)
    for naics6, weight in pairs:
        totals[naics6] += weight
    return max(totals.items(), key=lambda kv: (kv[1], kv[0]))[0]


class SicNaicsCrosswalk:
    def __init__(self) -> None:
        self._weighted = _load_weighted_by_sic4()
        self._bridge = _load_bridge_by_sic4()

        self._combined: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for sic4, pairs in self._weighted.items():
            self._combined[sic4].extend(pairs)
        for sic4, pairs in self._bridge.items():
            self._combined[sic4].extend(pairs)

    def lookup(self, sic4: str) -> CrosswalkResult:
        sic4 = (sic4 or "").strip().zfill(4)

        if sic4 in UNRESOLVED_SENTINELS:
            return CrosswalkResult(None, "unresolved", None)

        if sic4 in self._weighted:
            return CrosswalkResult(_dominant(self._weighted[sic4]), "exact_weighted", sic4)

        if sic4 in self._bridge:
            return CrosswalkResult(_dominant(self._bridge[sic4]), "exact_bridge", sic4)

        group_prefix = sic4[:3]
        group_pool = [
            pair
            for other_sic4, pairs in self._combined.items()
            if other_sic4[:3] == group_prefix
            for pair in pairs
        ]
        if group_pool:
            return CrosswalkResult(_dominant(group_pool), "rollup_group", group_prefix)

        division_prefix = sic4[:2]
        division_pool = [
            pair
            for other_sic4, pairs in self._combined.items()
            if other_sic4[:2] == division_prefix
            for pair in pairs
        ]
        if division_pool:
            return CrosswalkResult(_dominant(division_pool), "rollup_division", division_prefix)

        return CrosswalkResult(None, "unresolved", None)


_default_crosswalk: Optional[SicNaicsCrosswalk] = None


def get_default_crosswalk() -> SicNaicsCrosswalk:
    global _default_crosswalk
    if _default_crosswalk is None:
        _default_crosswalk = SicNaicsCrosswalk()
    return _default_crosswalk


def sic4_to_naics6(sic4: str) -> CrosswalkResult:
    return get_default_crosswalk().lookup(sic4)
