"""Deterministic timeline. Every edit is stored against ORIGINAL source time / word ids,
never against a clock position in the edited video. state = fold(live history entries).
Nothing here touches media or calls a model."""
import hashlib, json
from bisect import bisect_left, bisect_right
from .config import cfg

EPS = 1e-6


def live_entries(entries):
    """Drop entries cancelled by a later 'restore' (a restore can itself be restored)."""
    dead = set()
    for e in reversed(entries):
        if e["id"] in dead:
            continue
        for op in e["ops"]:
            if op["op"] == "restore":
                dead.add(op["target"])
    return [e for e in entries if e["id"] not in dead]


def _split(segs, x):
    out = []
    for a, b, s in segs:
        out += [(a, x, s), (x, b, s)] if a + EPS < x < b - EPS else [(a, b, s)]
    return out


def _inside(t, x, y):
    return t[0] >= x - EPS and t[1] <= y + EPS


def apply_cuts(segs, ranges):
    """Remove many source ranges from the segment list in one pass (O((segs+ranges) log ranges))."""
    rs = sorted((x, y) for x, y in ranges if y > x + EPS)
    merged = []
    for x, y in rs:
        if merged and x <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], y)
        else:
            merged.append([x, y])
    ends = [m[1] for m in merged]
    out = []
    for a, b, s in segs:
        cur, i = a, bisect_right(ends, a)
        while i < len(merged) and merged[i][0] < b - EPS:
            x, y = merged[i]
            if x > cur + EPS:
                out.append((cur, x, s))
            cur = max(cur, y)
            i += 1
        if cur < b - EPS:
            out.append((cur, b, s))
    return out


def _auto(words, eid, n=7):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= n or w["text"].endswith((".", "?", "!")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"id": f"cap{eid}_a{j}", "text": " ".join(w["text"] for w in c),
             "words": [c[0]["id"], c[-1]["id"]], "style": {}} for j, c in enumerate(out)]


def fold(entries, words, duration):
    segs = [(0.0, float(duration), 1.0)]
    st = {"caps": [], "mutes": [], "bleeps": [], "protects": set(), "music": None}
    styles = []
    for e in live_entries(entries):
        cuts = []
        for i, op in enumerate(e["ops"]):
            k = op["op"]
            if k == "cut":
                cuts.append(op["src"])
                continue
            if cuts:
                segs, cuts = apply_cuts(segs, cuts), []
            if k == "speed":
                x, y = op["src"]
                segs = _split(_split(segs, x), y)
                segs = [(a, b, op["factor"] if _inside((a, b), x, y) else s) for a, b, s in segs]
            elif k == "move":
                x, y = op["src"]
                segs = _split(_split(segs, x), y)
                inn = [t for t in segs if _inside(t, x, y)]
                rest = [t for t in segs if not _inside(t, x, y)]
                segs = rest + inn if op["to"] == "end" else inn + rest
            elif k == "caption":
                st["caps"].append({"id": f"cap{e['id']}_{i}", "text": op["text"], "words": op["words"], "style": {}})
            elif k == "autocaption":
                st["caps"] += _auto(words, e["id"])
            elif k == "mute":
                st["mutes"].append(op["words"])
            elif k == "bleep":
                st["bleeps"].append(op["words"])
            elif k == "protect":
                st["protects"].add(op["word"])
            elif k == "music":
                st["music"] = {"gain_db": op["gain_db"], "duck_db": op["duck_db"], "track": op.get("track")}
            elif k == "style":
                styles.append(op)
        if cuts:
            segs = apply_cuts(segs, cuts)
    by_id = {c["id"]: c for c in st["caps"]}
    for op in styles:
        c = by_id.get(op["target"])
        if c:
            c["style"].update(op["props"])
            if "scale" in op:
                c["style"]["scale"] = round(c["style"].get("scale", 1.0) * op["scale"], 3)
    st["segs"] = segs
    return st


# ---- time mapping ----
class Layout(list):
    """Segments in OUTPUT order with sorted indexes so mapping is O(log n). Source ranges never overlap."""
    def __init__(self, segs):
        super().__init__()
        t = 0.0
        for a, b, s in segs:
            d = (b - a) / s
            self.append({"a": a, "b": b, "s": s, "o0": t, "o1": t + d})
            t += d
        self.order = sorted(range(len(self)), key=lambda i: self[i]["a"])
        self.sa = [self[i]["a"] for i in self.order]
        self.o0 = [g["o0"] for g in self]


def layout(segs):
    return Layout(segs)


def total(L):
    return L[-1]["o1"] if L else 0.0


