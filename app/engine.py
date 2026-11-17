"""Instruction -> operations -> history. Ordinary deterministic code; no model is called here.

Every instruction ends in exactly one of: applied | needs_clarification | rejected.
Time policy (stated in every time-based answer): times in an instruction mean the CURRENT edited
timeline, unless the user says "original". All parts of one instruction are resolved against the
timeline as it was BEFORE that instruction."""
import copy, difflib, re
from bisect import bisect_left, bisect_right
from . import nlu
from .config import cfg
from .timeline import derive, thash, fold, layout, total, project, to_source, live_entries
from .transcript import norm, sentences

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "mm", "ah"}
NOT_FILLER_PREV = {"would", "i", "you", "we", "they", "he", "she", "feel", "feels", "look", "looks", "looked", "sound",
                   "sounds", "taste", "tastes", "just", "exactly", "more", "much", "not", "don't", "doesn't", "didn't",
                   "love", "loved", "to", "or", "and", "and,", "very", "so", "pretty", "kind", "things", "something"}
STOP = set("the a an of to in on and or for with their his her my our your about that this is are was were it its be at as by".split())
SYN = {"pricing": {"price", "prices", "cost", "costs", "pay", "paying", "fee", "fees", "subscription", "dollars", "plan", "plans", "pricing"},
       "price": {"pricing", "cost", "costs", "fee", "fees", "dollars"}, "cost": {"price", "pricing", "fee", "fees", "dollars"},
       "job": {"work", "worked", "working", "career", "employer", "company", "position", "role", "job"},
       "intro": {"welcome", "hello", "hi"}, "sponsor": {"sponsor", "sponsored", "ad", "advert", "promo", "code"}}
MAX_OPTIONS = 8
MULTI_ALL = {"cut", "caption"}      # intents that offer "all of them" when several matches
SINGLE_ONLY = {"keep", "move"}      # must pick one


class Reject(Exception):
    pass


