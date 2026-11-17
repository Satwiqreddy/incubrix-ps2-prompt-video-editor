# AI_USE

* **Claude Code (Anthropic, Claude Sonnet 5.5)** was used as a coding assistant for: the initial architecture suggestion (word-anchored history + derived timeline), generating the first drafts of `timeline.py`, `nlu.py`, `engine.py`, `media.py`, `main.py`, `runner.py`, the tests, fixtures and these documents, and for debugging (e.g. ffmpeg filter expressions, performance of bisect-based time mapping, snapping boundaries to silence).
* All code was run, tested and reviewed by the submitter, who must be able to explain and change it in the live session.
* **No AI model runs in the default product path.** Language is turned into intents by deterministic rules (`NLU=rules`). The optional LLM translator (`NLU=llm`) can only output validated intent JSON; it never edits the timeline.
* Test-media: no AI-generated or third-party media. The built-in music pad is synthesised by ffmpeg and labelled synthetic in the manifest.
* The web page (`app/static/index.html`), the guided demo and the git commit messages were also drafted with Claude Code and reviewed by the submitter.
* All reported numbers come from the scripts in this repo (`tools/bench.py`, `tools/metrics.py`, `pytest`, `app.runner`); nothing is hand-edited.

<!-- Submitter: edit this file to describe in your own words which parts you wrote or changed yourself. -->
