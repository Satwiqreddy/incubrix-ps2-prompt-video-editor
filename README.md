# Prompt-Based Video Editing Engine (IncuBrix Assessment 2)

Edit a video by typing. `"cut the first 30 seconds"`, `"remove the part about pricing"`, `"add a caption 'Welcome!' when I say welcome to the channel"`, `"undo"`.
Every instruction gets exactly one answer: **applied**, **needs_clarification** (a specific question with options) or **rejected** (with the reason).

**The one rule:** edits belong to *content*, never to a clock position. Each edit is stored against the **original source time / transcript word ids**; the edited timeline is *derived* from the untouched original plus the edit history, so a caption stays on its words after cuts, speed changes, reorders and restores. See `TIMELINE.md`.

```
text ──► nlu.py (words → intents; rules by default, optional LLM) ──► engine.py (intents → ops, asks/rejects)
                                                                        │ ops are plain data, appended to history
                                   timeline.py  fold(history) → segments + overlays → hash   (deterministic, no model)
                                                                        │
                                   media.py  ffmpeg render of the derived timeline (separate step, original never touched)
FastAPI (main.py): jobs · Idempotency-Key · JSON errors · logs · MinIO/S3 · /api/v1    runner.py: scripted sessions via HTTP
```

## Run it (under 10 commands)

```bash
pip install -r requirements.txt          # Python 3.11+; ffmpeg on PATH (or pip's imageio-ffmpeg is picked up automatically)
python -m tools.fixtures fixtures        # our own test videos (tone-burst "speech" + testsrc2) + word transcripts
uvicorn app.main:app --port 8000         # API + docs at http://localhost:8000/docs
python -m app.runner sessions/01_worked_example.yaml -o results --api http://localhost:8000
```
Docker: `docker compose up` (API :8000 + MinIO :9000/:9001). All tests, no API keys: `docker compose run --rm tests`.
Without a server: `python -m app.runner sessions -o results` runs every session in-process.
**Tests:** `python -m pytest -q` (135 tests, ~2 min; the scenario suite alone is 1 s: `pytest tests/test_scenarios.py`).

**Real-sounding speech test (Windows):** `python -m tools.make_speech_fixture fixtures` → `fixtures/real_speech.mp4` + `.words.json` (two Windows system voices; word timings from the synthesiser). Session `sessions_windows/19_real_speech.yaml` (kept out of the default folder because the clip is not committed). Local testing only (TTS licence unverified).

## API (`openapi.yaml`, all under `/api/v1`)

| | |
|---|---|
| `POST /videos` | multipart `file` (+ optional `transcript` words.json) **or** JSON `{"url": ...}` → `202` ingest **job** (probe, transcript) |
| `POST /sessions` `{video_id}` | start an editing conversation |
| `POST /sessions/{id}/instructions` `{say}` | one instruction → `{status, operations, summary, question, options, reason, warnings, timeline_hash, duration_s}` |
| `POST /sessions/{id}/answer` `{answer}` | answer the pending question ("the second one", "all of them", "2", "cancel") |
| `POST /sessions/{id}/render` | `202` render **job** → `video_url`, `timeline_url`, `otio_url`, manifest, measured A/V offset |
| `GET /sessions/{id}`, `/timeline[?format=otio]` | history, derived timeline (JSON Schema in `schema/`), OpenTimelineIO export |
| `GET /jobs/{id}` | `queued → running → succeeded/failed`, progress 0-100, result links |
| `GET /health`, `/ready` | |

Every POST accepts `Idempotency-Key` (same key + same body → stored response, work never repeated). Jobs may carry `callback_url`, signed `X-IncuBrix-Signature` = HMAC-SHA256(hex) of the body. Optional bearer token (`API_TOKEN`) and per-IP rate limit (`RATE_LIMIT_PER_MIN`).

**Errors** are `{"error":{"code","message","retryable","details"}}`. Codes: `VALIDATION_ERROR 422`, `NOT_FOUND 404`, `UNAUTHORIZED 401`, `RATE_LIMITED 429 (retryable)`, `UNSUPPORTED_MEDIA 415`, `TRANSCRIPT_MISSING 422`, `IDEMPOTENCY_KEY_REUSED 422`, `URL_NOT_ALLOWED 400`, `PAYLOAD_TOO_LARGE 413`, `FETCH_FAILED 502 (retryable)`, `RENDER_FAILED 500`, `CONFIG_INVALID`, `INTERNAL_ERROR 500 (retryable)`. (Edit-level refusals are normal `rejected` results with a `reason`, not HTTP errors.)

