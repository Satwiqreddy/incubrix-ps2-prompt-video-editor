# 8-minute demo video plan (unedited screen recording)
Start from the live URL (web page). Keep the terminal and the page side by side.
1. (0:00) Open the web page, click **Guide**, then `GET /docs` + one sentence: edits belong to content, not clock time. Show `TIMELINE.md` diagram.
2. (0:45) **Clean run** (page: **Watch a demo**, or terminal): `python -m app.runner sessions/01_worked_example.yaml -o results --api <live url>`; open `results.json`; play `final.mp4` — caption on "welcome to the channel", pricing question, undo.
3. (3:00) **Failure handled**: send "speed up 10x" and "cut everything" (rejected with reasons), "remove 0:30-0:40" twice (asks), `make it bigger` with no caption.
4. (4:15) **Tests running**: `pytest -q tests/test_scenarios.py` then `python -m tools.metrics`.
5. (5:30) **Design decision in code**: open `app/timeline.py::fold` — show that ops hold source time/word ids; change nothing, explain restore = skipping a cancelled entry, `thash` for determinism.
6. (7:15) Show `session_pack/…/results.json` hashes identical across reruns and the 1-frame render check.
