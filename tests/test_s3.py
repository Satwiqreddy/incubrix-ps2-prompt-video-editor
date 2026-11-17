"""S3/MinIO storage adapter, exercised against an in-process fake S3 (moto). Real MinIO is only used by docker compose."""
import pytest
import requests

moto = pytest.importorskip("moto")


def test_save_output_to_s3(monkeypatch, tmp_path):
    from moto import mock_aws
    monkeypatch.setenv("STORAGE", "s3")
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.setenv("S3_PUBLIC_ENDPOINT", "")
    monkeypatch.setenv("S3_ACCESS_KEY", "k")
    monkeypatch.setenv("S3_SECRET_KEY", "s")
    monkeypatch.setenv("S3_BUCKET", "bkt")
    f = tmp_path / "a.mp4"
    f.write_bytes(b"hello-video")
    with mock_aws():
        from app import store
        url = store.save_output(str(f), "sess/job/video.mp4")
        assert "bkt" in url and "X-Amz-Signature" in url, url
        assert requests.get(url).content == b"hello-video"
