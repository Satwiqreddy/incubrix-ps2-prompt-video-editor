"""Voice-pitch speaker estimate (used when Whisper transcribes; Whisper itself has no speakers).
Needs fixtures/real_speech.mp4 (python -m tools.make_speech_fixture, Windows) so it is skipped elsewhere."""
import json, os, subprocess
import numpy as np
import pytest
from app import transcript as T
from app.media import ffmpeg

HERE = os.path.join(os.path.dirname(__file__), "..", "fixtures")
MP4, JS = os.path.join(HERE, "real_speech.mp4"), os.path.join(HERE, "real_speech.words.json")


def audio(path):
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


@pytest.mark.skipif(not os.path.exists(MP4), reason="real_speech fixture not generated")
def test_two_voices_are_separated():
    words = json.load(open(JS))["words"]
    lab = T.diarize(audio(MP4), words)
    assert lab is not None and set(lab) == {"S1", "S2"}
    truth = {"A": "S1", "B": "S2"}
    assert sum(truth[w["speaker"]] == l for w, l in zip(words, lab)) / len(words) >= 0.95


def test_no_voice_means_no_speakers():
    words = [{"text": "x", "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(40)]
    assert T.diarize(np.zeros(16000 * 15, np.float32), words) is None
