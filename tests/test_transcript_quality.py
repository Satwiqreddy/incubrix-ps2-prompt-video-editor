"""Imperfect data: overlapping speech, speaker-label swaps, low-confidence words, empty transcript."""
import pytest
from app import engine as E
from app.transcript import build, warnings


def w(text, s, e, spk="A", conf=1.0):
    return {"text": text, "start": s, "end": e, "speaker": spk, "conf": conf}


def test_overlapping_speech_warned():
    words = build([w("hello", 0, 1, "A"), w("there", 0.5, 1.5, "B"), w("friend", 2, 3, "A")])
    assert any("overlapping speech" in x for x in warnings(words))


def test_speaker_label_swap_warned():
    words = build([w("a", 0, .4, "A"), w("b", .5, .8, "B"), w("c", .9, 1.3, "A"), w("d", 1.4, 1.7, "B"), w("e", 1.8, 2.2, "A")])
    assert any("speaker-label swap" in x for x in warnings(words))


def test_low_confidence_flagged_and_clean_transcript_has_no_warnings():
    assert any("low-confidence" in x for x in warnings(build([w("darn", 0, 1, conf=0.2), w("ok", 1, 2)])))
    assert warnings(build([w("fine", 0, 1), w("words", 1, 2)])) == []


def test_warnings_reach_the_user_on_ingest(client, tmp_path):
    import json, os, subprocess
    from app.media import ffmpeg
    mp4 = str(tmp_path / "v.mp4")
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=6", "-f", "lavfi", "-i", "anoisesrc=d=6:a=0.02",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", mp4], check=True)
    tr = {"words": [w("hello", 0, 1, "A"), w("there", 0.5, 1.5, "B", 0.2), w("friend", 2, 3, "A")]}
    r = client.post("/api/v1/videos", files={"file": ("v.mp4", open(mp4, "rb"), "video/mp4"),
                                             "transcript": ("t.json", json.dumps(tr).encode(), "application/json")})
    j = client.get("/api/v1/jobs/" + r.json()["job_id"]).json()
    ws = " | ".join(j["result"]["warnings"])
    assert "overlapping speech" in ws and "low-confidence" in ws


def test_empty_transcript_edits_that_need_words_are_rejected():
    v = {"words": [], "source": {"duration_s": 10.0, "fps": 30.0, "has_audio": True}}
    s = E.new_session("s", "v")
    for say in ["remove filler words", "remove the part about pricing", "add a caption 'x' when I say hello"]:
        r = E.handle(s, v, say)
        assert r["status"] == "rejected", (say, r)
    assert E.handle(s, v, "cut the first 2 seconds")["status"] == "applied"      # time-based edits still work
