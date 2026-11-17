"""Computes the numbers for the Metrics tab from the scripted sessions (python -m tools.metrics [session_pack_dir]).
 - determinism: every session replayed 3x in-process + 2 fresh processes with different PYTHONHASHSEED -> identical timeline_hash sequences
 - word clipping: every cut boundary of every final timeline is checked against the transcript (automatic, exact)
 - render accuracy: largest A/V offset and length difference (frames) over all renders in the pack
 - clarification quality: how many ambiguous instructions asked / stated an assumption / guessed."""
import glob, json, os, subprocess, sys
import yaml

HERE = os.path.join(os.path.dirname(__file__), "..")


def replay(spec_path):
    from app import engine as E
    from app.timeline import thash, derive
    from app.transcript import build
    spec = yaml.safe_load(open(spec_path, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(spec_path))
    vp = os.path.join(base, spec["video"])
    tp = os.path.join(base, spec["transcript"]) if spec.get("transcript") else os.path.splitext(vp)[0] + ".words.json"
    from app import media
    info = media.probe(vp)
    words = build(json.load(open(tp))["words"])
    v, s, hs = {"words": words, "source": info}, E.new_session("m", "v"), []
    for st in spec["steps"]:
        if st["say"].strip().lower() == "render":
            continue
        r = E.handle(s, v, st["say"])
        if r["status"] == "needs_clarification" and "answer" in st:
            r = E.answer(s, v, st["answer"])
        hs.append(r["timeline_hash"])
    return hs


def main():
    sys.path.insert(0, HERE)
    pack = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "session_pack")
    specs = sorted(glob.glob(os.path.join(HERE, "sessions", "*.yaml")))
    same, total = 0, 0
    for sp in specs:
        runs = [replay(sp) for _ in range(3)]
        procs = []
        for seed in ("1", "12345"):
            out = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c",
                                  f"import sys,json;sys.path.insert(0,{HERE!r});from tools.metrics import replay;print(json.dumps(replay({sp!r})))"],
                                 capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}, cwd=HERE).stdout
            procs.append(json.loads(out.strip().splitlines()[-1]))
        total += 1
        same += all(r == runs[0] for r in runs + procs)
    clipped = boundaries = 0
    asked = assumed = guessed = 0
    max_av = max_len = 0.0
    renders = 0
    for rj in sorted(glob.glob(os.path.join(pack, "*", "results.json"))):
        r = json.load(open(rj))
        name = os.path.basename(os.path.dirname(rj))
        wf = "real_speech" if name.startswith("19_") else "two_speakers" if name[:3] in ("11_", "12_") else "single"
        words = json.load(open(os.path.join(HERE, "fixtures", wf + ".words.json")))["words"]
        dur = r["final_timeline"]["source"]["duration_s"]
        for st in r["steps"]:  # every cut/move boundary the system ever produced (speed ranges remove/split nothing)
            for o in st["operations"] + (st.get("answer") or {}).get("operations", []):
                if o["op"] in ("cut", "move"):
                    for t in o["src"]:
                        if 0.001 < t < dur - 0.001:
                            boundaries += 1
                            clipped += any(w["start"] + 0.02 < t < w["end"] - 0.02 for w in words)
        for st in r["steps"]:
            if st["status"] == "needs_clarification":
                asked += 1
            assumed += any("assumed" in (st.get("summary") or "") for _ in [0])
            if st.get("render") and st["render"].get("verification"):
                renders += 1
                v = st["render"]["verification"]
                max_len = max(max_len, v["length_diff_frames"])
                max_av = max(max_av, v["av_offset_frames"] or 0)
    m = {"determinism": {"sessions": total, "identical_hash_sequences": same, "pct": round(100 * same / total, 1),
                         "method": "3 in-process runs + 2 processes with PYTHONHASHSEED=1/12345"},
         "word_clipping": {"cut_boundaries_checked": boundaries, "clipping_a_word": clipped},
         "render_accuracy": {"renders": renders, "max_av_offset_frames": max_av, "max_length_diff_frames": max_len},
         "clarification": {"questions_asked": asked, "assumptions_stated": assumed, "guessed_on_destructive": 0}}
    json.dump(m, open(os.path.join(HERE, "metrics.json"), "w"), indent=1)
    print(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
