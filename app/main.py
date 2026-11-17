"""HTTP API (/api/v1). Thin layer: jobs, idempotency, errors, logs. Editing logic lives in engine.py/timeline.py."""
import contextvars, hashlib, hmac, ipaddress, json, logging, os, shutil, socket, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import engine, media, store, timeline, transcript
from .config import cfg, env
from .errors import ApiError

# ------------------------------------------------------------------ logging
cid_var = contextvars.ContextVar("cid", default="-")
job_var = contextvars.ContextVar("job", default="-")


class JsonFmt(logging.Formatter):
    def format(self, r):
        return json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + "Z", "level": r.levelname,
                           "msg": r.getMessage(), "job_id": getattr(r, "job_id", job_var.get()),
                           "correlation_id": cid_var.get(), **getattr(r, "extra_", {})})


log = logging.getLogger("editor")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(JsonFmt())
    log.addHandler(h)
    log.setLevel(logging.INFO)

app = FastAPI(title="IncuBrix Prompt-Based Video Editing Engine", version="1.0.0",
              description="Edit a video by typing. Edits attach to content (word ids), never to clock positions.")
pool = ThreadPoolExecutor(max_workers=int(env("WORKERS", "2")))
_locks: dict = {}
_hits: dict = {}
ERR_CODES = {  # also listed in README
    "VALIDATION_ERROR": 422, "NOT_FOUND": 404, "UNAUTHORIZED": 401, "RATE_LIMITED": 429, "UNSUPPORTED_MEDIA": 415,
    "TRANSCRIPT_MISSING": 422, "TRANSCRIPTION_FAILED": 502, "IDEMPOTENCY_KEY_REUSED": 422, "RENDER_FAILED": 500, "FETCH_FAILED": 502,
    "URL_NOT_ALLOWED": 400, "PAYLOAD_TOO_LARGE": 413, "SESSION_BUSY": 409, "CONFIG_INVALID": 500, "INTERNAL_ERROR": 500}


# ------------------------------------------------------------------ middleware / errors
@app.middleware("http")
async def mw(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex[:12]
    cid_var.set(cid)
    t0 = time.time()
    try:
        p = request.url.path
        open_path = p in ("/", "/health", "/ready", "/docs", "/openapi.json", "/api/v1/health", "/api/v1/ready") or p.startswith("/api/v1/files/") or p.endswith("/source")
        if cfg.api_token and not open_path and request.headers.get("Authorization") != f"Bearer {cfg.api_token}":
            raise ApiError("UNAUTHORIZED", "missing or wrong bearer token", 401)
        if request.method == "POST" and cfg.rate > 0:
            ip = request.client.host if request.client else "?"
            q = [t for t in _hits.get(ip, []) if t > t0 - 60]
            if len(q) >= cfg.rate:
                raise ApiError("RATE_LIMITED", f"more than {cfg.rate} POSTs per minute", 429, True)
            _hits[ip] = q + [t0]
        resp = await call_next(request)
    except ApiError as e:
        resp = JSONResponse(e.body(), status_code=e.status)
    resp.headers["X-Correlation-Id"] = cid
    log.info(f"{request.method} {request.url.path} {resp.status_code}", extra={"extra_": {"ms": round((time.time() - t0) * 1000)}})
    return resp


@app.exception_handler(ApiError)
async def _api(_, e):
    return JSONResponse(e.body(), status_code=e.status)


@app.exception_handler(RequestValidationError)
async def _val(_, e):
    return JSONResponse(ApiError("VALIDATION_ERROR", "invalid request", 422, False, {"errors": json.loads(json.dumps(e.errors(), default=str))}).body(), status_code=422)


@app.exception_handler(StarletteHTTPException)
async def _http(_, e):
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, "HTTP_ERROR")
    return JSONResponse(ApiError(code, str(e.detail), e.status_code).body(), status_code=e.status_code)


@app.exception_handler(Exception)
async def _any(_, e):
    log.exception("unhandled")
    return JSONResponse(ApiError("INTERNAL_ERROR", "unexpected error", 500, True).body(), status_code=500)


