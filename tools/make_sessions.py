"""Writes the scripted session pack (sessions/*.yaml). Run: python -m tools.make_sessions
Each step: say, optional answer (used if the system asks), expect (checked by the runner)."""
import json, os, yaml

A, N, R = "applied", "needs_clarification", "rejected"


def st(say, answer=None, status=None, dur=None, first=None, **kw):
    d = {"say": say}
    if answer:
        d["answer"] = answer
    exp = {}
    if status:
        exp["status"] = status
    if dur is not None:
        exp["duration_s"] = dur
    exp.update(kw)
    if exp:
        d["expect"] = exp
    if first:
        d["expect_first"] = {"status": first}
    return d


SESSIONS = {
    "02_caption_cut_restore": ("single.mp4", [
        st("add a caption 'Let us plan' when I say let us begin with planning", status=A, dur=150.0),
        st("remove 0:45-0:52", status=A),                       # caption words are cut -> caption hidden (warning)
        st("put back what I removed", status=A, dur=150.0),     # caption returns
        st("render", status=A)]),
    "03_reorder_mute_filler": ("single.mp4", [
        st("move 0:10-0:20 to the end", status=A, dur=150.0),
        st("mute the word 'coffee'", status=A),
        st("bleep the word 'darn'", status=A),
        st("remove filler words", status=A),
        st("remove filler words", status=R),
        st("render", status=A)]),
    "04_time_policy_and_formats": ("single.mp4", [
        st("cut from 80 seconds to a minute twenty five", status=A),
        st("cut the last 10 seconds", status=A),
        st("add a caption 'x' at 5:00", status=R),             # out of range on the shorter video
        st("cut 0:00-0:10 in the original", status=A),
        st("speed up from 1:20 to 1:30 by 2x", status=A),
        st("render", status=A)]),
    "05_repeat_and_impossible": ("single.mp4", [
        st("remove 0:30-0:40", status=A),
        st("remove 0:30-0:40", answer="no", first=N, status=R),
        st("speed up 10x", status=R),
        st("cut everything", status=R),
        st("cut the first 5 seconds, speed up 1:00 to 1:10 by 10x and mute 'darn'", status=A),
        st("render", status=A)]),
    "06_undo_redo_restore": ("single.mp4", [
        st("add background music", status=A),
        st("cut 0:10-0:20", status=A, dur=140.2),
        st("cut 0:50-0:60", status=A, dur=130.2),
        st("undo the second-to-last cut but keep the music", status=A, dur=140.0),
        st("undo", status=A),
        st("redo", status=A),
        st("cut 0:00-0:05", status=A),
        st("redo", status=R),
        st("render", status=A)]),
    "07_thanks_four_times": ("single.mp4", [
        st("remove everything after I say thanks", answer="the last one", first=N, status=A),
        st("render", status=A)]),
    "08_vague_references": ("single.mp4", [
        st("remove the boring bit", answer="the longest pause", first=N, status=A),
        st("make it shorter", answer="remove filler words", first=N, status=A),
        st("make it bigger", status=R),                         # no caption exists yet
        st("add a caption 'Hi' at 0:20", status=A),
        st("add a caption 'Bye' at 1:30", status=A),
        st("make that caption red", answer="the last one", first=N, status=A),
        st("render", status=A)]),
    "09_fillers_silences_protected_pause": ("single.mp4", [
        st("keep the pause after 'Thanks.'", status=A),
        st("remove silences longer than 2 seconds", status=A, dur=104.9, tol=1),
        st("remove filler words", status=A),
        st("render", status=A)]),
    "10_music_ducking": ("single.mp4", [
        st("cut the first 90 seconds", status=A),
        st("add background music", status=A),
        st("render", status=A)]),
    "11_two_speakers_leadin": ("two_speakers.mp4", [
        st("remove everything the guest says about free lunch", answer="both the question and the answer", first=N, status=A),
        st("render", status=A)]),
    "12_two_speakers_captions": ("two_speakers.mp4", [
        st("auto-caption the whole video", status=A),
        st("caption the whole video", status=R),
        st("remove the part about price", status=A),
        st("render", status=A)]),
    "13_variable_frame_rate": ("vfr.mp4", [
        st("cut the first 100 seconds", status=A),
        st("add a caption 'VFR' when I say darn", status=A),
        st("render", status=A)]),
    "14_no_audio": ("noaudio.mp4", [
        st("mute the word 'coffee'", status=R),
        st("add a caption 'silent film' when I say coffee", status=A),
        st("cut the first 100 seconds", status=A),
        st("render", status=A)]),
    "15_autocaption_speed_move": ("single.mp4", [
        st("auto-caption the whole video", status=A),
        st("speed up from 1:00 to 1:30 by 2x", status=A),
        st("move 0:40-0:50 to the end", status=A),
        st("make that caption red", answer="all captions", first=N, status=A),
        st("render", status=A)]),
    "16_low_confidence_bleep": ("single.mp4", [
        st("bleep the word 'darn'", answer="only the confident", first=N, status=R),
        st("bleep the word 'darn'", answer="all, including low-confidence", first=N, status=A),
        st("render", status=A)], "lowconf.words.json"),
    "17_conflicts": ("single.mp4", [
        st("remove the part about pricing", answer="all of them", first=N, status=A),
        st("remove the part about pricing", status=R),           # already gone
        st("put back the pricing part", answer="all of them", status=A),
        st("remove silences longer than 60 seconds", status=R),
        st("sing me a song", status=R),
        st("render", status=A)]),
    "18_stress_many_edits": ("single.mp4", [
        st("remove filler words", status=A), st("remove silences longer than 1 second", status=A),
        st("cut 0:10-0:12", status=A), st("cut 0:20-0:23", status=A),
        st("speed up from 0:30 to 0:40 by 1.5x", status=A), st("move 0:50-0:55 to the start", status=A),
        st("add a caption 'Many cuts' when I say growth", answer="all of them", first=N, status=A),
        st("mute the word 'hello'", status=A), st("add background music", status=A),
        st("render", status=A)]),
}


def main():
    here = os.path.join(os.path.dirname(__file__), "..")
    os.makedirs(os.path.join(here, "sessions"), exist_ok=True)
    w = json.load(open(os.path.join(here, "fixtures", "single.words.json")))
    for x in w["words"]:
        if x["text"].lower().startswith("darn"):
            x["conf"] = 0.3
    json.dump(w, open(os.path.join(here, "fixtures", "lowconf.words.json"), "w"))
    for name, spec in SESSIONS.items():
        video, steps = spec[0], spec[1]
        doc = {"video": f"../fixtures/{video}", "steps": steps}
        if len(spec) > 2:
            doc["transcript"] = f"../fixtures/{spec[2]}"
        # 'tol' is used only by humans reading the file; the runner's numeric check already allows 0.05 s
        for s in steps:
            s.get("expect", {}).pop("tol", None)
        with open(os.path.join(here, "sessions", name + ".yaml"), "w", encoding="utf-8") as f:
            f.write(f"# scripted session: {name}\n")
            yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True, width=120)
    print(len(SESSIONS), "sessions written")


if __name__ == "__main__":
    main()
