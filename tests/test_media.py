"""Render checks on real files: length, A/V sync, captions on the right frames, audio on the right words."""
import json, os, subprocess
import numpy as np
import pytest
from app import engine as E, media
from app.timeline import derive, layout, project
from app.transcript import build


def setup(fx, name="single", edits=()):
    src = os.path.join(fx, name + ".mp4")
    info = media.probe(src)
    words = build(json.load(open(os.path.join(fx, name + ".words.json")))["words"])
    v = {"words": words, "source": info}
    s = E.new_session("s", "v")
    for say in edits:
        r = E.handle(s, v, say)
        assert r["status"] == "applied", (say, r)
    return src, info, words, derive(s["entries"], words, info["duration_s"], info), s


def gray_frame(path, t, w=640, h=360):
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w).astype(float)


def onsets(path, sr=8000):
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True).stdout
    a = np.abs(np.frombuffer(raw, np.int16).astype(float)) / 32768
    win = int(sr * 0.01)
    env = np.convolve(a, np.ones(win) / win, "same")[::win]
    on = env > 0.05
    return [i * 0.01 for i in range(len(on)) if on[i] and (i == 0 or not on[i - 1])]


def test_cut_render_length_sync_and_audio_follows_words(fx, tmp_path):
    src, info, words, tl, _ = setup(fx, edits=["cut the first 30 seconds", "cut the last 10 seconds"])
    out = str(tmp_path / "o.mp4")
    ver = media.render(src, tl, info, out)
    assert ver["length_diff_frames"] <= 1 and ver["av_offset_frames"] <= 1, ver
    L = layout([(g["src_start"], g["src_end"], g["speed"]) for g in tl["segments"]])
    exp = [project(L, w["start"], w["end"])[0][0] for w in words if project(L, w["start"], w["end"]) and
           project(L, w["start"], w["end"])[0][1] - project(L, w["start"], w["end"])[0][0] > 0.35]
    got = onsets(out)
    n = min(len(exp), len(got), 30)
    assert n >= 20
    err = max(abs(a - b) for a, b in zip(exp[:n], got[:n]))
    assert err <= 0.05, f"audio onset error {err:.3f}s vs timeline"


def test_many_cuts_do_not_drift(fx, tmp_path):
    src, info, words, tl, _ = setup(fx, edits=["remove filler words", "remove silences longer than 1 second", "cut 0:10-0:12", "cut 0:20-0:23"])
    assert len(tl["segments"]) >= 6
    ver = media.render(src, tl, info, str(tmp_path / "o.mp4"))
    assert ver["length_diff_frames"] <= 1 and ver["av_offset_frames"] <= 1, ver


def test_speed_and_move_render_length(fx, tmp_path):
    src, info, words, tl, _ = setup(fx, edits=["speed up from 1:00 to 1:30 by 2x", "slow down 0:10-0:20 by 0.5x", "move 0:40-0:50 to the end"])
    ver = media.render(src, tl, info, str(tmp_path / "o.mp4"))
    assert abs(tl["duration_s"] - (150 - 15 + 10)) < 0.1
    assert ver["length_diff_frames"] <= 1 and ver["av_offset_frames"] <= 1, ver


def test_caption_visible_exactly_on_its_words(fx, tmp_path):
    edits = ["cut the first 120 seconds"]
    src, info, words, tl0, s = setup(fx, edits=edits)
    E.handle(s, {"words": words, "source": info}, "add a caption 'HELLO' when I say darn")
    tl = derive(s["entries"], words, info["duration_s"], info)
    cap = tl["captions"][0]
    a, b = cap["intervals"][0]
    with_cap, without = str(tmp_path / "w.mp4"), str(tmp_path / "n.mp4")
    media.render(src, tl, info, with_cap)
    media.render(src, tl0, info, without)
    region = (slice(250, 340), slice(200, 440))   # bottom-centre where the caption box sits
    inside = np.abs(gray_frame(with_cap, (a + b) / 2)[region] - gray_frame(without, (a + b) / 2)[region]).mean()
    outside = np.abs(gray_frame(with_cap, max(0.2, a - 2.0))[region] - gray_frame(without, max(0.2, a - 2.0))[region]).mean()
    assert inside > 8 and outside < 3, (inside, outside)


