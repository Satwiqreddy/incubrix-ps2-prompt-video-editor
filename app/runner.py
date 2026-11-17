"""Session runner / batch runner:  python -m app.runner <session.yaml | folder> -o <out dir> [--api URL] [--token T]
Plays scripted sessions against the HTTP API and writes <out>/<name>/results.json (+ final.mp4 when a render step runs).
Without --api it runs the service in-process (same code path, no server needed)."""
import argparse, json, os, sys, time, uuid
from urllib.parse import urlparse

import yaml


def client(api, token):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    if api:
        import httpx
        return httpx.Client(base_url=api.rstrip("/"), headers=h, timeout=600)
    os.environ.setdefault("JOBS_INLINE", "1")
    os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")
    from fastapi.testclient import TestClient
    from .main import app
    return TestClient(app, headers=h)


def wait(c, job):
    while job["status"] in ("queued", "running"):
        time.sleep(0.3)
        job = c.get(f"/api/v1/jobs/{job['job_id']}").json()
    return job


def check(exp, r):
    bad = []
    for k, want in (exp or {}).items():
        got = r.get(k)
        ok = abs(got - want) <= 0.05 if isinstance(want, (int, float)) and isinstance(got, (int, float)) else got == want
        if k == "options_count":
            ok = len(r["options"]) == want
        if not ok:
            bad.append(f"{k}: expected {want!r}, got {got!r}")
    return bad


def run_one(c, path, out):
    spec = yaml.safe_load(open(path, encoding="utf-8"))
    name = os.path.splitext(os.path.basename(path))[0]
    od = os.path.join(out, name)
    os.makedirs(od, exist_ok=True)
    base = os.path.dirname(os.path.abspath(path))
    v = spec["video"]
    idem = lambda: {"Idempotency-Key": uuid.uuid4().hex}  # noqa: E731
    if v.startswith("http"):
        j = c.post("/api/v1/videos", json={"url": v, "transcript_url": spec.get("transcript")}, headers=idem()).json()
    else:
        vp = os.path.join(base, v)
        files = {"file": (os.path.basename(vp), open(vp, "rb"), "video/mp4")}
        tp = os.path.join(base, spec["transcript"]) if spec.get("transcript") else os.path.splitext(vp)[0] + ".words.json"
        if os.path.exists(tp):
            files["transcript"] = (os.path.basename(tp), open(tp, "rb"), "application/json")
        j = c.post("/api/v1/videos", files=files, headers=idem()).json()
    if j.get("error"):
        return {"name": name, "error": j["error"]}
    j = wait(c, j)
    if j["status"] != "succeeded":
        return {"name": name, "error": j["error"]}
    vid = j["result"]["video_id"]
    sid = c.post("/api/v1/sessions", json={"video_id": vid}, headers=idem()).json()["session_id"]
    steps, fails, final_video = [], [], None
    for i, st in enumerate(spec["steps"], 1):
        r = c.post(f"/api/v1/sessions/{sid}/instructions", json={"say": st["say"]}, headers=idem()).json()
        row = {k: r.get(k) for k in ("status", "operations", "summary", "question", "options", "reason", "warnings", "timeline_hash", "duration_s", "time_policy", "latency_ms")}
        row = {"step": i, "say": st["say"], **row}
        if r.get("status") == "needs_clarification" and "answer" in st:
            a = c.post(f"/api/v1/sessions/{sid}/answer", json={"answer": st["answer"]}, headers=idem()).json()
            row["answer"] = {"said": st["answer"], **{k: a.get(k) for k in ("status", "operations", "summary", "reason", "warnings", "timeline_hash", "duration_s")}}
            final = a
        else:
            final = r
        if r.get("render_job_id"):
            rj = wait(c, {"job_id": r["render_job_id"], "status": "queued"})
            row["render"] = rj
            if rj["status"] == "succeeded":
                final_video = os.path.join(od, "final.mp4")
                data = c.get(urlparse(rj["result"]["video_url"]).path).content if not rj["result"]["video_url"].startswith("http://localhost") else c.get(urlparse(rj["result"]["video_url"]).path).content
                open(final_video, "wb").write(data)
                row["render"]["verification"] = rj["result"]["verification"]
                vv = rj["result"]["verification"]
                if vv["length_diff_frames"] > 1 or (vv["av_offset_frames"] or 0) > 1:
                    fails.append({"step": i, "say": st["say"], "failures": [f"render outside 1-frame tolerance: {vv}"]})
        bad = check(st.get("expect"), final if "answer" in row else r)
        if "expect_first" in st:
            bad += check(st["expect_first"], r)
        if bad:
            fails.append({"step": i, "say": st["say"], "failures": bad})
        steps.append(row)
    s = c.get(f"/api/v1/sessions/{sid}").json()
    tl = s.pop("timeline")
    json.dump(tl, open(os.path.join(od, "timeline.json"), "w"), indent=1)
    res = {"name": name, "session_id": sid, "video_id": vid, "source_sha256": s["source_sha256"], "source_unchanged": s["source_unchanged"],
           "steps": steps, "final_timeline_hash": s["timeline_hash"], "final_duration_s": s["duration_s"], "final_timeline": tl,
           "final_video": os.path.basename(final_video) if final_video else None, "expectation_failures": fails}
    json.dump(res, open(os.path.join(od, "results.json"), "w"), indent=1)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sessions")
    ap.add_argument("-o", "--out", default="results")
    ap.add_argument("--api", default=os.environ.get("EDITOR_API"))
    ap.add_argument("--token", default=os.environ.get("API_TOKEN"))
    a = ap.parse_args(argv)
    c = client(a.api, a.token)
    files = [a.sessions] if os.path.isfile(a.sessions) else sorted(os.path.join(a.sessions, f) for f in os.listdir(a.sessions) if f.endswith((".yaml", ".yml")))
    bad = 0
    for f in files:
        t0 = time.time()
        r = run_one(c, f, a.out)
        n = len(r.get("expectation_failures", []))
        bad += n + ("error" in r)
        print(f"{'FAIL' if n or 'error' in r else 'ok  '} {os.path.basename(f)}  steps={len(r.get('steps', []))} "
              f"final={r.get('final_duration_s')}s {time.time() - t0:.1f}s", *(["| " + str(x) for x in r.get("expectation_failures", [])]))
    print(f"results written to {a.out}/<session>/results.json")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
