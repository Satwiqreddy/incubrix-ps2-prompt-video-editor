"""S3 storage adapter: against moto in-process, and against a real HTTP moto server (the same service docker compose runs)."""
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


def test_save_output_against_a_real_s3_http_server(monkeypatch, tmp_path):
    """Same path as docker compose: a moto *server* over HTTP, path-style SigV4 presigned download."""
    from moto.server import ThreadedMotoServer
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    srv.start()
    try:
        host, port = srv.get_host_and_port()
        ep = f"http://127.0.0.1:{port}"
        for k, v in (("STORAGE", "s3"), ("S3_ENDPOINT", ep), ("S3_PUBLIC_ENDPOINT", ep), ("S3_ACCESS_KEY", "k"), ("S3_SECRET_KEY", "s"), ("S3_BUCKET", "bkt2")):
            monkeypatch.setenv(k, v)
        f = tmp_path / "a.mp4"
        f.write_bytes(b"over-http")
        from app import store
        url = store.save_output(str(f), "sess/job/video.mp4")
        assert url.startswith(ep) and requests.get(url).content == b"over-http"
    finally:
        srv.stop()
