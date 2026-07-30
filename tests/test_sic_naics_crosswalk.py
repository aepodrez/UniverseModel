import sys
from pathlib import Path


WORKER_DIR = Path(__file__).resolve().parents[1] / "lambdas" / "universe_sic_worker"
sys.path.insert(0, str(WORKER_DIR))

from sic_naics_crosswalk import SicNaicsCrosswalk  # noqa: E402


def test_missing_exact_sic_does_not_infer_six_digit_naics_from_group():
    result = SicNaicsCrosswalk().lookup("0110")

    assert result.naics6 is None
    assert result.tier == "unresolved"
    assert result.sic4_used is None
