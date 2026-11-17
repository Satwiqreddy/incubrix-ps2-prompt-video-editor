"""Test fixtures we own: scripted 'talking' videos. Audio is synthetic (a tone burst per word, so
word timing is audible and measurable), video is ffmpeg testsrc2. No real person's voice/face/content.
Each script line = (start_s, speaker, text). Word i of a line lasts 0.4 s with a 0.06 s gap."""
import json, os, subprocess, sys, wave
import numpy as np

SINGLE = [  # one speaker, 150 s. "welcome to the channel" starts at 42.0; pricing mentioned 3x; a long pause at 100-108; 'thanks' 4x
    (0.0, "A", "Hello and thanks for joining this short talk about building a small business."),
    (7.0, "A", "Um today we will cover three things, planning, pricing and growth."),
    (14.0, "A", "This is a real example, and it is quite boring honestly, but stay with me."),
    (23.0, "A", "I started with nothing but a laptop and a lot of coffee."),
    (30.0, "A", "Uh the first lesson is to start small and test everything."),
    (38.0, "A", "Anyway."),
    (42.0, "A", "Welcome to the channel."),
    (45.0, "A", "Let us begin with planning because planning saves money."),
    (52.0, "A", "Write down who your customers are and what they need."),
    (60.0, "A", "Now the pricing. Pricing is the first thing people ask about."),
    (68.0, "A", "Your cost should cover your time, your tools and a fair profit."),
    (76.0, "A", "I like to keep things simple, like a flat monthly fee."),
    (84.0, "A", "Next is growth, and growth comes from happy customers."),
    (92.0, "A", "Ask for feedback, listen carefully and improve every week."),
    (100.0, "A", "Thanks."),
    (108.0, "A", "Back to pricing for a moment, because the price should never be a surprise."),
    (116.0, "A", "Show the pricing page clearly and keep the plan names honest."),
    (124.0, "A", "Darn, I almost forgot to mention taxes, they matter too."),
    (131.0, "A", "Thanks again for watching, thanks for the comments."),
    (139.0, "A", "Subscribe if you liked it, and thanks one last time."),
]

TWO = [  # host (A) interviews guest (B), 120 s. Guest talks about their old job twice.
    (0.0, "A", "Welcome to the show, today I am talking with a founder."),
    (6.0, "A", "Tell me about your old job before you started the company?"),
    (12.0, "B", "I worked at a bank for ten years, it was my old job and I learned a lot."),
    (22.0, "B", "The job taught me discipline, but I wanted to build something of my own."),
    (32.0, "A", "How did you fund the first version?"),
    (38.0, "B", "Savings mostly, and a small loan from a friend."),
    (46.0, "A", "What did the first customers pay?"),
    (52.0, "B", "We charged a low price at the start, then raised the pricing twice."),
    (62.0, "A", "Do you ever miss the old job?"),
    (68.0, "B", "Sometimes, the old job had free lunch, but no, not really."),
    (78.0, "A", "Final question, what is your advice?"),
    (84.0, "B", "Start small, talk to customers and never stop learning."),
    (94.0, "A", "Thanks for coming on the show."),
    (99.0, "B", "Thanks for having me."),
]

FILLER_LIKE = {"like"}


def words_for(script, wd=0.4, gap=0.06):
    out = []
    for start, spk, text in script:
        t = start
        for tok in text.split():
            out.append({"text": tok, "start": round(t, 3), "end": round(t + wd, 3), "speaker": spk, "conf": 0.97})
            t += wd + gap
    return out


def make_video(script, path, seconds, fps=30, size="640x360", vfr=False, with_audio=True):
    w = words_for(script)
    sr = 48000
    n = int(seconds * sr)
    a = np.zeros(n, dtype=np.float32)
    tt = np.arange(n) / sr
    for i, x in enumerate(w):
        s, e = int(x["start"] * sr), int(x["end"] * sr)
        f = 220 + 25 * (i % 12) + (110 if x["speaker"] == "B" else 0)
        a[s:e] = 0.35 * np.sin(2 * np.pi * f * tt[s:e]) * np.minimum(1, np.minimum(np.arange(e - s), np.arange(e - s)[::-1]) / 200)
    wav = os.path.splitext(path)[0] + ".wav"
    with wave.open(wav, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((a * 32767).astype(np.int16).tobytes())
    from app.media import ffmpeg
    vin = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if vfr:  # variable frame rate: drop every 3rd frame by timestamp
        vf = ["-vf", "select='lt(mod(n,3),2)'", "-fps_mode", "vfr"]
    else:
        vf = []
    cmd = [ffmpeg(), "-y", "-v", "error", *vin] + (["-i", wav] if with_audio else []) + vf + \
          ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "30"] + (["-c:a", "aac", "-b:a", "128k"] if with_audio else []) + \
          ["-shortest", path]
    subprocess.run(cmd, check=True)
    os.remove(wav)
    with open(os.path.splitext(path)[0] + ".words.json", "w", encoding="utf-8") as f:
        json.dump({"words": w}, f)
    return w


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    out = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
    os.makedirs(out, exist_ok=True)
    make_video(SINGLE, os.path.join(out, "single.mp4"), 150)
    make_video(TWO, os.path.join(out, "two_speakers.mp4"), 105)
    make_video(SINGLE, os.path.join(out, "vfr.mp4"), 150, vfr=True)
    make_video(SINGLE, os.path.join(out, "noaudio.mp4"), 150, with_audio=False)
    print("fixtures written to", out)
