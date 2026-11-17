import hashlib, hmac, json, os
import jsonschema, pytest
from conftest import upload, new_session


def test_health_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/v1/health").status_code == 200
    r = client.get("/api/v1/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_full_flow_and_original_untouched(client, fx):
    vid, _ = upload(client, os.path.join(fx, "single.mp4"))
    sid = new_session(client, vid)
    r = client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "cut the first 30 seconds"}).json()
    assert r["status"] == "applied" and abs(r["duration_s"] - 120) < 0.6 and r["latency_ms"] < 3000
    s = client.get(f"/api/v1/sessions/{sid}").json()
    assert s["source_unchanged"] is True and s["history"][0]["operations"][0]["op"] == "cut"
    schema = client.get("/api/v1/timeline-schema").json()
    jsonschema.validate(s["timeline"], schema)
    otio = client.get(f"/api/v1/sessions/{sid}/timeline?format=otio").json()
    assert otio["OTIO_SCHEMA"] == "Timeline.1" and otio["tracks"]["children"][0]["children"]


def test_clarification_flow_over_http(client, fx):
    vid, _ = upload(client, os.path.join(fx, "single.mp4"))
    sid = new_session(client, vid)
    r = client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "remove the part about pricing"}).json()
    assert r["status"] == "needs_clarification" and r["question"] and len(r["options"]) >= 2
    assert client.get(f"/api/v1/sessions/{sid}").json()["pending_question"]
    r = client.post(f"/api/v1/sessions/{sid}/answer", json={"answer": "the second one"}).json()
    assert r["status"] == "applied"
    assert client.post(f"/api/v1/sessions/{sid}/answer", json={"answer": "x"}).json()["status"] == "rejected"


def test_idempotency_instruction_applies_once(client, fx):
    vid, _ = upload(client, os.path.join(fx, "single.mp4"))
    sid = new_session(client, vid)
    h = {"Idempotency-Key": "k-1"}
    a = client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "cut the first 30 seconds"}, headers=h)
    b = client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "cut the first 30 seconds"}, headers=h)
    assert a.json() == b.json() and b.headers["Idempotent-Replay"] == "true"
    assert len(client.get(f"/api/v1/sessions/{sid}").json()["history"]) == 1


def test_idempotency_upload_returns_same_job(client, fx):
    p = os.path.join(fx, "two.mp4")
    _, a = upload(client, p, key="up-1")
    _, b = upload(client, p, key="up-1")
    assert a.json()["job_id"] == b.json()["job_id"]


def test_idempotency_key_reuse_with_other_body(client, fx):
    vid, _ = upload(client, os.path.join(fx, "single.mp4"))
    sid = new_session(client, vid)
    h = {"Idempotency-Key": "k-2"}
    client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "undo"}, headers=h)
    r = client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "redo"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("call,code,status", [
    (lambda c: c.get("/api/v1/sessions/nope"), "NOT_FOUND", 404),
    (lambda c: c.post("/api/v1/sessions", json={"video_id": "nope"}), "NOT_FOUND", 404),
    (lambda c: c.post("/api/v1/sessions", json={}), "VALIDATION_ERROR", 422),
    (lambda c: c.post("/api/v1/videos", json={}), "VALIDATION_ERROR", 422),
    (lambda c: c.post("/api/v1/videos", json={"url": "ftp://x/y.mp4"}), "URL_NOT_ALLOWED", 400),
    (lambda c: c.post("/api/v1/videos", files={"file": ("a.txt", b"hello", "text/plain")}), "UNSUPPORTED_MEDIA", 415),
    (lambda c: c.get("/api/v1/jobs/zzz"), "NOT_FOUND", 404),
])
def test_error_shape(client, call, code, status):
    r = call(client)
    e = r.json()["error"]
    assert r.status_code == status and e["code"] == code and set(e) == {"code", "message", "retryable", "details"}


def test_corrupt_video_rejected(client):
    r = client.post("/api/v1/videos", files={"file": ("a.mp4", b"not a video at all", "video/mp4")})
    j = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert j["status"] == "failed" and j["error"]["code"] == "UNSUPPORTED_MEDIA"


def test_auth_and_rate_limit(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "sekret")
    assert client.get("/api/v1/jobs/x").status_code == 401
    assert client.get("/api/v1/jobs/x", headers={"Authorization": "Bearer sekret"}).status_code == 404
    assert client.get("/health").status_code == 200
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    from app import main
    main._hits.clear()
    codes = [client.post("/api/v1/sessions", json={"video_id": "x"}).status_code for _ in range(5)]
    assert codes[-1] == 429
    r = client.post("/api/v1/sessions", json={"video_id": "x"})
    assert r.json()["error"]["retryable"] is True
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")


def test_correlation_id_echoed(client):
    assert client.get("/health", headers={"X-Correlation-Id": "abc123"}).headers["X-Correlation-Id"] == "abc123"


def test_callback_signature(client, fx, monkeypatch):
    sent = {}
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, content, timeout, headers: sent.update(url=url, body=content, headers=headers))
    p = os.path.join(fx, "two.mp4")
    files = {"file": ("two.mp4", open(p, "rb"), "video/mp4"),
             "transcript": ("w.json", open(os.path.splitext(p)[0] + ".words.json", "rb"), "application/json")}
    r = client.post("/api/v1/videos", files=files, data={"callback_url": "http://example.test/hook"})
    assert r.status_code == 202
    want = hmac.new(b"test-secret", sent["body"], hashlib.sha256).hexdigest()
    assert sent["headers"]["X-IncuBrix-Signature"] == want and json.loads(sent["body"])["status"] == "succeeded"


def test_ingest_warnings_vfr_noaudio(client, fx):
    vid, r = upload(client, os.path.join(fx, "vfr.mp4"))
    assert any("variable frame rate" in w for w in client.get(f"/api/v1/videos/{vid}").json()["warnings"])
    vid, r = upload(client, os.path.join(fx, "noaudio.mp4"))
    assert any("no audio" in w for w in client.get(f"/api/v1/videos/{vid}").json()["warnings"])
    sid = new_session(client, vid)
    assert client.post(f"/api/v1/sessions/{sid}/instructions", json={"say": "mute the word 'coffee'"}).json()["status"] == "rejected"


def test_openapi_matches_running_app(client):
    from app.dump_openapi import spec
    here = os.path.join(os.path.dirname(__file__), "..", "openapi.yaml")
    assert open(here, encoding="utf-8").read() == spec(), "regenerate: python -m app.dump_openapi > openapi.yaml"
    assert client.get("/openapi.json").json()["openapi"].startswith("3.1")


def test_secrets_not_in_logs(client, capsys):
    client.get("/health", headers={"Authorization": "Bearer topsecret"})
    assert "topsecret" not in capsys.readouterr().err
