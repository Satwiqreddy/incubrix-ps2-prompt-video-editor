"""Transcript: word list with ids, providers, sentence split, quality warnings."""
import json, os, re
from .config import cfg


def norm(t):
    return re.sub(r"[^\w']", "", t.lower())


def build(raw):
    """raw: [{text,start,end,speaker?,conf?}] -> words with ids, sorted, validated."""
    ws = []
    for i, w in enumerate(sorted(raw, key=lambda w: (w["start"], w["end"]))):
        ws.append({"id": i, "text": w["text"], "start": round(float(w["start"]), 3),
                   "end": round(float(w["end"]), 3), "speaker": w.get("speaker", "S1"),
                   "conf": round(float(w.get("conf", 1.0)), 3)})
    return ws


def warnings(words):
    out = []
    low = [w for w in words if w["conf"] < cfg.low_conf]
    if low:
        out.append(f"{len(low)} low-confidence words flagged (ids {[w['id'] for w in low][:10]})")
    ov = [(a, b) for a, b in zip(words, words[1:]) if b["start"] < a["end"] - 0.02 and a["speaker"] != b["speaker"]]
    if ov:
        out.append(f"overlapping speech at {[round(a['end'], 2) for a, _ in ov][:5]}s")
    flips = sum(1 for a, b, c in zip(words, words[1:], words[2:])
                if a["speaker"] == c["speaker"] != b["speaker"] and b["end"] - b["start"] < 0.5)
    if flips:
        out.append(f"{flips} very short speaker turns: possible speaker-label swap")
    return out


def sentences(words):
    """Split on end punctuation, a pause >= 0.7 s, or a speaker change."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if (nxt is None or re.search(r"[.!?]$", w["text"]) or nxt["start"] - w["end"] >= 0.7
                or nxt["speaker"] != w["speaker"]):
            out.append({"ids": [x["id"] for x in cur], "start": cur[0]["start"], "end": cur[-1]["end"],
                        "speaker": cur[0]["speaker"], "text": " ".join(x["text"] for x in cur)})
            cur = []
    return out


# ---- providers (swap with TRANSCRIBER: auto | sidecar | whisper) ----
_model = {}


def _f0(x, sr=16000):
    """median pitch (Hz) of the voiced frames in x, or None. Plain autocorrelation, 40 ms frames."""
    import numpy as np
    n, hop, vals = int(.04 * sr), int(.02 * sr), []
    lo, hi = int(sr / 400), int(sr / 70)
    for i in range(0, len(x) - n, hop):
        f = x[i:i + n] - x[i:i + n].mean()
        e = float(f @ f)
        if e < 1e-4 * n:
            continue
        ac = np.correlate(f, f, "full")[n - 1:]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / ac[0] > 0.35:
            vals.append(sr / k)
    return float(np.median(vals)) if len(vals) >= 3 else None


def diarize(audio, words, sr=16000):
    """Estimate 'S1'/'S2' labels from voice pitch (2-means in log-Hz). Returns None when the voices are not clearly two groups.
    A heuristic, not a speaker-recognition model: same-pitch voices will be merged into one speaker."""
    import numpy as np
    f0 = [_f0(audio[int(w["start"] * sr):int(w["end"] * sr)], sr) if w["end"] - w["start"] >= 0.12 else None for w in words]
    idx = [i for i, v in enumerate(f0) if v]
    if len(idx) < 20:
        return None
    lf = np.log2([f0[i] for i in idx])
    c = np.array([np.percentile(lf, 20), np.percentile(lf, 80)])
    for _ in range(25):
        lab = np.abs(lf[:, None] - c[None, :]).argmin(1)
        if len(set(lab)) < 2:
            return None
        c = np.array([lf[lab == k].mean() for k in (0, 1)])
    within = float(np.sqrt(np.mean([(lf[lab == k] - c[k]).var() for k in (0, 1)])))
    # two real voices are far apart relative to their own spread; one voice split in two is not (gaussian 2-means gives ~2.7)
    if abs(c[1] - c[0]) < 0.25 or abs(c[1] - c[0]) / max(within, 1e-6) < 3.5 or min((lab == 0).mean(), (lab == 1).mean()) < 0.1:
        return None
    seq, last = [None] * len(words), None
    for j, i in enumerate(idx):
        seq[i] = int(lab[j])
    for i in range(len(words)):                      # unvoiced words take the nearest voiced neighbour
        if seq[i] is None:
            nxt = next((seq[k] for k in range(i + 1, len(words)) if seq[k] is not None), None)
            seq[i] = last if last is not None else nxt
        last = seq[i]
    sm = list(seq)
    for i in range(2, len(seq) - 2):                 # median filter: a single odd word inside a run is not a speaker change
        win = seq[i - 2:i + 3]
        sm[i] = max(set(win), key=win.count)
    first = sm[0]
    return ["S1" if v == first else "S2" for v in sm]




def _whisper(media_path):
    """faster-whisper (MIT) on audio decoded by our own ffmpeg (float32, 16 kHz mono) -> word timestamps + probabilities."""
    import subprocess
    import numpy as np
    from faster_whisper import WhisperModel
    from .media import ffmpeg
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", media_path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    if len(audio) < 1600:
        return []
    if cfg.whisper_model not in _model:
        _model[cfg.whisper_model] = WhisperModel(cfg.whisper_model, compute_type="int8")
    # the prompt nudges the model to keep "um/uh" instead of silently dropping them (filler removal needs them)
    segs, _ = _model[cfg.whisper_model].transcribe(audio, word_timestamps=True, initial_prompt="Um, uh, so, like, you know, well, I mean.")
    raw = [{"text": w.word.strip(), "start": float(w.start), "end": float(w.end), "conf": float(w.probability)}
           for sg in segs for w in sg.words if w.word.strip()]
    try:
        labels = diarize(audio, raw) if raw else None
    except Exception:  # never let the speaker estimate break transcription
        labels = None
    for w, lab in zip(raw, labels or []):
        w["speaker"] = lab
    return build(raw)


def transcribe(media_path, sidecar=None, has_audio=True):
    from .errors import ApiError
    p = cfg.transcriber
    if p == "auto":  # a supplied transcript wins; otherwise run speech-to-text on the video's audio
        p = "sidecar" if sidecar else "whisper"
    if p in ("sidecar", "mock"):
        sc = sidecar or os.path.splitext(media_path)[0] + ".words.json"
        if isinstance(sc, (str, os.PathLike)) and os.path.exists(sc):
            sc = json.load(open(sc, encoding="utf-8"))
        if not isinstance(sc, (dict, list)):
            raise ApiError("TRANSCRIPT_MISSING", "TRANSCRIBER=sidecar needs a transcript file (words.json)", 422)
        return build(sc["words"] if isinstance(sc, dict) else sc)
    if p == "whisper":
        if not has_audio:
            return []
        try:
            return _whisper(media_path)
        except ImportError:
            raise ApiError("CONFIG_INVALID", "TRANSCRIBER=whisper needs: pip install faster-whisper", 500)
        except ApiError:
            raise
        except Exception as e:  # model download / decode problems
            raise ApiError("TRANSCRIPTION_FAILED", f"speech-to-text failed: {e}", 502, True)
    raise ApiError("CONFIG_INVALID", f"unknown TRANSCRIBER {p}", 500)
