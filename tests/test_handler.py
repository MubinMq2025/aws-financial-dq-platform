"""
Tests for the Lambda handler, using a fake S3 client (no AWS needed).

The folder is named "lambda", which is a Python keyword, so we import it with importlib.
"""

import importlib
import io
import json

import pytest

handler = importlib.import_module("lambda.handler")

RAW_KEY = "raw/year=2026/month=11/day=08/transactions_2026-11-08_batch001.csv"
CSV = (
    "transaction_id,customer_id,merchant_id,transaction_ts,amount,currency,payment_method,payment_source,status,country,merchant_name\n"
    "TXN-1,CUST-1,MER-1,2026-11-08T09:00:00Z,10.00,AUD,card,pos_terminal,approved,AU,Cafe\n"
    "TXN-2,CUST-2,MER-2,2026-11-08T09:05:00Z,-5.00,AUD,card,pos_terminal,approved,AU,Cafe\n"
)


class FakeS3:
    """Records uploads in memory instead of talking to AWS."""

    def __init__(self):
        self.uploaded = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(CSV.encode())}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.uploaded[Key] = Body.decode()


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(handler, "s3", fake)
    monkeypatch.setenv("DATA_BUCKET", "test-bucket")
    return fake


def test_manual_event_writes_three_outputs(fake_s3):
    result = handler.lambda_handler({"key": RAW_KEY}, None)

    assert result["processed"][0]["valid_rows"] == 1
    assert result["processed"][0]["rejected_rows"] == 1
    assert set(fake_s3.uploaded) == {
        "validated/year=2026/month=11/day=08/transactions_2026-11-08_batch001.csv",
        "rejected/year=2026/month=11/day=08/transactions_2026-11-08_batch001_rejected.csv",
        "dq-reports/year=2026/month=11/day=08/transactions_2026-11-08_batch001_dq_report.json",
    }
    report = json.loads(fake_s3.uploaded["dq-reports/year=2026/month=11/day=08/transactions_2026-11-08_batch001_dq_report.json"])
    assert report["rejected_rows"] == 1


def test_s3_notification_event_is_understood(fake_s3):
    event = {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": RAW_KEY}}}]}
    assert len(handler.lambda_handler(event, None)["processed"]) == 1


def test_eventbridge_event_is_understood(fake_s3):
    event = {"detail": {"bucket": {"name": "test-bucket"}, "object": {"key": RAW_KEY}}}
    assert len(handler.lambda_handler(event, None)["processed"]) == 1


def test_non_raw_file_is_skipped(fake_s3):
    result = handler.lambda_handler({"key": "validated/some_file.csv"}, None)
    assert result == {"processed": [], "skipped": ["validated/some_file.csv"]}
    assert fake_s3.uploaded == {}
