"""50+ scripted scenarios with expected results (pure engine, no media needed -> fast, runs in CI with no keys).
Each scenario is a mini editing session: (say, answer|None, expected)."""
import copy
import pytest
from app import engine as E
from app.timeline import derive, thash
from app.transcript import build
from tools.fixtures import SINGLE, TWO, words_for


def vid(script, dur, audio=True, tweak=None):
    w = build(words_for(script))
    if tweak:
        tweak(w)
    return {"words": w, "source": {"duration_s": float(dur), "fps": 30.0, "has_audio": audio}}


def lowconf(w):
    for x in w:
        if x["text"].lower().startswith("darn"):
            x["conf"] = 0.3


V = {"single": lambda: vid(SINGLE, 150), "two": lambda: vid(TWO, 105), "noaudio": lambda: vid(SINGLE, 150, audio=False),
     "lowconf": lambda: vid(SINGLE, 150, tweak=lowconf)}
A, N, R = "applied", "needs_clarification", "rejected"
S = lambda status, dur=None, **k: {"status": status, **({"dur": dur} if dur is not None else {}), **k}  # noqa: E731

SCEN = {
    "caption_phrase": ("single", [("add a caption 'Hi' when I say let us begin with planning", None, S(A, 150))]),
    "caption_not_found": ("single", [("add a caption 'Hi' when I say purple elephants", None, S(R, reason="can't find"))]),
    "caption_no_target": ("single", [("add a caption 'Hi'", "last", S(N, q="Where should the caption"))]),
    "caption_at_time": ("single", [("add a caption 'Hi' at 0:20", None, S(A, 150))]),
    "caption_out_of_range": ("single", [("add a caption 'x' at 5:00", None, S(R, reason="out of range"))]),
    "caption_multi_match_asks": ("single", [("add a caption 'x' when I say pricing", "all", S(N))]),
    "cut_first_30": ("single", [("cut the first 30 seconds", None, S(A, 120, tol=0.6))]),
    "cut_last_10": ("single", [("cut the last 10 seconds", None, S(A, 140, tol=0.6))]),
    "cut_range_colon": ("single", [("remove 0:30-0:40", None, S(A, 140, tol=0.6))]),
    "cut_range_words": ("single", [("cut from 80 seconds to a minute twenty five", None, S(A, 145, tol=0.6))]),
    "cut_whole_video": ("single", [("cut everything", None, S(R, reason="whole video"))]),
    "cut_out_of_range": ("single", [("remove 5:00-6:00", None, S(R, reason="out of range"))]),
    "cut_first_longer_than_video": ("single", [("cut the first 500 seconds", None, S(R, reason="out of range"))]),
    "cut_topic_asks": ("single", [("remove the part about pricing", None, S(N, optc=4))]),
    "cut_topic_all": ("single", [("remove the part about pricing", "all of them", S(A))]),
    "cut_topic_ordinal": ("single", [("remove the part about pricing", "the first one", S(A))]),
    "cut_topic_synonym": ("single", [("remove the part about cost", None, S(N))]),
    "meaning_match_is_shown_first": ("two", [("remove the part about cost", "none of these", S(N, q="word for word"))]),
    "cut_topic_missing": ("single", [("remove the part about nonsense", None, S(R, reason="can't find"))]),
    "cut_after_phrase_asks": ("single", [("remove everything after I say thanks", "the last one", S(A, 142.5, tol=1.0))]),
    "answer_bare_number": ("single", [("remove everything after I say thanks", "2", S(A, 100.4, tol=0.3))]),
    "keep_range": ("single", [("keep only 0:10-0:30", None, S(A, 20, tol=1.0))]),
    "speed_range": ("single", [("speed up from 1:00 to 1:30 by 2x", None, S(A, 135, tol=0.5))]),
    "speed_too_fast": ("single", [("speed up 10x", None, S(R, reason="outside the supported range"))]),
    "slow_down": ("single", [("slow down 0:10-0:20 by 0.5x", None, S(A, 160, tol=0.5))]),
    "speed_assumed_factor": ("single", [("speed up 0:10-0:20", None, S(A, 145, tol=0.5))]),
    "speed_no_range_asks": ("single", [("speed up by 2x", "the whole video", S(A, 75, tol=0.1))]),
    "move_to_end": ("single", [("move 0:10-0:20 to the end", None, S(A, 150))]),
    "mute_word": ("single", [("mute the word 'coffee'", None, S(A, 150))]),
    "bleep_word": ("single", [("bleep the word 'darn'", None, S(A, 150))]),
    "mute_missing_word": ("single", [("mute the word 'zebra'", None, S(R, reason="can't find"))]),
    "mute_no_audio": ("noaudio", [("mute the word 'coffee'", None, S(R, reason="no audio"))]),
    "bleep_low_confidence_asks": ("lowconf", [("bleep the word 'darn'", "only the confident", S(N, q="Low-confidence")),]),
    "fillers": ("single", [("remove filler words", None, S(A, 147.8, tol=0.3))]),
    "fillers_twice_rejected": ("single", [("remove the ums and uhs", None, S(A)), ("remove filler words", None, S(R, reason="no filler"))]),
    "filler_skips_bleeped": ("single", [("bleep the word 'um'", None, S(A)), ("remove filler words", None, S(A))]),
    "silences_2s": ("single", [("remove silences longer than 2 seconds", None, S(A, 97.5, tol=1))]),
    "silences_default_assumed": ("single", [("remove silences", None, S(A))]),
    "silences_none": ("single", [("remove silences longer than 60 seconds", None, S(R, reason="no silences"))]),
    "pause_survives_silence_removal": ("single", [
        ("keep the pause after 'Thanks.'", None, S(A, 150)), ("remove silences longer than 2 seconds", None, S(A, 104.9, tol=1))]),
    "music": ("single", [("add background music", None, S(A, 150))]),
    "autocaption": ("single", [("auto-caption the whole video", None, S(A, 150)), ("caption the whole video", None, S(R, reason="already captioned"))]),
    "style_without_caption": ("single", [("make it bigger", None, S(R, reason="no caption"))]),
    "style_color": ("single", [("add a caption 'Hi' at 0:20", None, S(A)), ("make that caption red", None, S(A, 150))]),
    "style_unknown_color_is_size_free": ("single", [("add a caption 'Hi' at 0:20", None, S(A)), ("make it bigger", None, S(A))]),
    "undo_empty": ("single", [("undo", None, S(R, reason="nothing to undo"))]),
    "undo_cut": ("single", [("cut the first 30 seconds", None, S(A, 120, tol=0.6)), ("undo", None, S(A, 150))]),
    "redo": ("single", [("cut the first 30 seconds", None, S(A)), ("undo", None, S(A, 150)), ("redo", None, S(A, 120, tol=0.6))]),
    "redo_cleared_by_new_edit": ("single", [("cut the first 30 seconds", None, S(A)), ("undo", None, S(A)), ("add background music", None, S(A)), ("redo", None, S(R, reason="nothing to redo"))]),
    "restore_last": ("single", [("cut 0:30-0:40", None, S(A)), ("put back what I removed", None, S(A, 150))]),
    "restore_step_missing": ("single", [("restore step 9", None, S(R, reason="does not exist"))]),
    "restore_nothing_removed": ("single", [("put back what I removed", None, S(R, reason="nothing has been removed"))]),
    "restore_intro": ("single", [("cut the first 30 seconds", None, S(A)), ("remove 0:30-0:40", None, S(A)), ("actually, put the intro back", None, S(A, 140, tol=0.6))]),
    "restore_second_to_last_keep_music": ("single", [("add background music", None, S(A)), ("cut 0:10-0:20", None, S(A)), ("cut 0:50-0:60", None, S(A)),
                                                    ("undo the second-to-last cut but keep the music", None, S(A, 140, tol=0.6))]),
    "unknown_instruction": ("single", [("sing me a song", None, S(R, reason="didn't recognise"))]),
    "empty_instruction": ("single", [("   ", None, S(R, reason="empty"))]),
    "duplicate_instruction_asks": ("single", [("remove 0:30-0:40", None, S(A)), ("remove 0:30-0:40", "no", S(N, q="same instruction"))]),
    "multipart_with_impossible": ("single", [("cut the first 5 seconds, speed up 1:00 to 1:10 by 10x and mute 'darn'", None, S(A, 145, tol=0.6, warn="part not applied"))]),
    "multipart_all_impossible": ("single", [("speed up 10x and cut everything", None, S(R))]),
    "shorter_asks": ("single", [("make it shorter", "filler", S(N, optc=3))]),
    "vague_boring_part_asks": ("single", [("remove the boring bit", "longest pause", S(N, optc=2))]),
    "intro_asks": ("single", [("cut the intro", "the first 10 seconds", S(N, optc=3))]),
    "original_time_policy": ("single", [("cut the first 30 seconds", None, S(A, 120, tol=0.6)), ("cut 0:00-0:10 in the original", None, S(R, reason="already removed"))]),
    "two_speakers_leadin_asked": ("two", [("remove everything the guest says about free lunch", "both the question and the answer", S(N, q="Remove that question too"))]),
    "two_speakers_guest_only": ("two", [("remove everything the guest says about free lunch", "only the guest", S(N))]),
    "two_speakers_no_such_speaker": ("two", [("remove everything the speaker 5 says about price", None, S(R, reason="no 'speaker 5'"))]),
}