## Supported instructions
cut a range / first / last N seconds / "the part about X" / "everything after I say X" · keep only a range · reorder (`move 0:10-0:20 to the end`) · speed up / slow down (0.25x–4x) · caption (on a phrase or a time) · auto-caption · mute / bleep a word · remove filler words (um, uh, and "like" only when used as a filler) · remove silences longer than N s (+ `keep the pause after 'X'`) · background music that ducks under speech · undo · redo · restore a specific earlier edit (`put the intro back`, `restore step 3`, `undo the second-to-last cut`) · restyle a caption (`make that caption red / bigger`).
Multi-part instructions (`cut the first 5 s, speed up 1:00-1:10 by 2x and mute 'darn'`) apply the parts that work and report exactly which part failed.

## Policies (also returned in answers)
* **Time policy:** times mean the **current edited timeline**; say "in the original" for source time. Multi-part instructions resolve against the timeline *before* the instruction.
* **Never guess on destructive edits:** several matches, vague words ("the boring bit", "make it shorter"), low-confidence transcript words, or a repeated identical instruction → a question with options. Cancelling drops the whole instruction.
* **Cuts never clip a word:** a cut/move boundary that lands inside a word moves to the nearest silence and the summary says so (`SNAP_CUTS=false` disables).
* **Overlapping speed changes:** a later `speed` instruction *sets* the factor on its range (it does not multiply), so repeating an instruction is safe.
* **Undo** removes the last instruction; a new edit clears redo; **restore** cancels one earlier instruction (and can itself be undone).
* Captions follow their words; if all their words are cut they hide (warning) and return when the cut is restored; partial cuts show the caption over the surviving words; sped-up captions stay ≥0.7 s.

## Settings (`.env.example` lists all)
`TRANSCRIBER=auto|sidecar|whisper` (auto = use an uploaded `words.json` if given, otherwise transcribe the audio with faster-whisper; model `WHISPER_MODEL`, default `base.en`, downloads on first use) · `NLU=rules|llm` (LLM only emits intents, output is validated, falls back to rules) · `STORAGE=local|s3` · model, paths, limits and secrets all come from environment variables. No secrets are logged or committed.

## Batch / session runner
`python -m app.runner <session.yaml | folder> -o <out> [--api URL] [--token T]` → `<out>/<session>/results.json` (per step: status, operations, summary, question, options, timeline_hash, duration_s; plus final timeline and `final.mp4`). Session format as in the brief (`video`, `steps[].say`, `answer`); `expect` blocks make it a regression test.

## Known limits (honest)
* Whisper (`base.en`, CPU, int8) was verified on one real-sounding clip only (125/125 words found, ~22 s for 61 s of audio on a laptop). Whisper timings are looser than exact timings and it can drop or mishear words; `base.en` is English only. Automated tests/sessions still use the `sidecar` provider with our fixtures (Whisper needs a model download, so it is not in CI).
* Our fixtures are synthetic: video = ffmpeg `testsrc2`, audio = one tone burst per word. They prove timing/sync/word-anchoring exactly, but **no real human speech** was transcribed or edited. Replace/add your own recording before the live session.
* Speakers: taken from your transcript file if it has them; with Whisper they are estimated from voice pitch (two clearly different voices are separated, 124/125 words right on our two-voice clip; similar voices merge into one speaker). Overlapping speech and rapid label flips are warned about, not fixed.
* Topic search ("the part about X") is lexical + stemming + a small synonym table, not embeddings. Unknown synonyms won't match (it says so instead of guessing).
* "the intro" and "the boring bit" are subjective → always a question.
* `docker compose` files were written but **not run** on this machine (no Docker available); the S3 adapter is tested against an in-process fake S3 (moto), the HTTP/render paths against ffmpeg for real.
* Built-in music is a synthetic chord pad (generated by the service); custom music tracks are not supported yet.
