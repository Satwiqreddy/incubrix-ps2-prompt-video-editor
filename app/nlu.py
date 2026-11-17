"""Words -> structured INTENTS. This is the only place language is interpreted.
Default parser is deterministic rules (no model, no keys). An LLM may replace it (NLU=llm) but it must
return the same intent dicts, which are validated here; the timeline is always built by engine.py/timeline.py."""
import json, re
from .config import cfg, env

INTENTS = {"caption", "autocaption", "cut", "keep", "move", "speed", "mute", "bleep", "fillers", "silences",
           "protect", "music", "undo", "redo", "restore", "style", "render", "shorter", "unknown"}

NW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
      "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90}
N = r"(?:\d+(?:\.\d+)?|(?:" + "|".join(NW) + r")(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)"
T_CLOCK = r"\d+:\d{2}(?::\d{2})?"
T_MIN = rf"(?:a|{N})\s*(?:minutes?|mins?)(?:\s*(?:and\s*)?{N}(?:\s*(?:seconds?|secs?))?)?"
T_SEC = rf"{N}\s*(?:seconds?|secs?|s)\b"
T = f"(?:{T_CLOCK}|{T_MIN}|{T_SEC})"
VERBS = r"(?:cut|remove|delete|trim|drop|mute|bleep|beep|censor|speed|slow|add|put|keep|move|make|caption|auto-?caption|undo|redo|restore|bring|render|export|reorder|insert|take|get)"
COLORS = ["red", "green", "blue", "yellow", "white", "black", "orange", "purple", "pink", "cyan"]


def num(x):
    if re.fullmatch(r"\d+(?:\.\d+)?", x):
        return float(x)
    return float(sum(NW[w] for w in re.split(r"[- ]", x)))


def parse_time(s):
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", s)
    if m:
        p = [int(x) for x in m.groups() if x is not None]
        return float(p[0] * 60 + p[1]) if len(p) == 2 else float(p[0] * 3600 + p[1] * 60 + p[2])
    m = re.fullmatch(rf"(a|{N})\s*(?:minutes?|mins?)(?:\s*(?:and\s*)?({N})(?:\s*(?:seconds?|secs?))?)?", s)
    if m:
        return (1.0 if m.group(1) == "a" else num(m.group(1))) * 60 + (num(m.group(2)) if m.group(2) else 0)
    m = re.fullmatch(rf"({N})\s*(?:seconds?|secs?|s)?", s)
    return num(m.group(1)) if m else None


def _q(s, keep=False):
    """hide quoted strings so they are never split or pattern-matched"""
    qs = []

    def sub(m):
        qs.append(m.group(0) if keep else m.group(2))
        return f"\x00{len(qs) - 1}\x00"
    t = re.sub(r"(?:(?<=\s)|^)(['\"“‘])(.+?)(['\"”’])(?=[\s.,;!?)]|$)", sub, s)
    return t, qs


def _uq(t, qs):
    return re.sub(r"\x00(\d+)\x00", lambda m: qs[int(m.group(1))], t)


def split_parts(s):
    t, qs = _q(s.strip(), keep=True)
    t = re.sub(r"[.!]+$", "", t)
    t = re.sub(r"^(?:(?:please|actually|ok|okay|hey|now|also|and|so)[,\s]*)+", "", t, flags=re.I)
    keep_note = None
    m = re.search(r",?\s*\bbut\s+(?:keep|leave)\s+(.+)$", t, re.I)
    if m:
        keep_note, t = m.group(1), t[:m.start()]
    parts = re.split(rf"\s*(?:[,;]|\bthen\b|\band\b)\s*(?:and\s+|then\s+)?(?={VERBS}\b)", t, flags=re.I)
    return [_uq(p.strip(), qs) for p in parts if p.strip()], keep_note, qs


