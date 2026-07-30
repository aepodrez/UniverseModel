import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

WORKER_DIR = Path(__file__).resolve().parents[1] / "lambdas" / "universe_sic_worker"
sys.path.insert(0, str(WORKER_DIR))

import universe_dataset as dataset
from universe_dataset import (
    UniverseQualityError,
    current_key,
    evaluate_universe,
    load_current_universe,
    publish_universe,
)


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.etags = {}

    @staticmethod
    def _error(code):
        return ClientError({"Error": {"Code": code}}, "S3")

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None, **_kwargs):
        del Bucket
        if IfNoneMatch == "*" and Key in self.objects:
            raise self._error("PreconditionFailed")
        if IfMatch is not None and self.etags.get(Key) != IfMatch:
            raise self._error("PreconditionFailed")
        body = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[Key] = body
        self.etags[Key] = f'"{hashlib.md5(body).hexdigest()}"'  # nosec - fake ETag
        return {"ETag": self.etags[Key]}

    def get_object(self, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise self._error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": self.etags[Key]}


def _csv(count=1001, duplicate=False):
    lines = ["ticker,cik,sic,naics,naics_tier"]
    for index in range(count):
        ticker = "T0000" if duplicate and index == count - 1 else f"T{index:04d}"
        lines.append(f"{ticker},{index + 1:010d},7372,541511,exact_weighted")
    return ("\n".join(lines) + "\n").encode()


def test_valid_universe_passes_and_pseudonymizes_evidence(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "off")
    report = evaluate_universe(_csv(), None, None)
    assert report["status"] == "pass"
    assert report["profile"]["rows"] == 1001


def test_first_run_does_not_invent_membership_changes(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "off")
    rows = dataset.parse_csv(_csv())

    evidence = dataset._row_evidence(rows, [], [])

    assert {item["kind"] for item in evidence} == {"systematic_sample"}


def test_duplicate_ticker_blocks_publication(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "off")
    with pytest.raises(UniverseQualityError) as exc:
        evaluate_universe(_csv(1001, duplicate=True), None, None)
    assert any(item["code"] == "duplicate_ticker" for item in exc.value.report["deterministic_findings"])


def test_inferred_naics_tier_blocks_publication(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "off")
    data = _csv().replace(
        b"T0000,0000000001,7372,541511,exact_weighted",
        b"T0000,0000000001,7372,541511,rollup_group",
    )

    with pytest.raises(UniverseQualityError) as exc:
        evaluate_universe(data, None, None)

    assert any(
        item["code"] == "invalid_naics_tier"
        for item in exc.value.report["deterministic_findings"]
    )


def test_publish_and_verified_read_detect_corruption(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "off")
    s3 = FakeS3()
    data = _csv()
    quality = evaluate_universe(data, None, None)
    publish_universe(s3, "b", "universe", "universe/universe.csv", "run-1", data, quality, None)
    current = load_current_universe(s3, "b")
    assert current["data"] == data
    assert current["run_id"] == "run-1"
    manifest_pointer = json.loads(s3.objects[current_key("universe")])
    manifest = json.loads(s3.objects[manifest_pointer["manifest_key"]])
    s3.objects[manifest["output"]["key"]] += b"corrupt"
    with pytest.raises(RuntimeError, match="SHA-256"):
        load_current_universe(s3, "b")


def test_stale_base_cannot_replace_newer_current(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "off")
    s3 = FakeS3()
    data = _csv()
    quality = evaluate_universe(data, None, None)
    publish_universe(s3, "b", "universe", "universe/universe.csv", "run-1", data, quality, None)
    stale_etag = load_current_universe(s3, "b")["pointer_etag"]
    publish_universe(s3, "b", "universe", "universe/universe.csv", "run-2", data, quality, stale_etag)
    with pytest.raises(RuntimeError, match="concurrently"):
        publish_universe(s3, "b", "universe", "universe/universe.csv", "run-3", data, quality, stale_etag)


def test_openrouter_receives_classifications_but_not_raw_identifiers(monkeypatch):
    monkeypatch.setenv("DQ_AI_MODE", "advisory")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("UNIVERSE_DQ_MIN_ROWS", "1")
    captured = {}

    class Response:
        def __enter__(self):
            return io.BytesIO(json.dumps({"model": "test", "choices": [{"message": {
                "content": json.dumps({"verdict": "pass", "confidence": 1.0,
                                       "summary": "ok", "anomalies": []})}}]}).encode())

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        del timeout
        captured.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(dataset, "urlopen", fake_urlopen)
    raw = b"ticker,cik,sic,naics,naics_tier\nSECRET,0000000001,7372,541511,exact_weighted\n"
    report = evaluate_universe(raw, None, None)
    prompt = captured["messages"][0]["content"]
    assert report["status"] == "pass"
    assert "SECRET" not in prompt
    assert "0000000001" not in prompt
    assert "541511" in prompt
    assert "id:" in prompt
