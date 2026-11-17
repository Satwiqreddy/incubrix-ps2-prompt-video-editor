# How edits stay attached to content (one page)

Schema: `schema/timeline.schema.json` (served at `GET /api/v1/timeline-schema`). Example derived timeline: `session_pack/01_worked_example/timeline.json`.

**Stored data (never changes except by appending):**
* `source` — the original file, read-only, with its SHA-256 (re-checked on every `GET /sessions/{id}` → `source_unchanged`).
* `words[]` — transcript with stable ids `{id, text, start, end, speaker, conf}` in **original** time.
* `history[]` — one entry per instruction: `{id, instruction, ops[]}`. Ops are small records:
  `cut{src:[a,b]}`, `speed{src,factor}`, `move{src,to}`, `caption{text, words:[first_id,last_id]}`, `mute/bleep{words}`, `music`, `style{target}`, `protect{word}`, `restore{target_entry}`.
  Every position in an op is **original source time or a word id** — there is no "1:40 in the edited video" anywhere in storage.

**Derived data (recomputed after every instruction by `timeline.fold`):**
1. Start with one segment `[0, duration]` of the original.
2. Fold the *live* history in order: `cut` removes source ranges from the segment list, `speed` splits and sets a factor, `move` re-orders pieces. Entries cancelled by a later `restore` are skipped (so restoring a cut brings back exactly what it removed, while later edits stay).
3. Overlays are projected through the segments: a caption's interval = where its anchor words now sit in the edited timeline. All words cut → hidden (warning); some cut → shown over the survivors; sped up → min 0.7 s.
4. `timeline_hash` = SHA-256 of the canonical derived JSON. Same session ⇒ same hash (checked 3× in-process and across processes with different `PYTHONHASHSEED`: 19/19 sessions identical).

**Why this works:** "at 1:20" is converted to source time *once*, when the instruction is understood (via the current segments). After that the edit is content-addressed, so cutting, speeding up, re-ordering or restoring earlier material can never move it.
**Undo** pops the last entry; **redo** re-pushes it; **restore** appends a `restore` entry (so it is itself undoable).
**Render** reads the original through the derived segments (frame-exact `trim` by frame number, audio by sample), so edited length = derived length within 1 frame.