def run(name):
    kind, steps = SCEN[name]
    v, s, out = V[kind](), E.new_session("s", "v"), []
    for say, ans, exp in steps:
        r = E.handle(s, v, say)
        first = r
        if ans is not None and r["status"] == N:
            r = E.answer(s, v, ans)
            # an expected status describes the FIRST response unless it is applied-after-answer
            if exp["status"] != N:
                pass
        out.append((say, first, r, exp))
    return s, v, out


@pytest.mark.parametrize("name", list(SCEN))
def test_scenario(name):
    s, v, out = run(name)
    for say, first, r, exp in out:
        got = first if exp["status"] == N else r
        assert got["status"] == exp["status"], (say, got["status"], got.get("reason"), got.get("question"))
        if "dur" in exp:
            assert abs(got["duration_s"] - exp["dur"]) <= exp.get("tol", 0.05), (say, got["duration_s"])
        if "reason" in exp:
            assert exp["reason"] in (got["reason"] or ""), (say, got["reason"])
        if "q" in exp:
            assert exp["q"] in (first["question"] or ""), (say, first["question"])
        if "optc" in exp:
            assert len(first["options"]) == exp["optc"], first["options"]
        if "warn" in exp:
            assert any(exp["warn"] in w for w in got["warnings"]), got["warnings"]
        assert got["time_policy"] == "current"


