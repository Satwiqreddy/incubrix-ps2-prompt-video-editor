# REPORT — Prompt-Based Video Editing Engine

## Approaches considered
1. **LLM edits the timeline directly** — rejected: non-deterministic, can't be tested, and the brief forbids it.
2. **Store edits as clock positions and re-shift later edits after each cut** — rejected: every operation (cut, speed, reorder, restore) needs shifting logic for every other stored edit; bugs compound and *restore* becomes ambiguous.
3. **Chosen: content-anchored history + derived timeline.** Edits are stored against original time/word ids; the edited timeline is a pure function of `(original, live history)`. Cut/restore/undo then reduce to list operations and "an edit sticks to its words" holds by construction. Language understanding is isolated in `nlu.py` (rules by default; an LLM may only emit validated intents).

## What works (all verified by `pytest` / `app.runner`, outputs in `session_pack/`)
* 8 must-haves: original untouched (SHA-256 re-checked per session) · edits stick to content (unit + render tests, burned-in source timestamp visible on rendered frames) · three-way answers · determinism · render matches timeline · all operations incl. undo/redo/restore · stated time policy · API/runner.
* Core worked example reproduced exactly (`sessions/01_worked_example.yaml`): caption 0:12–0:14 after the 30 s cut (4 words, 42.0–43.78 s original), pricing asks "which one", intro restored with caption back at 0:42 and cuts kept, undo reverses step 5 only.
* Advanced items: caption/word rules, ambiguity questions (`that caption`, `the boring bit`, `make it shorter`, repeated matches, repeated identical instruction), conflicts (impossible, whole-video cut, protected pauses), multi-part partial application, two-speaker lead-in question.
* Outstanding items: cuts snap to silence, low-confidence words are asked about, VFR → CFR 30 fps with a warning, no-audio videos work/fail clearly, 60-min latency, OpenTimelineIO export, 126-test suite incl. 77 scripted scenarios.

## Measured numbers (this laptop, Windows 11, no GPU; `tools/bench.py`, `tools/metrics.py`)
| Metric | Result |
|---|---|
| Instruction latency, 5-min transcript (625 words, engine only, 13 instruction types ×3) | median 5.6 ms, p95 18 ms, max 20 ms |
| Instruction latency through HTTP on a real 60-min MP4 (`tools/bench_http.py`, 30 instructions) | median 143 ms, p95 188 ms, max 254 ms; 30/30 under 3 s |
| Instruction latency, 60-min transcript (7,426 words, same mix) | median 67 ms, p95 105 ms, max 107 ms (target < 3 s). Excludes transcription/render and HTTP overhead; the HTTP test asserts < 3 s |
| Render accuracy over 19 renders | max A/V offset 0.9 frame, max length difference 0.9 frame (limit 1) |
| Audio follows words | tone onsets in rendered audio match timeline projection within 0.05 s (`tests/test_media.py`) |
| Determinism | 19/19 scripted sessions give identical `timeline_hash` sequences over 3 in-process runs + 2 processes with different hash seeds |
| Word clipping | 140 cut/move boundaries produced by the session pack, 0 inside a word (exact transcript check, not a listening test) |
| Clarification quality | 12 questions asked in the pack; 0 guesses on destructive edits (assumption path — speed factor, silence length — covered by scenario tests, not by the pack) |
| Cost | $0.00/min: rules NLU, sidecar transcript, local ffmpeg. With `whisper` locally also $0; an LLM translator would be ≈ one short call per instruction (not measured) |
| Render time | ≈ 6 s wall for a 92 s output at 640×360 (veryfast x264, includes upload/probe in the runner) |
| Server capacity | `WORKERS=2` render threads by default; not load-tested beyond that |

## What does NOT work / not verified (be strict)
* No real human speech/face was used: fixtures are synthetic (tone bursts + testsrc2). Whisper (`base.en`) transcription was checked on one clip of computer-voiced speech only (125/125 words, 22 s for 61 s of audio on CPU); it is not in the automated tests. Transcript errors are handled by confidence flags (low-confidence words are asked about) and fuzzy phrase matching.
* Speakers: from the transcript file, or (with Whisper) estimated from voice pitch: 124/125 words right on a two-voice clip, a one-voice clip is not split. Voices of similar pitch merge. Overlap/label-swap detection warns (unit tested) but does not repair.
* Topic search is lexical (+stemming + small synonym table), so paraphrases outside the table are "not found".
* `docker compose` untested here (no Docker). The live demo URL, GitHub repo, demo video and Submission Form still have to be produced by the submitter.
* Overlapping speed changes: a later `speed` on a range *sets* (not multiplies) the factor for the overlapped part; documented rule, little tested.
* OTIO export: opened successfully with OpenTimelineIO 0.18.1 (tracks, clip ranges, speed effect); not opened in a desktop editor.
* 60-minute: a real 60-minute MP4 (160x90, silent noise) with a synthetic transcript was edited through the HTTP API (median 143 ms, max 254 ms, 30/30 < 3 s); it was not rendered, and rendering time for 60 minutes was not measured.

## Found and fixed during final checks
* Render dropped the audio of later segments for 16 kHz mono sources (ffmpeg's implicit stream fan-out); fixed with explicit `split`/`asplit`, covered by `tests/test_media.py::test_16khz_mono_source_keeps_all_audio`, and the session runner now fails any render outside the 1-frame tolerance.
* Cut/move boundaries inside a word were not snapped for `move`; now they are (0 of 140 boundaries clip a word).

## Next
Real-speech fixtures + whisper test; diarization adapter; embedding-based topic search; custom music upload; per-segment render cache for faster re-renders; load test and render queue limits; validate OTIO in Resolve/OTIO.
