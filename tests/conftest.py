import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
_d = tempfile.mkdtemp(prefix="editor_test_")
os.environ.update(DATA_DIR=_d, JOBS_INLINE="1", RATE_LIMIT_PER_MIN="0", API_TOKEN="", TRANSCRIBER="sidecar", NLU="rules",
                  PUBLIC_URL="http://testserver", STORAGE="local", CALLBACK_SECRET="test-secret", ALLOW_PRIVATE_URLS="true")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def fx():
    """Own test media, generated once per run (tone-burst 'speech' + testsrc2 picture)."""
    from tools.fixtures import SINGLE, TWO, make_video
    d = tempfile.mkdtemp(prefix="fx_")
    make_video(SINGLE, os.path.join(d, "single.mp4"), 150)
    make_video(TWO, os.path.join(d, "two.mp4"), 105)
    make_video(SINGLE, os.path.join(d, "vfr.mp4"), 150, vfr=True)
    make_video(SINGLE, os.path.join(d, "noaudio.mp4"), 150, with_audio=False)
    return d


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def upload(client, path, key=None):
    files = {"file": (os.path.basename(path), open(path, "rb"), "video/mp4"),
             "transcript": ("w.json", open(os.path.splitext(path)[0] + ".words.json", "rb"), "application/json")}
    r = client.post("/api/v1/videos", files=files, headers={"Idempotency-Key": key} if key else {})
    assert r.status_code == 202, r.text
    j = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert j["status"] == "succeeded", j
    return j["result"]["video_id"], r


def new_session(client, vid):
    return client.post("/api/v1/sessions", json={"video_id": vid}).json()["session_id"]