def parse_where(t):
    t = t.strip().strip(".")
    tl = t.lower()
    tl = re.sub(r"^(the |that )?(part|section|bit|portion|segment)? ?(where|when) (i|we|they|he|she) (say|says|said|mention|mentions|talk about|talks about) ", "", tl)
    if re.fullmatch(r"(everything|all( of it)?|the (whole|entire) (video|thing)|it all)", tl):
        return {"kind": "all"}
    m = re.search(r"everything (after|before) (?:i say |the (?:word|phrase) |i said )?(.+)", tl)
    if m:
        return {"kind": m.group(1) + "_phrase", "phrase": m.group(2).strip(" '\"")}
    m = re.fullmatch(r"(?:the )?(first|last|second|third|1st|2nd|3rd) (?:sentence|line)", tl)
    if m:
        return {"kind": "sentence", "n": {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "last": -1}[m.group(1)]}
    m = re.search(rf"(?:the )?first ({T}|\d+(?:\.\d+)?)", tl)
    if m:
        return {"kind": "first", "n": parse_time(m.group(1))}
    m = re.search(rf"(?:the )?last ({T}|\d+(?:\.\d+)?)", tl)
    if m:
        return {"kind": "last", "n": parse_time(m.group(1))}
    m = re.search(rf"(?:from |between )?({T})\s*(?:-|–|to|until|till|through|and)\s*({T})(?:\s*(?:in the )?(original))?", tl)
    if m:
        return {"kind": "range", "a": parse_time(m.group(1)), "b": parse_time(m.group(2)),
                "policy": "original" if m.group(3) or "original" in tl else "current"}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", tl)
    if m:
        return {"kind": "range", "a": float(m.group(1)), "b": float(m.group(2)), "policy": "current"}
    m = re.search(rf"\b(?:at|around|near)\s+({T})", tl)
    if m:
        return {"kind": "point", "t": parse_time(m.group(1)), "policy": "original" if "original" in tl else "current"}
    if re.fullmatch(r"(the )?(intro|introduction|beginning|start|opening)", tl):
        return {"kind": "intro"}
    m = re.search(r"everything (?:the |that )?(guest|host|speaker \d|interviewer|interviewee) (?:says|said) about (.+)", tl)
    if m:
        return {"kind": "topic", "query": m.group(2).strip(), "speaker": m.group(1)}
    m = re.search(r"(?:the )?(?:part|section|bit|portion|segment|bits?|parts?|sentence|sentences|mentions?|lines?)(?: of the video)?\s+(?:about|on|regarding|that mentions?|mentioning|where i (?:talk about|mention|discuss))\s+(.+)", tl)
    if m:
        return {"kind": "topic", "query": m.group(1).strip(), "speaker": None}
    m = re.search(r"(?:about|mentions? of|mentioning)\s+(.+)", tl)
    if m:
        return {"kind": "topic", "query": m.group(1).strip(), "speaker": None}
    if re.search(r"\b(boring|dull|bad|slow|rambling|awkward|weak|worst)\b", tl):
        return {"kind": "vague", "text": t}
    m = re.fullmatch(r"(?:the |that )?(.+?)\s+(?:part|section|bit|portion|segment|bits|parts)", tl)
    if m and m.group(1) not in ("first", "last", "whole", "entire", "next", "previous"):
        return {"kind": "topic", "query": m.group(1).strip(), "speaker": None}
    return {"kind": "phrase", "phrase": t.strip(" '\"")} if t else None


def _factor(t, default):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|times|×)", t)
    if m:
        return float(m.group(1))
    if re.search(r"\bhalf\b", t):
        return 0.5
    if re.search(r"\b(double|twice)\b", t):
        return 2.0
    return default