def project(L, x, y):
    """source range -> list of output intervals (one per segment it touches)."""
    out = []
    i = max(0, bisect_right(L.sa, x) - 1)
    while i < len(L.sa) and L.sa[i] < y - EPS:
        g = L[L.order[i]]
        lo, hi = max(x, g["a"]), min(y, g["b"])
        if hi > lo + EPS:
            out.append((g["o0"] + (lo - g["a"]) / g["s"], g["o0"] + (hi - g["a"]) / g["s"]))
        i += 1
    return out


def to_source(L, t0, t1):
    """output-time range -> list of source ranges (current-time policy)."""
    out = []
    i = max(0, bisect_right(L.o0, t0) - 1)
    while i < len(L) and L[i]["o0"] < t1 - EPS:
        g = L[i]
        lo, hi = max(t0, g["o0"]), min(t1, g["o1"])
        if hi > lo + EPS:
            out.append((g["a"] + (lo - g["o0"]) * g["s"], g["a"] + (hi - g["o0"]) * g["s"]))
        i += 1
    return out


def word_range(words, ids):
    return words[ids[0]]["start"], words[ids[1]]["end"]


def derive(entries, words, duration, source=None):
    """Final, hashable timeline description."""
    st = fold(entries, words, duration)
    L = layout(st["segs"])
    warns = []

    def ivals(ids, minlen=0.0):
        iv = project(L, *word_range(words, ids))
        return [(a, max(b, min(a + minlen, total(L)))) for a, b in iv]

    caps = []
    for c in st["caps"]:
        iv = ivals(c["words"], cfg.min_caption)
        if not iv:
            warns.append(f"caption '{c['text']}' hidden: its words are cut")
        caps.append({**c, "intervals": [[round(a, 3), round(b, 3)] for a, b in iv], "hidden": not iv})
    mu = [[round(a, 3), round(b, 3)] for ids in st["mutes"] for a, b in ivals(ids)]
    bl = [[round(a, 3), round(b, 3)] for ids in st["bleeps"] for a, b in ivals(ids)]
    tl = {"schema": "incubrix.timeline/1",
          "duration_s": round(total(L), 3),
          "segments": [{"src_start": round(g["a"], 3), "src_end": round(g["b"], 3), "speed": g["s"],
                        "out_start": round(g["o0"], 3), "out_end": round(g["o1"], 3)} for g in L],
          "captions": caps, "mutes": mu, "bleeps": bl, "music": st["music"],
          "protected_pauses_after_word": sorted(st["protects"]), "warnings": warns}
    if source:
        tl["source"] = {k: source[k] for k in ("sha256", "duration_s", "fps", "width", "height", "has_audio") if k in source}
    return tl


def thash(tl):
    core = {k: v for k, v in tl.items() if k not in ("warnings", "source")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def to_otio(tl, media_url="source.mp4"):
    """Minimal OpenTimelineIO JSON: video + audio track, one clip per segment (speed = LinearTimeWarp)."""
    fps = (tl.get("source") or {}).get("fps") or 30

    def rt(v):
        return {"OTIO_SCHEMA": "RationalTime.1", "rate": fps, "value": round(v * fps, 3)}

    def rg(a, b):
        return {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(a), "duration": rt(b - a)}

    def track(kind):
        cl = []
        for i, g in enumerate(tl["segments"]):
            fx = [] if g["speed"] == 1 else [{"OTIO_SCHEMA": "LinearTimeWarp.1", "name": "speed", "effect_name":
                                              "LinearTimeWarp", "time_scalar": g["speed"], "metadata": {}}]
            cl.append({"OTIO_SCHEMA": "Clip.2", "name": f"seg{i}", "source_range": rg(g["src_start"], g["src_end"]),
                       "effects": fx, "markers": [], "metadata": {},
                       "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "ExternalReference.1", "name": "",
                                                             "target_url": media_url, "available_range": None,
                                                             "metadata": {}}},
                       "active_media_reference_key": "DEFAULT_MEDIA"})
        return {"OTIO_SCHEMA": "Track.1", "name": kind, "kind": kind, "children": cl, "metadata": {},
                "effects": [], "markers": []}

    markers = [{"OTIO_SCHEMA": "Marker.2", "name": c["text"], "marked_range": rg(iv[0], iv[1]), "color": "RED",
                "metadata": {"incubrix": "caption"}} for c in tl["captions"] for iv in c["intervals"]]
    return {"OTIO_SCHEMA": "Timeline.1", "name": "incubrix-edit", "metadata": {"incubrix_hash": thash(tl)},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": [track("Video"), track("Audio")],
                       "markers": markers, "metadata": {}, "effects": []}}
