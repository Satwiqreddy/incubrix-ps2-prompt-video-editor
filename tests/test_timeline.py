from app.timeline import derive, thash, to_source, layout, fold

W = [{"id": i, "text": f"w{i}", "start": float(i), "end": i + 0.9} for i in range(60)]


def d(entries):
    return derive(entries, W, 60.0)


def test_caption_sticks_to_words_after_cut_and_restore():
    cap = {"id": 1, "ops": [{"op": "caption", "text": "hi", "words": [42, 43]}]}
    cut = {"id": 2, "ops": [{"op": "cut", "src": [0, 30]}]}
    assert d([cap])["captions"][0]["intervals"] == [[42.0, 43.9]]
    assert d([cap, cut])["captions"][0]["intervals"] == [[12.0, 13.9]]          # moved with words
    back = {"id": 3, "ops": [{"op": "restore", "target": 2}]}
    assert d([cap, cut, back])["captions"][0]["intervals"] == [[42.0, 43.9]]    # restored
    assert thash(d([cap, cut, back])) == thash(d([cap]))


def test_caption_hidden_when_words_cut():
    cap = {"id": 1, "ops": [{"op": "caption", "text": "hi", "words": [5, 6]}]}
    t = d([cap, {"id": 2, "ops": [{"op": "cut", "src": [4, 8]}]}])
    assert t["captions"][0]["hidden"] and t["warnings"]


def test_speed_changes_duration_and_caption_min_length():
    cap = {"id": 1, "ops": [{"op": "caption", "text": "x", "words": [10, 10]}]}
    t = d([cap, {"id": 2, "ops": [{"op": "speed", "src": [0, 60], "factor": 4}]}])
    assert t["duration_s"] == 15.0
    iv = t["captions"][0]["intervals"][0]
    assert iv[1] - iv[0] >= 0.7                                                  # readable minimum


def test_move_reorders_and_overlays_follow():
    cap = {"id": 1, "ops": [{"op": "caption", "text": "x", "words": [10, 10]}]}
    t = d([cap, {"id": 2, "ops": [{"op": "move", "src": [10, 20], "to": "end"}]}])
    assert t["duration_s"] == 60.0 and t["captions"][0]["intervals"] == [[50.0, 50.9]]


def test_current_time_maps_back_to_source():
    L = layout(fold([{"id": 1, "ops": [{"op": "cut", "src": [0, 30]}]}], W, 60)["segs"])
    assert to_source(L, 10, 20) == [(40.0, 50.0)]


def test_restore_can_be_restored():
    es = [{"id": 1, "ops": [{"op": "cut", "src": [0, 10]}]}, {"id": 2, "ops": [{"op": "restore", "target": 1}]},
          {"id": 3, "ops": [{"op": "restore", "target": 2}]}]
    assert d(es)["duration_s"] == 50.0
