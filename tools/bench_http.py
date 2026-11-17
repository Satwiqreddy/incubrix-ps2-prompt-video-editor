"""End-to-end latency on a REAL 60-minute video file through the HTTP API (ingest and render excluded).
python -m tools.bench_http [minutes]   -> bench_http_results.json"""
import json, os, statistics, subprocess, sys, tempfile, time

os.environ.setdefault("JOBS_INLINE", "1")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("TRANSCRIBER", "sidecar")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bench_"))


def main(minutes=60):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.media import ffmpeg
    from tools.bench import synth
    d = tempfile.mkdtemp(prefix="b60_")
    mp4 = os.path.join(d, "long.mp4")
    secs = minutes * 60
    t0 = time.time()
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x303030:s=160x90:r=10:d={secs}", "-f", "lavfi",
                    "-i", f"anoisesrc=d={secs}:a=0.02:r=16000", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40", "-c:a", "aac", "-b:a", "24k",
                    "-shortest", mp4], check=True)
    words, _ = synth(minutes)
    wj = os.path.join(d, "long.words.json")
    json.dump({"words": words}, open(wj, "w"))
    gen = time.time() - t0
    c = TestClient(app)
    t0 = time.time()
    r = c.post("/api/v1/videos", files={"file": ("long.mp4", open(mp4, "rb"), "video/mp4"), "transcript": ("w.json", open(wj, "rb"), "application/json")})
    j = c.get("/api/v1/jobs/" + r.json()["job_id"]).json()
    assert j["status"] == "succeeded", j
    ingest = time.time() - t0
    sid = c.post("/api/v1/sessions", json={"video_id": j["result"]["video_id"]}).json()["session_id"]
    say = ["cut 2:00-2:10", "cut the first 5 seconds", "speed up from 1:00 to 1:30 by 2x", "move 3:00-3:10 to the end",
           "add a caption 'Hi' when I say growth plan", "mute the word 'coffee'", "remove the part about pricing", "remove filler words",
           "remove silences longer than 1.5 seconds", "auto-caption the whole video", "add background music", "undo", "put back what I removed",
           "cut the last 10 seconds", "make it shorter"]
    ms, rows = [], []
    for rep in range(2):
        for t in say:
            t1 = time.perf_counter()
            x = c.post(f"/api/v1/sessions/{sid}/instructions", json={"say": t}).json()
            e = (time.perf_counter() - t1) * 1000
            if x["status"] == "needs_clarification":
                t1 = time.perf_counter()
                x = c.post(f"/api/v1/sessions/{sid}/answer", json={"answer": "all of them"}).json()
                e += (time.perf_counter() - t1) * 1000
            ms.append(e)
            rows.append((round(e), t, x["status"]))
    out = {"video_minutes": minutes, "video_file_mb": round(os.path.getsize(mp4) / 1e6, 1), "words": len(words), "instructions": len(ms),
           "median_ms": round(statistics.median(ms)), "p95_ms": round(sorted(ms)[int(.95 * len(ms)) - 1]), "max_ms": round(max(ms)),
           "under_3s": sum(m < 3000 for m in ms), "ingest_s": round(ingest, 1), "slowest": sorted(rows, reverse=True)[:3],
           "note": "latency measured through the HTTP API (JSON parse of the transcript and session included); ingest and render excluded"}
    json.dump(out, open("bench_http_results.json", "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