def fmt(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


def stem(w):
    w = norm(w)
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[:-len(suf)]
    return w


class Ctx:
    def __init__(s, session, video):
        s.session, s.video = session, video
        s.words, s.src = video["words"], video["source"]
        s.dur = s.src["duration_s"]
        s.fps = s.src.get("fps") or 30.0
        s.st = fold(session["entries"], s.words, s.dur)
        s.L = layout(s.st["segs"])
        s.cur = total(s.L)
        s.sents = sentences(s.words)
        s.toks = [norm(w["text"]) for w in s.words]
        s.starts = [w["start"] for w in s.words]
        cov = sorted((a, b) for a, b, _ in s.st["segs"])
        starts = [a for a, _ in cov]
        s.live = {}
        for w in s.words:  # coverage is disjoint+sorted: only the last segment starting before the word's end can overlap it
            i = bisect_left(starts, w["end"] - 1e-6) - 1
            s.live[w["id"]] = i >= 0 and cov[i][1] > w["start"] + 1e-6
        s.order = sorted(range(len(s.words)), key=lambda i: s.words[i]["start"] + s.words[i]["end"])
        s.mids = [(s.words[i]["start"] + s.words[i]["end"]) / 2 for i in s.order]
        s.spk = []
        for w in s.words:
            if w["speaker"] not in s.spk:
                s.spk.append(w["speaker"])

    # ---- helpers ----
    def ff(s, t):
        return min(s.dur, max(0.0, round(t * s.fps) / s.fps))

    def snap(s, t, notes):
        """move a cut boundary that lands inside a word to the nearest silence (never clip a word)."""
        t = s.ff(t)
        if not cfg.snap:
            return t
        j = bisect_right(s.starts, t) - 1
        for k in (j, j - 1):  # the word starting last before t (and its predecessor, for overlapping speech)
            if k < 0:
                continue
            w = s.words[k]
            if w["start"] + 0.02 < t < w["end"] - 0.02:
                prev_end = s.words[k - 1]["end"] if k else 0.0
                nxt = s.words[k + 1]["start"] if k + 1 < len(s.words) else s.dur
                cands = [(prev_end + w["start"]) / 2, (w["end"] + nxt) / 2]
                new = s.ff(min(cands, key=lambda c: abs(c - t)))
                notes.append(f"boundary {fmt(t)} was inside the word '{w['text']}'; moved to {fmt(new)} (nearest silence)")
                return new
        return t

    def words_in(s, ranges, live=True):
        ids = set()
        for a, b in ranges:
            ids.update(s.order[bisect_left(s.mids, a - 1e-6):bisect_right(s.mids, b + 1e-6)])
        return sorted(i for i in ids if not live or s.live[i])

    def text(s, ranges, n=60):
        t = " ".join(s.words[i]["text"] for i in s.words_in(ranges, live=False))
        return t if len(t) <= n else t[:n - 1] + "…"

    def curspan(s, ranges):
        iv = [p for a, b in ranges for p in project(s.L, a, b)]
        return (min(x for x, _ in iv), max(y for _, y in iv)) if iv else None

    def cand(s, ranges, words=None, label=None):
        cs = s.curspan(ranges)
        if label is None:
            label = (f"{fmt(cs[0])}–{fmt(cs[1])}" if cs else "removed text") + f" “{s.text(ranges)}”"
        ids = words or (lambda w: [w[0], w[-1]] if w else None)(s.words_in(ranges))
        return {"label": label, "src": [tuple(r) for r in ranges], "words": ids, "cur": cs}

    def phrase_hits(s, phrase, include_cut=False):
        pt = [norm(x) for x in phrase.split() if norm(x)]
        if not pt:
            return []
        n, hits = len(pt), []
        for i in range(len(s.toks) - n + 1):
            if all(s.toks[i + k] == pt[k] for k in range(n)):
                hits.append((i, i + n - 1))
        if not hits:  # ASR makes mistakes: fuzzy token match (documented, flagged)
            for i in range(len(s.toks) - n + 1):
                if all(difflib.SequenceMatcher(None, s.toks[i + k], pt[k]).ratio() >= 0.8 for k in range(n)):
                    hits.append((i, i + n - 1))
        return [h for h in hits if include_cut or s.live[h[0]] or s.live[h[1]]]

    def topic_hits(s, query, speaker=None, include_cut=False):
        q = {stem(x) for x in query.split() if norm(x) not in STOP and norm(x)}
        exp = set(q)
        for x in list(q):
            exp |= {stem(y) for y in SYN.get(x, SYN.get(x + "s", set()))}
        need = max(1, (len(q) + 1) // 2)
        spk = None
        if speaker:
            ix = {"host": 0, "interviewer": 0, "guest": 1, "interviewee": 1}.get(speaker)
            if ix is None:
                ix = int(speaker.split()[-1]) - 1
            spk = s.spk[ix] if ix < len(s.spk) else None
            if spk is None:
                raise Reject(f"there is no '{speaker}' in this video (speakers found: {s.spk})")
        hits = []
        for k, se in enumerate(s.sents):
            if spk and se["speaker"] != spk:
                continue
            toks = {stem(x) for x in se["text"].split()}
            core = len(toks & q) if q & toks else 0
            if (len(toks & exp) >= need and (core >= 1 or len(toks & exp) >= need)) and (
                    include_cut or any(s.live[i] for i in se["ids"])):
                hits.append(k)
        groups = []
        for k in hits:  # merge adjacent matching sentences
            if groups and k == groups[-1][-1] + 1:
                groups[-1].append(k)
            else:
                groups.append([k])
        return groups, spk, q


def clock_range(a, b, ctx, policy, what):
    if a is None or b is None or b <= a:
        raise Reject(f"I can't read that time range ({what})")
    if policy == "original":
        if a >= ctx.dur - 0.05:
            raise Reject(f"out of range: original video is {fmt(ctx.dur)} long")
        return [(a, min(b, ctx.dur))]
    if a >= ctx.cur - 0.05 or b > ctx.cur + 0.5:
        raise Reject(f"out of range: the current video is {fmt(ctx.cur)} long, but you asked for {fmt(a)}–{fmt(b)}")
    r = to_source(ctx.L, a, min(b, ctx.cur))
    if not r:
        raise Reject("nothing at that time")
    return r


def candidates(where, ctx, intent, include_cut=False):
    """-> ('cands', [cand]) | ('ask', question, [(label, cand)])  ; raises Reject."""
    if not where:
        raise Reject("no target given")
    k = where["kind"]
    pol = where.get("policy", "current")
    if k == "all":
        return "cands", [ctx.cand([(0.0, ctx.dur)] if include_cut else to_source(ctx.L, 0, ctx.cur))]
    if k in ("first", "last"):
        n = where["n"]
        if n is None or n <= 0:
            raise Reject("I can't read that duration")
        if n > ctx.cur + 0.05:
            raise Reject(f"out of range: the current video is only {fmt(ctx.cur)} long")
        a, b = (0, n) if k == "first" else (ctx.cur - n, ctx.cur)
        return "cands", [ctx.cand(clock_range(a, b, ctx, "original" if include_cut else "current", k))]
    if k == "range":
        return "cands", [ctx.cand(clock_range(where["a"], where["b"], ctx, "original" if include_cut else pol, "range"))]
    if k == "point":
        t = where["t"]
        r = clock_range(t, t + 3.0, ctx, pol, "point") if pol == "current" and t + 3 <= ctx.cur else \
            clock_range(t, max(t + 0.1, ctx.cur if pol == "current" else ctx.dur), ctx, pol, "point")
        return "cands", [ctx.cand(r)]
    if k in ("phrase", "after_phrase", "before_phrase"):
        hits = ctx.phrase_hits(where["phrase"], include_cut)
        if not hits:
            raise Reject(f"I can't find “{where['phrase']}” in the transcript" + (" (it may already be cut)" if not include_cut else ""))
        out = []
        for i, j in hits:
            if k == "phrase":
                out.append(ctx.cand([(ctx.words[i]["start"], ctx.words[j]["end"])], [i, j]))
            elif k == "after_phrase":
                e = ctx.words[j]["end"]
                if e >= ctx.dur - 0.05 or not to_source(ctx.L, 0, ctx.cur):
                    continue
                out.append(ctx.cand([(e, ctx.dur)], None, f"after “{where['phrase']}” at {fmt((ctx.curspan([(ctx.words[i]['start'], e)]) or (0, 0))[1])}"))
            else:
                out.append(ctx.cand([(0.0, ctx.words[i]["start"])], None,
                                    f"before “{where['phrase']}” at {fmt((ctx.curspan([(ctx.words[i]['start'], ctx.words[j]['end'])]) or (0, 0))[0])}"))
        if not out:
            raise Reject("nothing comes after that")
        return "cands", out
    if k == "topic":
        groups, spk, qset = ctx.topic_hits(where["query"], where.get("speaker"), include_cut)
        if not groups:
            raise Reject(f"I can't find anything about “{where['query']}”" + (f" said by the {where['speaker']}" if where.get("speaker") else ""))
        out = []
        for g in groups:
            r = [(ctx.sents[g[0]]["start"], ctx.sents[g[-1]]["end"])]
            c = ctx.cand(r)
            c["lead"] = g[0]  # sentence index, used for the "host lead-in" question
            c["literal"] = any(stem(x) in qset for k in g for x in ctx.sents[k]["text"].split())  # False = found by meaning only
            c["spk"] = spk
            out.append(c)
        return "cands", out
    if k == "sentence":
        live = [se for se in ctx.sents if any(ctx.live[i] for i in se["ids"])]
        n = where["n"]
        if not live or (n > 0 and n > len(live)):
            raise Reject("there is no such sentence in the current video")
        se = live[n - 1] if n > 0 else live[-1]
        return "cands", [ctx.cand([(se["start"], se["end"])], [se["ids"][0], se["ids"][-1]])]
    if k == "intro":
        opts = []
        for lab, n in (("the first sentence", None), ("the first 10 seconds", 10.0), ("the first 30 seconds", 30.0)):
            if n is None:
                r = [(ctx.sents[0]["start"] if False else 0.0, ctx.sents[0]["end"])] if ctx.sents else []
            elif n < ctx.cur:
                r = clock_range(0, n, ctx, "original" if include_cut else "current", "intro")
            else:
                continue
            if r:
                opts.append((f"{lab} ({fmt(ctx.curspan(r)[1] if ctx.curspan(r) else 0)} long)" if False else lab, ctx.cand(r)))
        return "ask", "How long is “the intro”? I can't tell from the audio alone.", opts
    if k == "vague":
        opts = []
        gaps = [(b["start"] - a["end"], a["end"], b["start"]) for a, b in zip(ctx.words, ctx.words[1:]) if b["start"] > a["end"]]
        if gaps:
            g = max(gaps)
            opts.append(("the longest pause", ctx.cand([(g[1], g[2])])))
        sl = [se for se in ctx.sents if len(se["ids"]) >= 4 and any(ctx.live[i] for i in se["ids"])]
        if sl:
            se = min(sl, key=lambda x: len(x["ids"]) / max(0.1, x["end"] - x["start"]))
            opts.append(("the slowest-paced sentence", ctx.cand([(se["start"], se["end"])])))
        return "ask", f"“{where['text']}” is subjective. Which part do you mean? (or tell me a time range or topic)", opts
    raise Reject(f"unsupported target ({k})")


def cut_ops(cand, ctx, notes):
    out = []
    live = [(max(a, g["a"]), min(b, g["b"])) for a, b in cand["src"] for g in ctx.L if min(b, g["b"]) > max(a, g["a"]) + 1e-6]
    for a, b in live:  # only what is still in the video (already-removed spans are not cut again)
        a2, b2 = ctx.snap(a, notes) if a > 0 else 0.0, ctx.snap(b, notes) if b < ctx.dur else ctx.dur
        if b2 - a2 > 0.5 / ctx.fps:
            out.append({"op": "cut", "src": [round(a2, 4), round(b2, 4)]})
    return out


def snap_range(ctx, a, b, notes):
    """Boundaries of a move/speed range never fall inside a word (a word must not be split across two places)."""
    return (ctx.snap(a, notes) if a > 0 else 0.0), (ctx.snap(b, notes) if b < ctx.dur else ctx.dur)


def build(intent, part, cand, ctx, notes):
    if intent == "cut":
        ops = cut_ops(cand, ctx, notes)
        if not ops:
            raise Reject("that range is already removed or empty")
        return ops
    if intent == "keep":
        cs = cand["cur"]
        keep = [(0, cs[0]), (cs[1], ctx.cur)]
        ops = []
        for a, b in keep:
            if b - a > 0.01:
                for r in to_source(ctx.L, a, b):
                    ops += cut_ops(ctx.cand([r]), ctx, notes)
        return ops
    if intent == "move":
        rs = [snap_range(ctx, a, b, notes) for a, b in cand["src"]]
        return [{"op": "move", "src": list(r), "to": part["to"]} for r in (rs if part["to"] == "end" else rs[::-1]) if r[1] > r[0]]
    if intent == "speed":
        # speed boundaries stay exactly where the user put them (frame-snapped): a rate change never removes or splits content
        return [{"op": "speed", "src": [ctx.ff(a), ctx.ff(b)], "factor": part["_f"]} for a, b in cand["src"]]
    if intent == "caption":
        ids = cand["words"]
        if not ids:
            raise Reject("there are no spoken words there to attach a caption to (captions follow words)")
        notes.append(f"caption “{part['text']}” attached to {ids[1] - ids[0] + 1} word(s) “{ctx.text(cand['src'], 40)}” "
                     f"(original time {fmt(ctx.words[ids[0]]['start'])}–{fmt(ctx.words[ids[1]]['end'])})")
        return [{"op": "caption", "text": part["text"], "words": ids}]
    if intent in ("mute", "bleep"):
        if not ctx.src.get("has_audio", True):
            raise Reject("this video has no audio track to mute or bleep")
        if not cand["words"]:
            raise Reject("no spoken words at that position")
        return [{"op": intent, "words": cand["words"]}]
    if intent == "protect":
        if not cand["words"]:
            raise Reject("no spoken word there")
        return [{"op": "protect", "word": cand["words"][1]}]
    raise Reject("unsupported")


def resolve_part(part, ctx, notes):
    """-> ('ops', ops) | ('ask', q, [(label, ops)]) ; raises Reject"""
    it = part["intent"]
    S = ctx.session
    if it == "unknown":
        raise Reject("I didn't recognise that as an edit. Try: cut / keep / move / speed up / slow down / caption / mute / bleep / "
                     "remove filler words / remove silences / add background music / undo / redo / restore.")
    if it in ("undo", "redo", "render"):
        return "special", it
    if it == "autocaption":
        for e in live_entries(S["entries"]):
            if any(o["op"] == "autocaption" for o in e["ops"]):
                raise Reject(f"the whole video is already captioned (step {e['id']})")
        return "ops", [{"op": "autocaption"}]
    if it == "music":
        return "ops", [{"op": "music", "gain_db": -26 if part.get("quiet") else -20, "duck_db": -12}]
    if it == "fillers":
        ids = []
        for k, w in enumerate(ctx.words):
            t = ctx.toks[k]
            if not ctx.live[k]:
                continue
            if t in FILLERS:
                ids.append(k)
            elif t == "like":
                pg = w["start"] - ctx.words[k - 1]["end"] if k else 1
                ng = ctx.words[k + 1]["start"] - w["end"] if k + 1 < len(ctx.words) else 1
                prev = ctx.toks[k - 1] if k else ""
                if (pg >= 0.2 or ng >= 0.2 or w["text"].endswith(",")) and prev not in NOT_FILLER_PREV:
                    ids.append(k)
        guarded = {i for g in ctx.st["mutes"] + ctx.st["bleeps"] for i in range(g[0], g[1] + 1)}
        skip = [i for i in ids if i in guarded]
        ids = [i for i in ids if i not in guarded]
        for i in skip:
            notes.append(f"kept '{ctx.words[i]['text']}' at {fmt((ctx.curspan([(ctx.words[i]['start'], ctx.words[i]['end'])]) or (0, 0))[0])}: it is muted/bleeped")
        if not ids:
            raise Reject("no filler words found")
        ops = []
        for i in ids:
            a = (ctx.words[i - 1]["end"] + ctx.words[i]["start"]) / 2 if i else 0.0
            b = (ctx.words[i]["end"] + ctx.words[i + 1]["start"]) / 2 if i + 1 < len(ctx.words) else ctx.dur
            ops.append({"op": "cut", "src": [round(ctx.ff(a), 4), round(ctx.ff(b), 4)]})
        notes.append(f"removed {len(ids)} filler words: " + ", ".join(sorted({ctx.words[i]['text'].strip(',.') for i in ids})))
        return "ops", ops
    if it == "silences":
        m = part["min_s"]
        if not m or m <= 0:
            raise Reject("I can't read the silence length")
        if part.get("default"):
            notes.append("no length given: assumed 1.0 s")
        ops, pad = [], 0.1
        edges = [(0.0, ctx.words[0]["start"], None)] + [(a["end"], b["start"], a["id"]) for a, b in zip(ctx.words, ctx.words[1:])] + \
                [(ctx.words[-1]["end"], ctx.dur, ctx.words[-1]["id"])]
        for a, b, wid in edges:
            if b - a > m and wid not in ctx.st["protects"]:
                x, y = ctx.ff(a + pad), ctx.ff(b - pad)
                if y - x > 0.5 / ctx.fps:
                    ops.append({"op": "cut", "src": [round(x, 4), round(y, 4)]})
        kept = [wid for a, b, wid in edges if b - a > m and wid in ctx.st["protects"]]
        if kept:
            notes.append(f"kept {len(kept)} protected pause(s)")
        if not ops:
            raise Reject(f"no silences longer than {m:g} s")
        return "ops", ops
    if it == "restore":
        return "special", ("restore", part["ref"])
    if it == "style":
        caps = [c for c in derive(S["entries"], ctx.words, ctx.dur)["captions"] if not c["hidden"]]
        if not caps:
            raise Reject("there is no caption to change (add one first)")
        props = dict(part["props"])
        base = {"op": "style", "props": props}
        if part.get("scale"):
            base["scale"] = part["scale"]
        if len(caps) == 1:
            return "ops", [{**base, "target": caps[0]["id"]}]
        opts = [(f"{fmt(c['intervals'][0][0])} “{c['text']}”", [{**base, "target": c["id"]}]) for c in caps[-5:]]
        opts.append(("all captions", [{**base, "target": c["id"]} for c in caps]))
        return "ask", f"There are {len(caps)} captions. Which one do you mean by “that caption”?", opts
    if it == "shorter":
        opts = []
        for lab, p in (("remove silences longer than 1 s", {"intent": "silences", "min_s": 1.0}),
                       ("remove filler words (um, uh, filler 'like')", {"intent": "fillers"}),
                       ("speed the whole video up 1.25x", {"intent": "speed", "factor": 1.25, "where": {"kind": "all"}})):
            try:
                n2 = []
                r = resolve_part(p, ctx, n2)
                opts.append((lab, r[1]))
            except Reject:
                pass
        if not opts:
            raise Reject("nothing obvious to shorten; give me a time range or topic to remove")
        return "ask", "“Shorter” can mean several things. Which do you want?", opts

    # ---- target-based intents ----
    if it == "speed":
        f = part.get("factor") or part["default"]
        if part.get("assumed"):
            notes.append(f"no factor given: assumed {f:g}x")
        if not (cfg.min_speed <= f <= cfg.max_speed):
            raise Reject(f"speed {f:g}x is outside the supported range {cfg.min_speed:g}x–{cfg.max_speed:g}x")
        part = {**part, "_f": f}
        if not part.get("where"):
            c = ctx.cand(to_source(ctx.L, 0, ctx.cur))
            return "ask", "Speed change what? You didn't say which part.", [("the whole video", build("speed", part, c, ctx, notes)), ("cancel", [])]
    where = part.get("where")
    if it in ("caption",) and not where:
        c1 = ctx.cand([(ctx.sents[0]["start"], ctx.sents[0]["end"])])
        c2 = ctx.cand([(ctx.sents[-1]["start"], ctx.sents[-1]["end"])])
        return "ask", "Where should the caption go? You didn't say which words.", [
            ("on the first sentence", build("caption", part, c1, ctx, notes)),
            ("on the last sentence", build("caption", part, c2, ctx, notes))]
    if it == "cut" and where and where["kind"] == "all":
        raise Reject("that would remove the whole video; nothing would be left")
    prev = S["entries"][-1] if S["entries"] else None
    inc = it == "restore"
    r = candidates(where, ctx, it, inc)
    if r[0] == "ask":
        opts = []
        for lab, c in r[2]:
            try:
                opts.append((lab, build(it, part, c, ctx, notes)))
            except Reject:
                pass
        return "ask", r[1], opts
    cs = r[1]
    if it in ("mute", "bleep"):
        low = [c for c in cs if c["words"] and any(ctx.words[i]["conf"] < cfg.low_conf for i in range(c["words"][0], c["words"][1] + 1))]
        if low:
            good = [c for c in cs if c not in low]
            lab = ", ".join(f"{fmt(c['cur'][0])} (conf {min(ctx.words[i]['conf'] for i in range(c['words'][0], c['words'][1] + 1)):.2f})" for c in low)
            return "ask", f"Low-confidence transcription at {lab}. I may have the wrong word; {it} anyway?", [
                (f"{it} only the confident matches", sum((build(it, part, c, ctx, notes) for c in good), [])),
                (f"{it} all, including low-confidence", sum((build(it, part, c, ctx, notes) for c in cs), []))]
        notes.append(f"{'muted' if it == 'mute' else 'bleeped'} {len(cs)} occurrence(s) at " + ", ".join(fmt(c["cur"][0]) for c in cs))
        return "ops", sum((build(it, part, c, ctx, notes) for c in cs), [])
    if it == "protect":
        return "ops", sum((build(it, part, c, ctx, notes) for c in cs), [])
    if where["kind"] == "topic" and it in ("cut", "keep", "move", "caption") and any(not c.get("literal", True) for c in cs):
        # meaning-based match ("pricing" found "cost"): show what was found BEFORE touching anything
        opts = [(c["label"] + ("" if c.get("literal", True) else "  (matched by meaning)"), build(it, part, c, ctx, notes)) for c in cs[:MAX_OPTIONS]]
        if it in MULTI_ALL and len(cs) > 1:
            opts.append(("all of them", sum((build(it, part, c, ctx, notes) for c in cs), [])))
        opts.append(("none of these", []))
        return "ask", f"Nothing says “{where['query']}” word for word here. These look related. Which should I use?", opts
    if it == "restore":
        raise Reject("internal")  # handled in special
    if len(cs) > 1 and where["kind"] in ("phrase", "topic", "after_phrase", "before_phrase"):
        q = f"“{where.get('phrase') or where.get('query')}” appears {len(cs)} times. Which one, or all?"
        shown = cs[:MAX_OPTIONS]
        opts = [(c["label"], build(it, part, c, ctx, notes)) for c in shown]
        if len(cs) > len(shown):
            q += f" (showing the first {len(shown)} of {len(cs)}; say 'all of them' for every match)"
        if it in MULTI_ALL:
            opts.append((f"all of them ({len(cs)})" if len(cs) > len(shown) else "all of them", sum((build(it, part, c, ctx, notes) for c in cs), [])))
        return "ask", q, opts
    c = cs[0]
    if it == "cut" and where["kind"] == "topic" and c.get("spk") and "lead" in c:
        k = c["lead"]
        if k > 0 and ctx.sents[k - 1]["speaker"] != c["spk"]:
            lead = ctx.sents[k - 1]
            c2 = ctx.cand([(lead["start"], c["src"][-1][1])])
            return "ask", (f"The {where['speaker']}'s lines at {fmt(c['cur'][0])} answer a question from the other speaker "
                           f"(“{lead['text'][:60]}”). Remove that question too?"), [
                (f"only the {where['speaker']}'s lines", build("cut", part, c, ctx, notes)),
                ("both the question and the answer", build("cut", part, c2, ctx, notes))]
    if (it in ("cut", "keep", "move") and prev and nlu_norm(prev["instruction"]) == nlu_norm(part.get("_orig", ""))
            and where["kind"] in ("range", "first", "last", "point")):
        return "ask", (f"That is the same instruction as step {prev['id']}, which already removed/changed that span. Times mean the "
                       f"current timeline, so repeating it would hit different content. Apply again?"), [
            ("yes, apply it again", build(it, part, c, ctx, notes)), ("no, leave it as is", [])]
    ops = build(it, part, c, ctx, notes)
    if c.get("cur") and where["kind"] in ("range", "first", "last", "point"):
        notes.append(f"time policy: {'original' if where.get('policy') == 'original' else 'current'} timeline")
    elif it == "cut":
        notes.append(f"removed “{ctx.text(c['src'])}” ({fmt(c['cur'][0]) if c['cur'] else ''})")
    return "ops", ops


def nlu_norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


# ------------------------------------------------------------------ orchestration
def _timeline(session, video):
    return derive(session["entries"], video["words"], video["source"]["duration_s"], video["source"])


def _result(status, say, session, video, ops=None, summary="", question=None, options=None, reason=None,
            warnings=None, parts=None, before=None, extra=None, tl=None):
    tl = tl or _timeline(session, video)
    r = {"status": status, "instruction": say, "operations": ops or [], "summary": summary, "question": question,
         "options": options or [], "reason": reason, "warnings": (warnings or []) + tl["warnings"],
         "time_policy": "current", "timeline_hash": thash(tl), "duration_s": tl["duration_s"],
         "before_duration_s": before if before is not None else tl["duration_s"], "parts": parts or []}
    r.update(extra or {})
    return r


def _commit(session, say, ops):
    e = {"id": session["next_id"], "instruction": say, "ops": ops}
    session["next_id"] += 1
    session["entries"].append(e)
    session["redo"] = []  # a new edit clears redo
    return e


def _dsum(before, after, extra=""):
    d = after - before
    return f"Duration {before:.2f}s -> {after:.2f}s ({'+' if d >= 0 else ''}{d:.2f}s)." + (" " + extra if extra else "")


def _special(session, video, say, kind, ctx, before):
    if kind == "undo":
        if not session["entries"]:
            return _result("rejected", say, session, video, reason="nothing to undo")
        e = session["entries"].pop()
        session["redo"].append(e)
        return _result("applied", say, session, video, [{"op": "undo", "entry": e["id"], "instruction": e["instruction"]}],
                       _dsum(before, total(layout(fold(session["entries"], video["words"], ctx.dur)["segs"])),
                             f"Reversed step {e['id']} (“{e['instruction']}”) only."), before=before)
    if kind == "redo":
        if not session["redo"]:
            return _result("rejected", say, session, video, reason="nothing to redo (a new edit clears redo)")
        e = session["redo"].pop()
        session["entries"].append(e)
        return _result("applied", say, session, video, [{"op": "redo", "entry": e["id"]}],
                       _dsum(before, _timeline(session, video)["duration_s"], f"Re-applied step {e['id']}."), before=before)
    raise AssertionError(kind)


def _restore(session, video, say, ref, ctx, before):
    live = live_entries(session["entries"])
    cutty = [e for e in live if any(o["op"] == "cut" for o in e["ops"])]
    if "step" in ref:
        t = [e for e in live if e["id"] == ref["step"]]
        if not t:
            e = next((e for e in session["entries"] if e["id"] == ref["step"]), None)
            raise Reject(f"step {ref['step']} " + ("is already undone/restored" if e else "does not exist"))
        targets = t
    elif "nth_last_cut" in ref:
        n = ref["nth_last_cut"]
        if len(cutty) < n:
            raise Reject("there are not that many removals to restore" if cutty else "nothing has been removed")
        targets = [cutty[-n]]
    else:
        r = candidates(ref["where"], ctx, "restore", include_cut=True)
        if r[0] == "ask":
            cs = [c for _, c in r[2]]
            if ref["where"]["kind"] == "intro":
                cs = [cs[-1]]
        else:
            cs = r[1]
        rng = [x for c in cs for x in c["src"]]
        targets = [e for e in cutty if any(o["op"] == "cut" and any(o["src"][0] < b and o["src"][1] > a for a, b in rng) for o in e["ops"])]
        if ref["where"]["kind"] == "intro":
            targets = [e for e in cutty if any(o["op"] == "cut" and o["src"][0] <= 1.0 for o in e["ops"])]
        if not targets:
            raise Reject("nothing that was removed overlaps that part")
        if len(targets) > 1:
            opts = [(f"step {e['id']}: {e['instruction']}", [{"op": "restore", "target": e["id"]}]) for e in targets]
            opts.append(("all of them", [{"op": "restore", "target": e["id"]} for e in targets]))
            return "ask", f"{len(targets)} earlier edits removed that. Which should I restore?", opts
    return "ops", [{"op": "restore", "target": e["id"]} for e in targets]


def handle(session, video, say):
    say = say.strip()
    ctx = Ctx(session, video)
    before = _timeline(session, video)["duration_s"]
    session["pending"] = None if not session.get("pending") else session["pending"]
    dropped = bool(session.get("pending"))
    session["pending"] = None
    if not say:
        return _result("rejected", say, session, video, reason="empty instruction")
    if say.lower().strip(" .!") == "render":
        return _result("applied", say, session, video, [{"op": "render"}], "Render requested (see render job).", before=before,
                       extra={"render": True})
    try:
        parsed = nlu.parse(say)
    except Exception as ex:
        return _result("rejected", say, session, video, reason=f"could not parse: {ex}")
    parts, notes = parsed["parts"], []
    if parsed.get("note"):
        notes.append(f"“but keep {parsed['note']}”: noted (edits here never touch it)")
    ops_all, outcomes, ask = [], [], None
    for p in parts:
        p["_orig"] = say if len(parts) == 1 else p.get("text", "")
        try:
            r = resolve_part(p, ctx, notes)
            if r[0] == "special":
                if r[1] == "render":
                    return _result("applied", say, session, video, [{"op": "render"}], "Render requested (see render job).",
                                   before=before, extra={"render": True})
                if r[1] in ("undo", "redo"):
                    if len(parts) > 1:
                        raise Reject("undo/redo must be a separate instruction")
                    return _special(session, video, say, r[1], ctx, before)
                if isinstance(r[1], tuple):
                    r = _restore(session, video, say, r[1][1], ctx, before)
            if r[0] == "ask":
                if not r[2]:
                    raise Reject("I found no usable options for that")
                if ask is None:
                    ask = (p, r[1], r[2])
                outcomes.append({"text": p.get("text", ""), "status": "needs_clarification"})
            else:
                ops_all += r[1]
                outcomes.append({"text": p.get("text", ""), "status": "applied"})
        except Reject as ex:
            outcomes.append({"text": p.get("text", ""), "status": "rejected", "reason": str(ex)})
    rejected = [o for o in outcomes if o["status"] == "rejected"]
    if ask:
        p, q, opts = ask
        session["pending"] = {"instruction": say, "question": q, "base_ops": ops_all, "options": [{"label": l, "ops": o} for l, o in opts],
                              "parts": outcomes, "notes": notes}
        return _result("needs_clarification", say, session, video, question=q, options=[l for l, _ in opts], before=before, parts=outcomes,
                       summary="Nothing applied yet." + (f" (Earlier question dropped.)" if dropped else ""),
                       warnings=[f"{len(rejected)} part(s) cannot be applied: " + "; ".join(o['reason'] for o in rejected)] if rejected else [])
    if not ops_all:
        if len(parts) == 1 or not rejected:
            reason = rejected[0]["reason"] if rejected else "nothing to do"
        else:
            reason = "; ".join(f"“{o['text']}”: {o['reason']}" for o in rejected)
        return _result("rejected", say, session, video, reason=reason, parts=outcomes, before=before)
    return _finish(session, video, say, ops_all, notes, outcomes, before)


def _finish(session, video, say, ops, notes, outcomes, before):
    trial = copy.deepcopy(session)
    _commit(trial, say, ops)
    tl = _timeline(trial, video)
    if tl["duration_s"] < 0.2:
        return _result("rejected", say, session, video, reason="that would remove the whole video; nothing would be left", before=before)
    e = _commit(session, say, ops)
    rejected = [o for o in outcomes if o["status"] == "rejected"]
    warn = [f"part not applied: “{o['text']}” — {o['reason']}" for o in rejected]
    return _result("applied", say, session, video, ops, _dsum(before, tl["duration_s"], " ".join(notes)), before=before,
                   warnings=warn, parts=outcomes if len(outcomes) > 1 else [], extra={"step_id": e["id"]}, tl=tl)


def answer(session, video, text):
    p = session.get("pending")
    say = (p or {}).get("instruction", "")
    if not p:
        return _result("rejected", "", session, video, reason="no question is waiting for an answer")
    t = text.strip().lower()
    opts = p["options"]
    pick = None
    ords = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
            "sixth": 6, "seventh": 7, "eighth": 8}
    bare = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    if t in bare:  # only a bare "two" means option 2 ("the last one" must not mean option 1)
        t = str(bare[t])
    if re.search(r"\b(all|both|every|everything|each)\b", t):
        pick = next((o for o in opts if o["label"].startswith(("all", "both", "yes"))), None)
    if pick is None and re.search(r"\b(cancel|none|nothing|never ?mind|skip)\b", t):
        pick = {"label": "cancel", "ops": []}
    if pick is None:
        m = re.search(r"\b(\d+)\b", t)
        w = next((ords[x] for x in re.findall(r"[a-z0-9]+", t) if x in ords), None)
        n = w or (int(m.group(1)) if m and not re.search(r"\d+:\d", t) and int(m.group(1)) <= len(opts) else None)
        if n and n <= len(opts):
            pick = opts[n - 1]
        elif re.search(r"\blast\b", t):
            pick = opts[-1] if not opts[-1]["label"].startswith("all") else opts[-2]
    if pick is None:
        for o in opts:  # a time like "2:40" or words from the label
            for tm in re.findall(r"\d+:\d{2}", t):
                if tm in o["label"] or tm.lstrip("0") in o["label"]:
                    pick = o
        if pick is None:
            best = max(opts, key=lambda o: difflib.SequenceMatcher(None, t, o["label"].lower()).ratio())
            if t and (t in best["label"].lower() or difflib.SequenceMatcher(None, t, best["label"].lower()).ratio() > 0.6):
                pick = best
    if pick is None:
        toks = [x for x in re.findall(r"[a-z]{3,}", t) if x not in STOP and x not in ("one", "the", "that", "this", "please")]
        sc = [sum(x in o["label"].lower() for x in toks) for o in opts]
        if toks and sc.count(max(sc)) == 1 and max(sc) > 0:
            pick = opts[sc.index(max(sc))]
    if pick is None and re.fullmatch(r"(yes|yep|yeah|y|ok|okay|sure)", t):
        pick = opts[0]
    if pick is None and re.fullmatch(r"(no|nope|n)", t):
        pick = opts[-1] if opts[-1]["label"].startswith(("no", "cancel")) else {"label": "cancel", "ops": []}
    if pick is None:
        session["pending"] = p
        return _result("needs_clarification", say, session, video, question="I couldn't tell which you meant. " + p["question"],
                       options=[o["label"] for o in opts], summary="Nothing applied yet.")
    session["pending"] = None
    before = _timeline(session, video)["duration_s"]
    ops = p["base_ops"] + pick["ops"]
    if not pick["ops"] and pick["label"] in ("cancel",) or not ops:
        return _result("rejected", say, session, video, reason=f"cancelled: you chose “{pick['label']}”", before=before)
    return _finish(session, video, say, ops, p["notes"] + [f"you chose: {pick['label']}"], p["parts"], before)


def new_session(sid, video_id):
    return {"id": sid, "video_id": video_id, "entries": [], "redo": [], "next_id": 1, "pending": None}
