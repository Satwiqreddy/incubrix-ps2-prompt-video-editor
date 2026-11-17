"""Real-sounding speech test video (Windows only): two Windows system voices read a short interview; word timings come from the
synthesiser's own word events (no speech-to-text needed); picture = ffmpeg testsrc2.   python -m tools.make_speech_fixture [outdir]
Writes real_speech.mp4 + real_speech.words.json (upload both on the web page).
Licensing: Windows TTS output has unverified redistribution terms -> for your own local testing only; do not ship/publish it."""
import json, os, subprocess, sys, tempfile, wave
import numpy as np

# (voice, speaker label, silence before in seconds, text)
LINES = [
    ("David", "A", 0.8, "Hello and welcome to the channel. Today I am talking with a founder about building a small business."),
    ("Zira", "B", 0.6, "Thanks for having me. Um, I used to work at a bank, and that was my old job."),
    ("David", "A", 2.6, "Let us talk about pricing. How did you decide what to charge your first customers?"),
    ("Zira", "B", 0.6, "Well, we started with a low price, and then we raised the pricing twice as the product improved."),
    ("David", "A", 0.6, "That makes sense. What mistakes did you make with your plans and your cost?"),
    ("Zira", "B", 3.0, "Uh, I almost forgot to mention taxes. Darn, that was a big one, and it hurt our profit."),
    ("David", "A", 0.6, "What is your advice for new founders?"),
    ("Zira", "B", 0.6, "Start small, talk to customers every week, and keep the pricing simple. Thanks for watching, thanks again."),
]


def main(out="fixtures"):
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, ".."))
    from app.media import ffmpeg
    tmp, sr = tempfile.mkdtemp(prefix="tts_"), 16000
    audio, words, t = [], [], 0.0
    for i, (voice, spk, gap, text) in enumerate(LINES):
        w, j = os.path.join(tmp, f"{i}.wav"), os.path.join(tmp, f"{i}.json")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(here, "tts_windows.ps1"),
                        "-Wav", w, "-Json", j, "-Voice", voice, "-Text", text], check=True)
        r16 = os.path.join(tmp, f"{i}r.wav")
        subprocess.run([ffmpeg(), "-y", "-v", "error", "-i", w, "-ar", str(sr), "-ac", "1", r16], check=True)
        with wave.open(r16) as f:
            a = np.frombuffer(f.readframes(f.getnframes()), np.int16)
        ev = json.load(open(j, encoding="utf-8-sig"))
        toks = text.split()
        audio += [np.zeros(int(gap * sr), np.int16), a]
        t += gap
        dur = len(a) / sr
        for k, e in enumerate(ev):
            start = e["start"]
            guess = start + 0.09 * len(e["text"]) + 0.15          # a word is about this long; pauses between sentences are NOT part of a word
            end = min(guess, (ev[k + 1]["start"] - 0.04) if k + 1 < len(ev) else dur)
            words.append({"text": toks[k] if len(toks) == len(ev) else e["text"], "start": round(t + start, 3),
                          "end": round(t + max(end, start + 0.1), 3), "speaker": spk, "conf": 1.0})
        t += dur
    audio.append(np.zeros(int(0.8 * sr), np.int16))
    a = np.concatenate(audio)
    total = len(a) / sr
    os.makedirs(out, exist_ok=True)
    wav = os.path.join(out, "real_speech.wav")
    with wave.open(wav, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr); f.writeframes(a.tobytes())
    mp4 = os.path.join(out, "real_speech.mp4")
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={total:.2f}", "-i", wav,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "128k",
                    "-shortest", mp4], check=True)
    os.remove(wav)
    json.dump({"words": words}, open(os.path.join(out, "real_speech.words.json"), "w"), indent=1)
    print(f"{mp4}: {total:.1f}s, {len(words)} words")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
