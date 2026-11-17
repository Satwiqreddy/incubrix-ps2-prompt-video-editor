"""ffmpeg: probe, measure, render. Rendering is a separate step that reads the untouched original."""
import os, re, shutil, subprocess, tempfile
from .config import cfg, env
from .errors import ApiError

SR = 48000


def ffmpeg():
    b = env("FFMPEG_BIN") or shutil.which("ffmpeg")
    if not b:
        try:
            import imageio_ffmpeg
            b = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            raise ApiError("CONFIG_INVALID", "ffmpeg not found (set FFMPEG_BIN)", 500)
    return b


def _run(args, **kw):
    return subprocess.run([ffmpeg(), "-hide_banner", *args], capture_output=True, text=True, errors="replace", **kw)


def _hms(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


def probe(path):
    r = _run(["-i", path])
    err = r.stderr
    m = re.search(r"Duration: (\d+:\d+:\d+\.\d+)", err)
    v = re.search(r"Stream #\d+:\d+.*?Video: .*?(\d{2,5})x(\d{2,5})", err)
    if not m or not v:
        raise ApiError("UNSUPPORTED_MEDIA", "not a readable video file (need MP4/MOV with a video stream)", 415)
    fps = re.search(r"(\d+(?:\.\d+)?) fps", err)
    vf = _run(["-i", path, "-an", "-vf", "vfrdet", "-f", "null", "-"]).stderr
    vm = re.search(r"VFR:([\d.]+) \((\d+)/(\d+)\)", vf)
    nfr = int(vm.group(2)) + int(vm.group(3)) if vm else 0
    dur = _hms(m.group(1))
    fps_v = float(fps.group(1)) if fps else 30.0
    vfr = bool(vm and float(vm.group(1)) > 0.01)
    if vfr:  # variable frame rate: the render normalises to constant 30 fps
        fps_v = 30.0
    return {"duration_s": round(dur, 3), "fps": fps_v, "width": int(v.group(1)), "height": int(v.group(2)),
            "has_audio": bool(re.search(r"Stream #\d+:\d+.*?Audio:", err)), "vfr": vfr}


def measure(path):
    """Actual stream lengths from the container: (video_s, audio_s|None, video_frames)."""
    def one(sel):
        e = _run(["-i", path, "-map", sel, "-c", "copy", "-f", "null", "-"]).stderr
        t = re.findall(r"time=(\d+:\d+:\d+\.\d+)", e)
        fr = re.findall(r"frame=\s*(\d+)", e)
        return (_hms(t[-1]) if t else None), (int(fr[-1]) if fr else 0)
    v, fr = one("0:v:0")
    a = one("0:a:0")[0] if "Audio:" in _run(["-i", path]).stderr else None
    return v, a, fr


def _atempo(s):
    out = []
    while s > 2.0 + 1e-9:
        out.append("atempo=2.0"); s /= 2.0
    while s < 0.5 - 1e-9:
        out.append("atempo=0.5"); s /= 0.5
    out.append(f"atempo={s:.6f}")
    return ",".join(out)


def _en(ivs):
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ivs) or "0"


