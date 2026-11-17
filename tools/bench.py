"""Instruction latency (engine + timeline, excluding transcription and render) on 5-min and 60-min transcripts.
python -m tools.bench  -> writes bench_results.json"""
import json, random, statistics, sys, time
from app import engine as E
from app.transcript import build

VOC = "pricing cost plan customers growth small business coffee laptop planning feedback weekly honest simple fee profit money time tools".split()


def synth(minutes, seed=7):
    rnd = random.Random(seed)
    w, t = [], 0.0
    while t < minutes * 60 - 8:
        n = rnd.randint(8, 16)
        for i in range(n):
            tok = rnd.choice(VOC + ["um", "uh", "the", "and", "like", "so", "we"]) + ("." if i == n - 1 else "")
            w.append({"text": tok, "start": round(t, 3), "end": round(t + 0.32, 3), "speaker": "A", "conf": 0.95})
            t += 0.38
        t += rnd.choice([0.4, 0.8, 1.5, 2.5])
    return build(w), t


def bench(minutes):
    words, last = synth(minutes)
    dur = minutes * 60.0
    v = {"words": words, "source": {"duration_s": dur, "fps": 30.0, "has_audio": True}}
    s = E.new_session("b", "v")
    cases = [("cut range", "cut 2:00-2:10"), ("cut first", "cut the first 5 seconds"), ("speed", "speed up from 1:00 to 1:30 by 2x"),
             ("move", "move 3:00-3:10 to the end"), ("caption phrase", "add a caption 'Hi' when I say growth plan"),
             ("mute word", "mute the word 'coffee'"), ("topic (asks)", "remove the part about pricing"), ("fillers", "remove filler words"),
             ("silences", "remove silences longer than 1.5 seconds"), ("autocaption", "auto-caption the whole video"), ("music", "add background music"),
             ("undo", "undo"), ("restore", "put back what I removed")]
    rows = {}
    for rep in range(3):
        for name, say in cases:
            t0 = time.perf_counter()
            r = E.handle(s, v, say)
            ms = (time.perf_counter() - t0) * 1000
            rows.setdefault(name, []).append(ms)
            assert r["status"] in ("applied", "needs_clarification", "rejected")
    allms = [x for xs in rows.values() for x in xs]
    return {"minutes": minutes, "words": len(words), "history_entries": len(s["entries"]),
            "median_ms": round(statistics.median(allms), 1), "p95_ms": round(sorted(allms)[int(.95 * len(allms))], 1), "max_ms": round(max(allms), 1),
            "slowest": sorted(((round(max(x), 1), k) for k, x in rows.items()), reverse=True)[:3]}


if __name__ == "__main__":
    out = [bench(5), bench(60)]
    json.dump(out, open("bench_results.json", "w"), indent=1)
    for o in out:
        print(o)