def parse_part(p):
    t0 = p.strip()
    t = re.sub(r"^(?:(?:please|actually|ok|okay|hey|now|also|and|so)\b[,\s]*)+", "", t0, flags=re.I).strip()
    tl = t.lower()
    qt, qs = _q(t)
    ql = qt.lower()
    if re.fullmatch(r"(undo|undo that|undo it|undo the last (edit|change|step|one))", tl):
        return {"intent": "undo"}
    if re.fullmatch(r"(redo|redo that|redo it)", tl):
        return {"intent": "redo"}
    if re.match(r"(render|export)\b", tl):
        return {"intent": "render"}
    if re.match(r"(undo|restore|bring back|revert)\b", tl) or re.search(r"\b(put|bring)\b.*\bback\b", tl):
        m = re.search(r"\b(?:step|edit|instruction)\s*#?(\d+)", tl)
        if m:
            return {"intent": "restore", "ref": {"step": int(m.group(1))}}
        m = re.search(r"second[- ]to[- ]last", tl)
        if m:
            return {"intent": "restore", "ref": {"nth_last_cut": 2}}
        if re.search(r"what i (removed|cut|deleted)|the last (cut|removal|thing)|that cut|the cut|it\b$", tl):
            return {"intent": "restore", "ref": {"nth_last_cut": 1}}
        rest = re.sub(r"^(undo|restore|bring back|revert|put back|put|bring)\s*|\s*back\s*$|\bback\b", "", tl).strip()
        return {"intent": "restore", "ref": {"where": parse_where(rest)} if rest else {"nth_last_cut": 1}}
    if re.match(r"make (that|the|this|it)\b", tl):
        props = {}
        for c in COLORS:
            if re.search(rf"\b{c}\b", tl):
                props["color"] = c
        scale = 1.25 if re.search(r"\b(bigger|larger|big|large)\b", tl) else 0.8 if re.search(r"\b(smaller|small|tiny)\b", tl) else None
        if props or scale:
            return {"intent": "style", "props": props, "scale": scale}
        if re.search(r"\b(shorter|tighter|quicker|faster)\b", tl):
            return {"intent": "shorter"}
        return {"intent": "unknown", "text": t0}
    if re.search(r"auto-?caption|caption (the )?(whole|entire|all|everything)|(add|generate) (captions|subtitles)|subtitle (the )?(whole|entire|video)", tl):
        return {"intent": "autocaption"}
    m = re.match(r"(?:add|put|insert|create|overlay)\s+(?:a\s+|the\s+)?(?:caption|text|title|text overlay|subtitle|label)?\s*\x00(\d+)\x00\s*(.*)$", qt, re.I)
    if m:
        rest = _uq(m.group(2), qs).strip()
        rest = re.sub(r"^(?:when|as|while) i say\s+", "", rest, flags=re.I)
        rest = re.sub(r"^(?:on|over|at|during)\s+(?:the\s+)?(?:words?\s+)?", lambda x: x.group(0) if re.match(r"at\b", x.group(0)) else "", rest, flags=re.I)
        return {"intent": "caption", "text": qs[int(m.group(1))], "where": parse_where(rest) if rest else None}
    if re.search(r"\bmusic\b", ql) and re.match(r"(add|put|insert|play)", ql):
        return {"intent": "music", "quiet": bool(re.search(r"quiet|soft|low|subtle", ql))}
    if re.search(r"\b(filler|fillers|ums?|uhs?|ums and uhs|um and uh)\b", ql) and re.match(r"(remove|cut|delete|drop|get rid|take out)", ql):
        return {"intent": "fillers"}
    m = re.match(rf"(?:keep|preserve|protect)\s+(?:the|that|this)\s+(?:pause|silence|gap)\s*(.*)$", ql)
    if m:
        w = _uq(m.group(1), qs).strip()
        w = re.sub(r"^(?:after|at|on|following)\s+", "", w)
        return {"intent": "protect", "where": parse_where(w) if w else None}
    m = re.match(rf"(?:remove|cut|delete|trim|drop|take out|get rid of)\s+(?:all\s+|the\s+|any\s+)*(?:silences?|pauses?|dead air|gaps)(?:\s+(?:longer|over|more|greater|above)(?:\s+than)?\s+({T}|\d+(?:\.\d+)?))?", ql)
    if m:
        return {"intent": "silences", "min_s": parse_time(m.group(1)) if m.group(1) else 1.0, "default": not m.group(1)}
    m = re.match(r"(mute|bleep|beep|censor)\s+(.*)$", ql)
    if m:
        w = _uq(m.group(2), qs).strip()
        w = re.sub(r"^(the\s+)?(word|words|phrase)\s+", "", w)
        return {"intent": "mute" if m.group(1) == "mute" else "bleep", "where": parse_where(w)}
    m = re.match(r"(speed up|slow down|speed|slow)\b\s*(.*)$", ql)
    if m:
        up = m.group(1).startswith("speed")
        f = _factor(ql, None)
        rest = re.sub(r"\bby\b|(\d+(?:\.\d+)?)\s*(x|times|×)|\bhalf\b|\b(double|twice)\b", " ", m.group(2)).strip()
        rest = re.sub(r"^(the\s+)?(video|clip|part|section)\s*", "", rest).strip()
        return {"intent": "speed", "factor": f, "assumed": f is None, "default": 2.0 if up else 0.5,
                "where": parse_where(_uq(rest, qs)) if rest and rest not in ("up", "down") else None}
    m = re.match(r"(?:move|reorder|put|shift)\s+(.+?)\s+(?:to|at)\s+the\s+(end|start|beginning|front)\b", ql)
    if m:
        return {"intent": "move", "where": parse_where(_uq(m.group(1), qs)), "to": "end" if m.group(2) == "end" else "start"}
    m = re.match(r"(?:keep|trim to|only keep|retain)\s+(?:only\s+)?(.+)$", ql)
    if m:
        return {"intent": "keep", "where": parse_where(_uq(m.group(1), qs))}
    m = re.match(r"(?:cut|remove|delete|trim|drop|take out|get rid of)\s+(.+)$", ql)
    if m:
        return {"intent": "cut", "where": parse_where(_uq(m.group(1), qs))}
    return {"intent": "unknown", "text": t0}