def build_graph(tl, info, tmp, has_music_input):
    F, N = info["fps"], len(tl["segments"])
    ha = info["has_audio"]
    # explicit split/asplit: relying on ffmpeg's implicit fan-out silently DROPPED audio for some sources (found on 16 kHz mono AAC)
    g = [f"[0:v:0]fps={F},split={N}" + "".join(f"[v0_{i}]" for i in range(N))]
    if ha:
        g.append(f"[0:a:0]aresample={SR},asplit={N}" + "".join(f"[a0_{i}]" for i in range(N)))
    for i, s in enumerate(tl["segments"]):
        sf, ef = round(s["src_start"] * F), max(round(s["src_start"] * F) + 1, round(s["src_end"] * F))
        sp = "" if s["speed"] == 1 else f",setpts=PTS/{s['speed']}"
        g.append(f"[v0_{i}]trim=start_frame={sf}:end_frame={ef},setpts=PTS-STARTPTS{sp}[v{i + 1}]")
        if ha:
            ss, es = round(sf / F * SR), round(ef / F * SR)
            at = "" if s["speed"] == 1 else "," + _atempo(s["speed"])
            g.append(f"[a0_{i}]atrim=start_sample={ss}:end_sample={es},asetpts=PTS-STARTPTS{at}[a{i + 1}]")
    ins = "".join(f"[v{i + 1}]" + (f"[a{i + 1}]" if ha else "") for i in range(N))
    g.append(f"{ins}concat=n={N}:v=1:a={1 if ha else 0}[vc]" + ("[ac]" if ha else ""))
    # captions
    cur, k = "vc", 0
    scale_h = info["height"] / 18
    for c in tl["captions"]:
        if c["hidden"]:
            continue
        fn = f"cap{k}.txt"
        with open(os.path.join(tmp, fn), "w", encoding="utf-8") as f:
            f.write(c["text"])
        st = c["style"]
        col = st.get("color", "white")
        size = max(10, int(scale_h * st.get("scale", 1.0)))
        en = _en(c["intervals"])
        g.append(f"[{cur}]drawtext=fontfile=font.ttf:textfile={fn}:fontcolor={col}:fontsize={size}:box=1:boxcolor=black@0.55:"
                 f"boxborderw=12:x=(w-text_w)/2:y=h-text_h-h/10:enable='{en}'[vt{k}]")
        cur, k = f"vt{k}", k + 1
    g.append(f"[{cur}]format=yuv420p[vout]")
    # audio: mute / bleep / music
    last = None
    if ha:
        last = "ac"
        gate = tl["mutes"] + tl["bleeps"]
        if gate:
            g.append(f"[{last}]volume=enable='{_en(gate)}':volume=0[am]")
            last = "am"
    beep = None
    if tl["bleeps"]:
        T = tl["duration_s"]
        g.append(f"sine=frequency=1000:sample_rate={SR}:duration={T:.3f},volume='if({_en(tl['bleeps'])},0.35,0)':eval=frame[beep]")
        beep = "beep"
    m = tl.get("music")
    if m:
        T = tl["duration_s"]
        g.append(f"aevalsrc='0.10*sin(2*PI*220*t)+0.08*sin(2*PI*277.18*t)+0.08*sin(2*PI*329.63*t)':s={SR}:d={T:.3f},"
                 f"volume={m['gain_db'] + 14}dB[mus]")
        if last:
            g.append(f"[{last}]asplit=2[sp1][sp2]")
            g.append(f"[mus][sp1]sidechaincompress=threshold=0.02:ratio=12:attack=20:release=400[duck]")
            mix = ["sp2", "duck"] + ([beep] if beep else [])
        else:
            mix = ["mus"] + ([beep] if beep else [])
        g.append("".join(f"[{x}]" for x in mix) + f"amix=inputs={len(mix)}:normalize=0[aout]")
        last = "aout"
    elif beep and last:
        g.append(f"[{last}][{beep}]amix=inputs=2:normalize=0[aout]")
        last = "aout"
    elif beep:
        last = beep
    return ";\n".join(g), last


def render(source, tl, info, out_path, progress=None):
    if tl["duration_s"] <= 0:
        raise ApiError("RENDER_FAILED", "empty timeline", 422)
    tmp = tempfile.mkdtemp(prefix="render_")
    try:
        font = cfg.font
        if not os.path.exists(font):
            for alt in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf"):
                if os.path.exists(alt):
                    font = alt
                    break
        shutil.copyfile(font, os.path.join(tmp, "font.ttf"))
        graph, alast = build_graph(tl, info, tmp, False)
        with open(os.path.join(tmp, "graph.txt"), "w", encoding="utf-8") as f:
            f.write(graph)
        ver = re.search(r"version\D*(\d+)", _run(["-version"]).stdout)
        fcs = ["-/filter_complex", "graph.txt"] if ver and int(ver.group(1)) >= 7 else ["-filter_complex_script", "graph.txt"]
        cmd = [ffmpeg(), "-hide_banner", "-y", "-v", "error", "-progress", "pipe:1", "-nostats", "-i", os.path.abspath(source), *fcs,
               "-map", "[vout]"] + (["-map", f"[{alast}]"] if alast else ["-an"]) + \
              ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(info["fps"])] + \
              (["-c:a", "aac", "-b:a", "160k", "-ar", str(SR)] if alast else []) + \
              ["-t", f"{tl['duration_s']:.3f}", "-movflags", "+faststart", os.path.abspath(out_path)]
        p = subprocess.Popen(cmd, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        for line in p.stdout:
            m = re.match(r"out_time_us=(\d+)", line)
            if m and progress:
                progress(min(99, int(int(m.group(1)) / 1e6 / tl["duration_s"] * 100)))
        err = p.stderr.read()
        if p.wait() != 0:
            raise ApiError("RENDER_FAILED", "ffmpeg failed", 500, False, {"stderr": err[-1500:]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    v, a, fr = measure(out_path)
    v = fr / info["fps"]  # frame count is exact; container timestamps are not
    return {"video_s": round(v, 3), "audio_s": a, "frames": fr, "expected_s": tl["duration_s"],
            "length_diff_frames": round(abs(v - tl["duration_s"]) * info["fps"], 2),
            "av_offset_frames": round(abs(v - a) * info["fps"], 2) if a is not None else None}
