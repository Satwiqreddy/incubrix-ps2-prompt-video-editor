import pytest
from app import nlu


@pytest.mark.parametrize("s,v", [("1:05", 65), ("0:30", 30), ("80 seconds", 80), ("a minute twenty", 80), ("1 minute 20 seconds", 80),
                                 ("two minutes", 120), ("thirty seconds", 30), ("1:02:03", 3723), ("a minute", 60), ("5s", 5)])
def test_parse_time(s, v):
    assert nlu.parse_time(s) == v


def test_quoted_text_is_never_split():
    r = nlu.rules_parse("add a caption 'Hi, and bye' at 0:20")
    assert len(r["parts"]) == 1 and r["parts"][0]["text"] == "Hi, and bye"


def test_multipart_split_and_note():
    r = nlu.rules_parse("cut the first 5 seconds, speed up 1:00 to 1:10 by 2x and mute 'darn', but keep the music")
    assert [p["intent"] for p in r["parts"]] == ["cut", "speed", "mute"] and r["note"] == "the music"


def test_llm_output_is_validated_and_falls_back(monkeypatch):
    good = {"content": [{"text": '{"parts":[{"intent":"cut","where":{"kind":"first","n":5}}]}'}]}
    assert nlu.llm_parse("x", post=lambda b, h: good)["parts"][0]["intent"] == "cut"
    with pytest.raises(ValueError):
        nlu.llm_parse("x", post=lambda b, h: {"content": [{"text": '{"parts":[{"intent":"delete_database"}]}'}]})
    monkeypatch.setenv("NLU", "llm")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_API_URL", "http://127.0.0.1:9/none")   # provider down -> deterministic rules still answer
    assert nlu.parse("cut the first 5 seconds")["parts"][0]["intent"] == "cut"