def rules_parse(text):
    parts, note, _ = split_parts(text)
    return {"parts": [{"text": p, **parse_part(p)} for p in parts], "note": note}


# ---- optional LLM translator (same output contract; validated; falls back to rules) ----
LLM_PROMPT = ("Translate the user's video-editing instruction into JSON: {\"parts\":[{\"intent\":one of "
              + ",".join(sorted(INTENTS)) + ", ...fields}]}. Fields: text, where{kind:all|first|last|range|point|phrase|topic|"
              "after_phrase|before_phrase|intro|vague, n,a,b,t,phrase,query,speaker,policy}, factor, min_s, props, scale, ref, to. "
              "Times are seconds. Output JSON only. Never invent timestamps that were not said.")


def llm_parse(text, post=None):
    import httpx
    post = post or (lambda body, h: httpx.post(env("LLM_API_URL"), json=body, headers=h, timeout=20).json())
    r = post({"model": env("LLM_MODEL"), "max_tokens": 600, "system": LLM_PROMPT,
              "messages": [{"role": "user", "content": text}]},
             {"x-api-key": env("LLM_API_KEY"), "anthropic-version": "2023-06-01"})
    raw = r["content"][0]["text"]
    return validate(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]))


def validate(d):
    ps = d.get("parts")
    if not isinstance(ps, list) or not ps or any(p.get("intent") not in INTENTS for p in ps):
        raise ValueError("invalid intent JSON")
    for p in ps:
        p.setdefault("text", "")
    return {"parts": ps, "note": d.get("note")}


def parse(text):
    if cfg.nlu == "llm":
        try:
            return llm_parse(text)
        except Exception:
            pass  # provider down / invalid output: deterministic rules still work
    return rules_parse(text)
