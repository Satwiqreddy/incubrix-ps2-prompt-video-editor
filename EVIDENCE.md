# EVIDENCE INDEX — copy into the Excel Submission Form (Yes / Partial / No + proof)

Run first: `python -m tools.fixtures fixtures && python -m pytest -q && python -m app.runner sessions -o session_pack && python -m tools.metrics && python -m tools.bench`

## Must-haves
| # | Status | Proof |
|---|---|---|
| 1 Never damage original | Yes | `tests/test_api.py::test_full_flow_and_original_untouched` (`source_unchanged`), `tests/test_media.py::test_render_never_modifies_source`; every `results.json` has `source_sha256` + `source_unchanged: true` |
| 2 Edits stick to content | Yes | `tests/test_timeline.py`, `tests/test_scenarios.py::test_caption_*`, `tests/test_media.py::test_caption_visible_exactly_on_its_words`; rendered frame at output 12.8 s shows burned-in source time 42.8 (`session_pack/01_worked_example/final.mp4`) |
| 3 Three-way answers | Yes | every step of every `session_pack/*/results.json` (`status`); 77 scenarios |
| 4 Predictable | Yes | `metrics.json` determinism 19/19; code: `timeline.py` has no model call, `nlu.py` only returns intents |
| 5 Render matches timeline | Yes | `metrics.json` render_accuracy (≤0.9 frame A/V, ≤0.9 frame length); `tests/test_media.py` |
| 6 All operations incl. undo/redo/restore | Yes | sessions 01, 06, 17; scenarios `undo_*`, `redo*`, `restore_*` |
| 7 Clear time policy | Yes | every time-based answer says "time policy: current timeline"; "in the original" supported (`04_time_policy_and_formats`) |
| 8 Integration Standard + runner + live demo | Partial | runner: `app/runner.py`, sessions pass. **Live demo: NOT deployed yet (submitter must deploy, see below)** |

## Levels
| Level / item | Status | Proof |
|---|---|---|
| Core: worked example on own video | Yes (synthetic video) | `sessions/01_worked_example.yaml`, `session_pack/01_worked_example/` |
| Core: single + multi-speaker | Yes (sidecar transcript, synthetic) | sessions 01 and 11/12 |
| Adv: captions follow words / partial cuts / 0.7 s min | Yes | `test_timeline.py`, session 02, 15 |
| Adv: ambiguity & references | Yes | session 08, scenarios `shorter_asks`, `vague_*`, `style_*` |
| Adv: conflicts / pauses / multi-part / two speakers | Yes | sessions 05, 09, 11, 17; scenario `multipart_*` |
| Out: cuts snap to silence | Yes | `test_cut_snaps_to_silence_never_clips_a_word`; metrics: 0/140 |
| Out: low-confidence flagged | Yes | session 16, scenario `bleep_low_confidence_asks` |
| Out: VFR / no-audio | Yes | sessions 13, 14; `test_vfr_and_noaudio_render` |
| Out: overlapping speech / label-swap warnings | Yes | `tests/test_transcript_quality.py` (unit + shown to the user on ingest) |
| Out: 60-min < 3 s | Yes | `bench_http_results.json`: a REAL 60-min MP4 through the HTTP API, 30 instructions, median 143 ms, max 254 ms, 30/30 under 3 s (`python -m tools.bench_http 60`); engine-only: `bench_results.json` |
| Out: OpenTimelineIO export | Yes | exported file opened with the real OpenTimelineIO 0.18.1 library (`py -3.12 tools/check_otio.py`): 2 tracks, correct clip ranges, 2x speed as LinearTimeWarp |
| Out: ≥40 scripted scenarios in CI | Yes | `tests/test_scenarios.py` (77 cases), `.github/workflows/ci.yml` (not yet run on GitHub) |

## Integration Standard
1 compose+tests: **Partial** (written, not run here; tests run via `pytest`) · 2 openapi: Yes (`tests/test_api.py::test_openapi_matches_running_app`) · 3 jobs/callback HMAC: Yes (`test_callback_signature`) · 4 idempotency: Yes · 5 errors: Yes (`test_error_shape`) · 6 config: Yes (`.env.example`) · 7 files in/out + S3: Yes (`tests/test_s3.py` fake S3) · 8 logs/health/correlation id: Yes · 9 manifest: Yes (render job result) · 10 batch runner: Yes.

## Metrics tab
`metrics.json`, `bench_results.json`, REPORT.md table. Word-clipping figure is an exact transcript check, **not** a listening test of 20 cuts.

## Speech-to-text
Partial: Whisper `base.en` transcribes uploads without a transcript file (checked manually on `fixtures/real_speech.mp4`: 125 words, 22 s). Not covered by automated tests.

## Hard cases tab
Scenarios in `tests/test_scenarios.py` + sessions 02–08, 11–14, 17 (all 8 hard-case bullets of the brief are covered; two-speaker swap/overlap only as warnings).

## Still to do by the submitter (I could not do these)
1. Fill the Excel form from this file (the template was not provided to me). 2. Private GitHub repo with real commit history, reviewer `techteam@incubrix.com`. 3. Deploy a live demo (any host that runs the Dockerfile; set `API_TOKEN`, keep `RATE_LIMIT_PER_MIN`, upload fixtures or your own video + words.json) and keep it up. 4. Record the ≤8-min unedited demo (`DEMO_SCRIPT.md`). 5. Run `docker compose up` / `docker compose run --rm tests` once on a Docker machine and fix anything that differs. 6. Add your own real talking-head clip + transcript and re-run. 7. Rewrite `AI_USE.md` in your own words. 8. Be ready to explain and change every file in the live session.
