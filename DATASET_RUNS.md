# Universe dataset publication contract

The CDK-deployed Universe pipeline has one authoritative publisher:
`euclidean-universe-downloader` starts a run and
`euclidean-universe-sic-worker` validates and publishes it. The root-level
`lambda_handler.py` is a disabled legacy entry point because it produced an
incomplete schema and wrote mutable S3 keys directly.

## S3 layout

Each execution gets a unique `run_id`. Its intermediate files and final output
never share keys with another execution:

```text
universe/work/runs/<run_id>/manifest.json
universe/work/runs/<run_id>/result_0.json
universe/_runs/universe/runs/<run_id>/outputs/universe.csv
universe/_runs/universe/runs/<run_id>/manifest.json
universe/_runs/universe/current.json
universe/universe.csv                         # compatibility alias only
universe/pending_sic_updates/<timestamp>.json
```

The run manifest is written only after `universe.csv` is complete. It records
the exact byte length and SHA-256 digest of the CSV, plus its deterministic and
AI-assisted quality report. `current.json` contains the run ID, manifest key,
and manifest SHA-256. Readers resolve the pointer, verify the manifest digest,
then verify the CSV length and digest before parsing any rows.

Promotion is compare-and-swap. A run records the current run it started from;
the worker refuses publication if that base changed, and the S3 conditional
write refuses a concurrent pointer update. Failed, stale, or partially written
runs therefore remain unreferenced and cannot become current. The mutable
`universe/universe.csv` object is refreshed only after successful promotion for
older consumers; new consumers use `current.json`.

## Quality gates

Deterministic checks run before publication and block:

- missing `ticker`, `cik`, `sic`, `naics`, or `naics_tier` columns;
- unexpectedly small datasets or duplicate tickers;
- malformed ticker, CIK, SIC, or populated NAICS values;
- a row-count drop above `UNIVERSE_DQ_MAX_ROW_DROP` (default 15%);
- a NAICS coverage drop above `UNIVERSE_DQ_MAX_NAICS_DROP` (default 5%).

The OpenRouter review receives aggregate profiles and at most
`DQ_EVIDENCE_ROWS` (default 48) real rows selected from systematic samples,
rule violations, membership changes, and classification changes. Ticker and
CIK values are replaced with stable SHA-256 pseudonyms; SIC, NAICS, tier, and
other non-identifier values remain visible. The response must match a strict
JSON schema and cite supplied evidence IDs. Its default `advisory` mode can
mark a run warning but cannot override deterministic failures. Set
`DQ_AI_MODE=enforce` only if AI availability and high-confidence failures
should block publication.

EDGAR filing polling does not publish the Universe dataset. It writes immutable
SIC correction events under `pending_sic_updates`; the next Universe run merges
them in key order and deletes them only after a successful publication.