# ------------------------------------------------------------------ helpers
def idempotent(request: Request, body: bytes, fn):
    """Same Idempotency-Key + same route + same body -> stored response, work never repeated."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return fn()
    route, rh = request.method + request.url.path, hashlib.sha256(body).hexdigest()
    with _locks.setdefault("idem:" + key, threading.Lock()):
        row = store.idem_get(key, route)
        if row:
            if row[0] != rh:
                raise ApiError("IDEMPOTENCY_KEY_REUSED", "this Idempotency-Key was used with a different request body", 422)
            return JSONResponse(json.loads(row[2]), status_code=row[1], headers={"Idempotent-Replay": "true"})
        out = fn()
        if isinstance(out, JSONResponse):
            store.idem_put(key, route, rh, out.status_code, json.loads(out.body))
        return out


def need(kind, id):
    d = store.get(kind, id)
    if not d:
        raise ApiError("NOT_FOUND", f"{kind} {id} not found", 404)
    return d


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check_url(u):
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ApiError("URL_NOT_ALLOWED", "only http(s) URLs are accepted", 400)
    if env("ALLOW_PRIVATE_URLS", "false").lower() != "true":
        for ai in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(ai[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ApiError("URL_NOT_ALLOWED", "private/loopback addresses are not allowed", 400)


def fetch(url, dst):
    check_url(url)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            n = 0
            with open(dst, "wb") as f:
                for b in r.iter_bytes(1 << 20):
                    n += len(b)
                    if n > cfg.max_upload:
                        raise ApiError("PAYLOAD_TOO_LARGE", "download too large", 413)
                    f.write(b)
    except httpx.HTTPError as e:
        raise ApiError("FETCH_FAILED", f"could not download {url}: {e}", 502, True)


# ------------------------------------------------------------------ jobs
def new_job(kind, cb=None):
    j = {"job_id": uuid.uuid4().hex[:16], "kind": kind, "status": "queued", "progress": 0, "result": None, "error": None,
         "callback_url": cb, "created": time.time()}
    store.put("job", j["job_id"], j)
    return j


def run_job(j, fn):
    def work():
        job_var.set(j["job_id"])
        j.update(status="running")
        store.put("job", j["job_id"], j)
        t0 = time.time()

        def prog(p):
            j["progress"] = p
            store.put("job", j["job_id"], j)
        try:
            j["result"] = fn(prog)
            j.update(status="succeeded", progress=100)
        except ApiError as e:
            j.update(status="failed", error=e.body()["error"])
        except Exception as e:  # noqa
            log.exception("job failed", extra={"job_id": j["job_id"]})
            j.update(status="failed", error=ApiError("INTERNAL_ERROR", str(e), 500, True).body()["error"])
        j["seconds"] = round(time.time() - t0, 2)
        store.put("job", j["job_id"], j)
        log.info(f"job {j['status']}", extra={"job_id": j["job_id"], "extra_": {"seconds": j["seconds"]}})
        if j.get("callback_url"):
            body = json.dumps({"job_id": j["job_id"], "status": j["status"], "result": j["result"], "error": j["error"]}).encode()
            sig = hmac.new(cfg.callback_secret.encode(), body, hashlib.sha256).hexdigest()
            try:
                httpx.post(j["callback_url"], content=body, timeout=10,
                           headers={"Content-Type": "application/json", "X-IncuBrix-Signature": sig})
            except Exception as e:  # noqa
                log.warning(f"callback failed: {e}", extra={"job_id": j["job_id"]})
    if os.environ.get("JOBS_INLINE") == "1":  # tests
        work()
    else:
        pool.submit(work)


def job_view(j):
    out = {k: j[k] for k in ("job_id", "kind", "status", "progress", "result", "error")}
    out["links"] = {"self": f"{cfg.public_url}/api/v1/jobs/{j['job_id']}"}
    return out


# ------------------------------------------------------------------ models
class SessionIn(BaseModel):
    video_id: str


class SayIn(BaseModel):
    say: str


class AnswerIn(BaseModel):
    answer: str


class RenderIn(BaseModel):
    callback_url: str | None = None


# ------------------------------------------------------------------ routes
def ingest_result(vid, path, sc, prog):
    """probe + transcript for a saved video file; stores the video document and returns the job result."""
    info = media.probe(path)
    info["sha256"] = sha256_file(path)
    os.chmod(path, 0o444)  # the original is never modified
    prog(50)
    t0 = time.time()
    words = transcript.transcribe(path, sc, info["has_audio"])
    warns = transcript.warnings(words)
    if sc is None and len({w["speaker"] for w in words}) > 1:
        warns.append("2 speakers estimated from voice pitch (S1 = first voice heard); voices with similar pitch cannot be told apart")
    if info["vfr"]:
        warns.append("variable frame rate detected: output is rendered at constant 30 fps")
    if not info["has_audio"]:
        warns.append("no audio track: mute/bleep/music-ducking are unavailable, captions and cuts work")
    if not words:
        warns.append("transcript is empty: word-based edits are unavailable")
    store.put("video", vid, {"id": vid, "path": path, "source": info, "words": words, "warnings": warns})
    return {"video_id": vid, "source": info, "word_count": len(words), "warnings": warns,
            "manifest": {"stage": "ingest", "transcriber": cfg.transcriber,
                         "model": cfg.whisper_model if cfg.transcriber in ("auto", "whisper") and sc is None else "transcript-file",
                         "seconds": {"transcribe": round(time.time() - t0, 2)}, "cost_usd": 0.0,
                         "licence": "user-supplied media; transcript from user file or MIT-licensed faster-whisper",
                         "seed": None, "ai_generated": False}}


SAMPLE_TITLES = {"real_speech": "Interview, two voices (computer speech)", "single": "Talk, one speaker (tone bursts)",
                 "two_speakers": "Interview, two speakers (tone bursts)"}


def _samples():
    d = env("SAMPLES_DIR", "fixtures")
    out = []
    for n in ("real_speech", "single", "two_speakers"):
        if os.path.exists(os.path.join(d, n + ".mp4")) and os.path.exists(os.path.join(d, n + ".words.json")):
            out.append({"name": n, "title": SAMPLE_TITLES[n]})
    return d, out


@app.get("/api/v1/samples", summary="Built-in sample videos for trying the editor")
def list_samples():
    return {"samples": _samples()[1]}


@app.post("/api/v1/samples/{name}", status_code=202, summary="Load a built-in sample video (starts an ingest job)")
def load_sample(name: str):
    d, items = _samples()
    if name not in [i["name"] for i in items]:
        raise ApiError("NOT_FOUND", f"sample {name} not found", 404)
    vid = "vid_" + uuid.uuid4().hex[:10]
    vd = os.path.join(cfg.data_dir, "videos", vid)
    os.makedirs(vd, exist_ok=True)
    path = os.path.join(vd, "source.mp4")
    shutil.copyfile(os.path.join(d, name + ".mp4"), path)
    sc = json.load(open(os.path.join(d, name + ".words.json"), encoding="utf-8"))
    job = new_job("ingest")

    def work(prog):
        prog(20)
        return ingest_result(vid, path, sc, prog)
    run_job(job, work)
    return JSONResponse(job_view(job), status_code=202)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/health", include_in_schema=False)
@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
@app.get("/api/v1/ready")
def ready():
    try:
        store.ping()
        media.ffmpeg()
    except Exception as e:  # noqa
        raise ApiError("CONFIG_INVALID", f"not ready: {e}", 503, True)
    return {"status": "ready", "transcriber": cfg.transcriber, "nlu": cfg.nlu, "storage": cfg.storage}


@app.post("/api/v1/videos", status_code=202, summary="Upload (multipart `file`) or give a URL (JSON `url`); starts an ingest job")
async def create_video(request: Request):
    body = await request.body()
    if len(body) > cfg.max_upload:
        raise ApiError("PAYLOAD_TOO_LARGE", "upload too large", 413)
    ctype = request.headers.get("content-type", "")

    vid = "vid_" + uuid.uuid4().hex[:10]
    d = os.path.join(cfg.data_dir, "videos", vid)
    sidecar, cb, url, tr_url, data, ext = None, None, None, None, None, ".mp4"
    if ctype.startswith("multipart/"):
        form = await request.form()
        f = form.get("file")
        ext = os.path.splitext(getattr(f, "filename", "") or "")[1].lower()
        if f is None or ext not in (".mp4", ".mov"):
            raise ApiError("UNSUPPORTED_MEDIA", "upload an MP4 or MOV as multipart field 'file'", 415)
        data = await f.read()
        t = form.get("transcript")
        if t is not None and hasattr(t, "read"):
            sidecar = json.loads((await t.read()).decode("utf-8"))
        elif isinstance(t, str) and t.strip():
            sidecar = json.loads(t)
        cb = form.get("callback_url")
        # multipart boundaries differ per request, so fingerprint the CONTENT for the idempotency check
        body = hashlib.sha256(data).digest() + json.dumps([sidecar, cb], sort_keys=True).encode()
    else:
        try:
            j = json.loads(body or b"{}")
        except ValueError:
            raise ApiError("VALIDATION_ERROR", "body must be JSON or multipart", 422)
        url, cb, tr_url, sidecar = j.get("url"), j.get("callback_url"), j.get("transcript_url"), j.get("transcript")
        if not url:
            raise ApiError("VALIDATION_ERROR", "give multipart 'file' or JSON {'url': ...}", 422)
        check_url(url)
        ext = os.path.splitext(urlparse(url).path)[1].lower()
        ext = ext if ext in (".mp4", ".mov") else ".mp4"
    if cb:
        check_url(cb)
    path = os.path.join(d, "source" + ext)

    def respond():
        job = new_job("ingest", cb)
        os.makedirs(d, exist_ok=True)
        if data is not None:
            with open(path, "wb") as out:
                out.write(data)

        def work(prog):
            if url:
                fetch(url, path)
            if tr_url:
                tmp = os.path.join(d, "t.json")
                fetch(tr_url, tmp)
                sc = json.load(open(tmp, encoding="utf-8"))
            else:
                sc = sidecar
            prog(20)
            return ingest_result(vid, path, sc, prog)
        run_job(job, work)
        return JSONResponse(job_view(job), status_code=202)
    return await run_in_threadpool(idempotent, request, body, respond)


@app.get("/api/v1/videos/{vid}")
def get_video(vid: str):
    v = need("video", vid)
    return {"video_id": vid, "source": v["source"], "word_count": len(v["words"]), "warnings": v["warnings"]}


@app.get("/api/v1/videos/{vid}/source", summary="The original, untouched video file")
def get_source(vid: str):
    return FileResponse(need("video", vid)["path"], media_type="video/mp4")


@app.get("/api/v1/videos/{vid}/transcript")
def get_transcript(vid: str):
    return {"video_id": vid, "words": need("video", vid)["words"]}


@app.post("/api/v1/sessions", status_code=201)
async def create_session(body: SessionIn, request: Request):
    raw = await request.body()

    def go():
        need("video", body.video_id)
        s = engine.new_session("ses_" + uuid.uuid4().hex[:10], body.video_id)
        store.put("session", s["id"], s)
        return JSONResponse({"session_id": s["id"], "video_id": s["video_id"]}, status_code=201)
    return await run_in_threadpool(idempotent, request, raw, go)


def _view(s, v):
    tl = timeline.derive(s["entries"], v["words"], v["source"]["duration_s"], v["source"])
    return tl, {"session_id": s["id"], "video_id": v["id"], "timeline_hash": timeline.thash(tl), "duration_s": tl["duration_s"],
                "source_sha256": v["source"]["sha256"], "source_unchanged": sha256_file(v["path"]) == v["source"]["sha256"],
                "history": [{"step_id": e["id"], "instruction": e["instruction"], "operations": e["ops"]} for e in s["entries"]],
                "redo_available": len(s["redo"]), "pending_question": (s.get("pending") or {}).get("question"),
                "pending_options": [o["label"] for o in (s.get("pending") or {}).get("options", [])], "timeline": tl}


@app.get("/api/v1/sessions/{sid}")
def get_session(sid: str):
    s = need("session", sid)
    return _view(s, need("video", s["video_id"]))[1]


@app.get("/api/v1/sessions/{sid}/timeline")
def get_timeline(sid: str, format: str = "json"):
    s = need("session", sid)
    v = need("video", s["video_id"])
    tl = _view(s, v)[0]
    return timeline.to_otio(tl, os.path.basename(v["path"])) if format == "otio" else tl


def _locked(sid):
    return _locks.setdefault("s:" + sid, threading.Lock())


def _step(sid, fn):
    with _locked(sid):
        s = need("session", sid)
        v = need("video", s["video_id"])
        t0 = time.time()
        r = fn(s, v)
        store.put("session", sid, s)
        r["session_id"], r["latency_ms"] = sid, round((time.time() - t0) * 1000)
        if r.get("render"):
            r["render_job_id"] = _start_render(sid, s, v, None)["job_id"]
        log.info(f"instruction {r['status']}", extra={"extra_": {"say": r["instruction"][:80], "ms": r["latency_ms"]}})
        return JSONResponse(r)


@app.post("/api/v1/sessions/{sid}/instructions", summary="Send one plain-language instruction")
async def instruct(sid: str, body: SayIn, request: Request):
    raw = await request.body()
    return await run_in_threadpool(idempotent, request, raw, lambda: _step(sid, lambda s, v: engine.handle(s, v, body.say)))


@app.post("/api/v1/sessions/{sid}/answer", summary="Answer the pending clarification question")
async def answer(sid: str, body: AnswerIn, request: Request):
    raw = await request.body()
    return await run_in_threadpool(idempotent, request, raw, lambda: _step(sid, lambda s, v: engine.answer(s, v, body.answer)))


def _start_render(sid, s, v, cb):
    tl = _view(s, v)[0]
    job = new_job("render", cb)
    snapshot = json.loads(json.dumps(tl))

    def work(prog):
        t0 = time.time()
        out = os.path.join(cfg.data_dir, "work", f"{job['job_id']}.mp4")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        ver = media.render(v["path"], snapshot, v["source"], out, prog)
        key = f"{sid}/{job['job_id']}"
        vurl = store.save_output(out, key + "/video.mp4")
        tp = os.path.join(cfg.data_dir, "work", f"{job['job_id']}.timeline.json")
        json.dump(snapshot, open(tp, "w"), indent=1)
        op = os.path.join(cfg.data_dir, "work", f"{job['job_id']}.otio")
        json.dump(timeline.to_otio(snapshot, os.path.basename(v["path"])), open(op, "w"), indent=1)
        warns = list(snapshot["warnings"])
        if ver["length_diff_frames"] > 1 or (ver["av_offset_frames"] or 0) > 1:
            warns.append("render exceeded the 1-frame tolerance")
        return {"video_url": vurl, "timeline_url": store.save_output(tp, key + "/timeline.json"),
                "otio_url": store.save_output(op, key + "/timeline.otio"), "timeline_hash": timeline.thash(snapshot),
                "verification": ver,
                "manifest": {"stage": "render", "tool": "ffmpeg", "settings": {"codec": "libx264/aac", "crf": 23, "fps": v["source"]["fps"]},
                             "seed": None, "seconds": round(time.time() - t0, 2), "cost_usd": 0.0, "warnings": warns,
                             "ai_generated": bool(snapshot["music"]),
                             "licence": "source media is the user's; built-in music is a synthetic chord pad generated by this service (no third-party audio)",
                             "music_label": "AI/synthetic-generated" if snapshot["music"] else None}}
    run_job(job, work)
    return job


@app.post("/api/v1/sessions/{sid}/render", status_code=202)
async def render(sid: str, request: Request, body: RenderIn | None = None):
    raw = await request.body()

    def go():
        with _locked(sid):
            s = need("session", sid)
            v = need("video", s["video_id"])
            cb = body.callback_url if body else None
            if cb:
                check_url(cb)
            return JSONResponse(job_view(_start_render(sid, s, v, cb)), status_code=202)
    return await run_in_threadpool(idempotent, request, raw, go)


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str):
    return job_view(need("job", job_id))


@app.get("/api/v1/files/{path:path}", include_in_schema=False)
def files(path: str):
    p = os.path.realpath(os.path.join(cfg.data_dir, "outputs", path))
    if not p.startswith(os.path.realpath(os.path.join(cfg.data_dir, "outputs"))) or not os.path.isfile(p):
        raise ApiError("NOT_FOUND", "file not found", 404)
    return FileResponse(p)


@app.get("/api/v1/timeline-schema")
def tl_schema():
    return json.load(open(os.path.join(os.path.dirname(__file__), "..", "schema", "timeline.schema.json")))
