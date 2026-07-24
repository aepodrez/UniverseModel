import importlib.util
import io
import json
import os
import sys
from pathlib import Path


os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("SIC_WORKER_FUNCTION_NAME", "worker")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

DOWNLOADER_DIR = Path(__file__).resolve().parents[1] / "lambdas" / "universe_downloader"
sys.path.insert(0, str(DOWNLOADER_DIR))
spec = importlib.util.spec_from_file_location("universe_downloader", DOWNLOADER_DIR / "lambda_function.py")
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class Paginator:
    def __init__(self, objects):
        self.objects = objects

    def paginate(self, Bucket, Prefix):
        del Bucket
        yield {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)]}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.puts = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return Paginator(self.objects)

    def get_object(self, Bucket, Key):
        del Bucket
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


class FakeLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)


def test_pending_sic_updates_are_merged_in_key_order(monkeypatch):
    prefix = "universe/pending_sic_updates"
    fake = FakeS3({
        f"{prefix}/a.json": json.dumps({"updates": {"1": "1111", "2": "2222"}}).encode(),
        f"{prefix}/b.json": json.dumps({"updates": {"1": "3333"}}).encode(),
    })
    monkeypatch.setattr(downloader, "s3", fake)
    updates, keys = downloader._load_pending_sic_updates()
    assert updates == {"0000000001": "3333", "0000000002": "2222"}
    assert keys == [f"{prefix}/a.json", f"{prefix}/b.json"]


def test_downloader_creates_run_scoped_work_and_propagates_run_id(monkeypatch):
    fake_s3 = FakeS3()
    fake_lambda = FakeLambda()
    monkeypatch.setattr(downloader, "s3", fake_s3)
    monkeypatch.setattr(downloader, "lambda_", fake_lambda)
    monkeypatch.setattr(downloader, "_fetch_all_tickers", lambda: [
        {"cik": "0000000001", "ticker": "A", "name": "A", "exchange": "NYSE"},
        {"cik": "0000000002", "ticker": "B", "name": "B", "exchange": "Nasdaq"},
    ])
    monkeypatch.setattr(downloader, "_filter_by_exchange", lambda rows: rows)
    monkeypatch.setattr(downloader, "_load_existing_sic_cache",
                        lambda: ({"0000000001": "1111"}, "base-run", '"etag"'))
    monkeypatch.setattr(downloader, "_load_pending_sic_updates",
                        lambda: ({"0000000002": "2222"}, ["pending/a.json"]))

    result = downloader.lambda_handler({"run_id": "run-new"}, None)
    assert result["run_id"] == "run-new"
    assert fake_s3.puts[0]["Key"] == "universe/work/runs/run-new/manifest.json"
    manifest = json.loads(fake_s3.puts[0]["Body"])
    assert manifest["base_run_id"] == "base-run"
    assert manifest["existing_sic"]["0000000002"] == "2222"
    assert manifest["pending_sic_keys"] == ["pending/a.json"]
    assert json.loads(fake_lambda.calls[0]["Payload"])["run_id"] == "run-new"