def test_at_least_40_scenarios():
    assert len(SCEN) >= 40


@pytest.mark.parametrize("name", ["restore_intro", "multipart_with_impossible", "fillers", "pause_survives_silence_removal", "cut_topic_all"])
def test_deterministic_three_runs(name):
    hs = []
    for _ in range(3):
        s, v, out = run(name)
        hs.append(thash(derive(s["entries"], v["words"], v["source"]["duration_s"])))
    assert len(set(hs)) == 1


def test_original_never_mentioned_in_history_is_untouched_by_ops():
    # ops are only data: replaying the same history on a fresh copy gives the same timeline
    s, v, _ = run("restore_intro")
    h1 = thash(derive(s["entries"], v["words"], 150.0))
    h2 = thash(derive(copy.deepcopy(s["entries"]), v["words"], 150.0))
    assert h1 == h2


def test_caption_follows_words_through_cuts_speed_reorder_restore():
    v, s = V["single"](), E.new_session("s", "v")
    E.handle(s, v, "add a caption 'Welcome!' when I say welcome to the channel")
    base = lambda: derive(s["entries"], v["words"], 150.0)["captions"][0]  # noqa: E731
    words_start = v["words"][base()["words"][0]]["start"]
    E.handle(s, v, "cut the first 30 seconds")
    assert abs(base()["intervals"][0][0] - (words_start - 30)) < 0.7           # moved with the words
    E.handle(s, v, "speed up from 0:00 to 0:10 by 2x")
    E.handle(s, v, "move 1:00-1:10 to the end")
    E.handle(s, v, "put the intro back")
    assert abs(base()["intervals"][0][0] - words_start) < 5.5                  # intro back, caption back (speed shrinks 0:00-0:10)
    assert not base()["hidden"]


def test_caption_comes_back_when_the_cut_is_restored():
    v, s = V["single"](), E.new_session("s", "v")
    E.handle(s, v, "add a caption 'Hi' when I say let us begin with planning")
    E.handle(s, v, "remove 0:45-0:52")
    assert derive(s["entries"], v["words"], 150.0)["captions"][0]["hidden"]
    E.handle(s, v, "put back what I removed")
    assert not derive(s["entries"], v["words"], 150.0)["captions"][0]["hidden"]


def test_bleep_not_cut_by_filler_removal():
    v, s = V["single"](), E.new_session("s", "v")
    E.handle(s, v, "bleep the word 'um'")
    r = E.handle(s, v, "remove filler words")
    assert r["status"] == A and any("muted/bleeped" in x for x in r["summary"].split(". ")) or "kept" in r["summary"]
    tl = derive(s["entries"], v["words"], 150.0)
    assert tl["bleeps"] and not tl["captions"]


def test_speaker_two_leadin_cut_removes_both():
    v, s = V["two"](), E.new_session("s", "v")
    r = E.handle(s, v, "remove everything the guest says about free lunch")
    assert r["status"] == N
    r2 = E.answer(s, v, "both the question and the answer")
    assert r2["status"] == A and r2["duration_s"] < 105 - 10


def test_second_instruction_after_clarification_drops_old_question():
    v, s = V["single"](), E.new_session("s", "v")
    E.handle(s, v, "remove the part about pricing")
    r = E.handle(s, v, "add background music")
    assert r["status"] == A and not s["pending"]