def test_bleep_replaces_word_and_mute_silences_it(fx, tmp_path):
    src, info, words, tl, _ = setup(fx, edits=["cut the first 100 seconds", "mute the word 'darn'"])
    out = str(tmp_path / "m.mp4")
    media.render(src, tl, info, out)
    a, b = tl["mutes"][0]
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", out, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.abs(np.frombuffer(raw, np.int16).astype(float)) / 32768
    assert x[int((a + .05) * 8000):int((b - .05) * 8000)].max() < 0.01          # silent where muted
    assert x[int((a - .5) * 8000):int((a - .1) * 8000)].max() > 0.05 or x[int((b + .1) * 8000):int((b + .5) * 8000)].max() > 0.05


def test_music_ducks_under_speech(fx, tmp_path):
    src, info, words, tl, _ = setup(fx, edits=["cut the first 90 seconds", "add background music"])
    out = str(tmp_path / "mu.mp4")
    media.render(src, tl, info, out)
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", out, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.abs(np.frombuffer(raw, np.int16).astype(float)) / 32768
    w = [w for w in words if w["start"] >= 92]
    speech = np.mean([x[int((q["start"] - 90 + .1) * 8000):int((q["end"] - 90 - .1) * 8000)].mean() for q in w[:6]])
    gap = x[int((tl["duration_s"] - 3) * 8000):int((tl["duration_s"] - 1) * 8000)]
    assert gap.mean() > 0.003, "music should be audible in gaps"
    assert speech > gap.mean() * 2


def test_vfr_and_noaudio_render(fx, tmp_path):
    src, info, words, tl, _ = setup(fx, "vfr", ["cut the first 100 seconds"])
    assert info["vfr"]
    ver = media.render(src, tl, info, str(tmp_path / "v.mp4"))
    assert ver["length_diff_frames"] <= 1 and ver["av_offset_frames"] <= 1
    src, info, words, tl, _ = setup(fx, "noaudio", ["cut the first 100 seconds", "add a caption 'hi' when I say darn"])
    ver = media.render(src, tl, info, str(tmp_path / "n.mp4"))
    assert ver["audio_s"] is None and ver["length_diff_frames"] <= 1


def test_render_never_modifies_source(fx, tmp_path):
    import hashlib
    src, info, words, tl, _ = setup(fx, edits=["cut the first 100 seconds"])
    h = lambda: hashlib.sha256(open(src, "rb").read()).hexdigest()  # noqa: E731
    before = h()
    media.render(src, tl, info, str(tmp_path / "o.mp4"))
    assert h() == before


def test_cut_snaps_to_silence_never_clips_a_word(fx):
    src, info, words, tl, s = setup(fx, edits=["cut the first 30.2 seconds"])   # 30.2 s falls inside a word in this transcript
    inside = [w for w in words if w["start"] + 0.02 < tl["segments"][0]["src_start"] < w["end"] - 0.02]
    assert not inside


def test_16khz_mono_source_keeps_all_audio(fx, tmp_path):
    """Regression: with implicit fan-out ffmpeg dropped the audio of later segments for 16 kHz mono AAC sources."""
    src16 = str(tmp_path / "s16.mp4")
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-i", os.path.join(fx, "single.mp4"), "-c:v", "copy", "-ar", "16000", "-ac", "1", "-c:a", "aac", src16], check=True)
    import shutil
    shutil.copyfile(os.path.join(fx, "single.words.json"), str(tmp_path / "s16.words.json"))
    src, info, words, tl, _ = setup(str(tmp_path), "s16", ["remove filler words", "remove silences longer than 1 second", "cut 0:10-0:12"])
    assert len(tl["segments"]) >= 4
    ver = media.render(src, tl, info, str(tmp_path / "o.mp4"))
    assert ver["length_diff_frames"] <= 1 and ver["av_offset_frames"] <= 1, ver
