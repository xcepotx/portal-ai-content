import os, sys, json, inspect, subprocess, shutil
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)


def _run(cmd: list[str]):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok = (p.returncode == 0)
        err = (p.stderr or "")[-2500:]
        return ok, err
    except Exception as e:
        return False, str(e)


def _find_newest_mp4(root: Path) -> str | None:
    try:
        cand = sorted([p for p in root.rglob("*.mp4") if p.is_file()], key=lambda x: x.stat().st_mtime, reverse=True)
        if cand:
            return str(cand[0].resolve())
    except Exception:
        pass
    return None


def _mix_bgm_ffmpeg(inp_mp4: str, bgm_dir: str, vol: float) -> str:
    bgm_dir = str(bgm_dir or "").strip()
    if (not bgm_dir) or (not os.path.isdir(bgm_dir)):
        print("[BGM] bgm_dir not found:", bgm_dir, flush=True)
        return inp_mp4

    files = [os.path.join(bgm_dir, f) for f in os.listdir(bgm_dir) if f.lower().endswith(".mp3")]
    if not files:
        print("[BGM] No mp3 found in:", bgm_dir, flush=True)
        return inp_mp4

    import random
    bgm = random.choice(files)

    inp = Path(inp_mp4).resolve()
    out_mp4 = str(inp.with_name(inp.stem + "_bgm.mp4"))

    af = (
        f"[0:a]volume=1.0[a0];"
        f"[1:a]volume={float(vol):.3f}[bgm];"
        f"[a0][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    # attempt 1: copy video
    cmd1 = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-stream_loop", "-1", "-i", bgm,
        "-filter_complex", af,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        out_mp4
    ]
    ok, err = _run(cmd1)
    if ok:
        print("[BGM] mixed (copy v):", bgm, "->", out_mp4, flush=True)
        return out_mp4

    print("[BGM][WARN] copy mix failed, retry re-encode. err:", err, flush=True)

    # attempt 2: re-encode video
    cmd2 = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-stream_loop", "-1", "-i", bgm,
        "-filter_complex", af,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-b:v", "3000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        out_mp4
    ]
    ok, err = _run(cmd2)
    if ok:
        print("[BGM] mixed (re-encode):", bgm, "->", out_mp4, flush=True)
        return out_mp4

    print("[BGM][WARN] ffmpeg mix failed:", err, flush=True)
    return inp_mp4


def _apply_avatar_postprocess(mp4_path: str, avatar_id: str, scale: float, avatars_root: str) -> str:
    mp4_path = str(mp4_path or "").strip()
    if (not mp4_path) or (not os.path.exists(mp4_path)):
        print("[AVATAR][WARN] mp4 not found, skip", flush=True)
        return mp4_path

    if shutil.which("rhubarb") is None:
        print("[AVATAR][WARN] rhubarb not found in PATH, skip", flush=True)
        return mp4_path

    avatar_root = Path(str(avatars_root or "assets/avatars")).expanduser().resolve()
    avatar_dir = (avatar_root / str(avatar_id)).resolve()
    base_png = avatar_dir / "char_base_cat.png"

    if not base_png.exists():
        print("[AVATAR][WARN] base png missing:", str(base_png), flush=True)
        return mp4_path

    work = Path(mp4_path).resolve().parent / "_avatar_tmp"
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "audio.wav"
    cues = work / "mouth_cues.json"

    ok, err = _run(["ffmpeg", "-y", "-i", mp4_path, "-vn", "-ac", "1", "-ar", "48000", str(wav)])
    if (not ok) or (not wav.exists()):
        print("[AVATAR][WARN] ffmpeg wav fail:", err, flush=True)
        return mp4_path

    ok, err = _run(["rhubarb", "-r", "phonetic", "-f", "json", "-o", str(cues), str(wav)])
    if (not ok) or (not cues.exists()):
        print("[AVATAR][WARN] rhubarb fail:", err, flush=True)
        return mp4_path

    try:
        import json as _json
        from moviepy import VideoFileClip, ImageClip, CompositeVideoClip  # ✅ MoviePy v2

        # --- helpers (v2-first, v1 fallback) ---
        def _dur(c, d):
            return c.with_duration(d) if hasattr(c, "with_duration") else c.set_duration(d)

        def _start(c, t):
            return c.with_start(t) if hasattr(c, "with_start") else c.set_start(t)

        def _pos(c, p):
            if hasattr(c, "with_position"):
                return c.with_position(p)
            if hasattr(c, "set_position"):
                return c.set_position(p)
            if hasattr(c, "set_pos"):
                return c.set_pos(p)
            return c

        def _aud(c, a):
            return c.with_audio(a) if hasattr(c, "with_audio") else c.set_audio(a)

        def _resize_h(c, h):
            # moviepy v2 biasanya masih ada resize()
            if hasattr(c, "resize"):
                return c.resize(height=h)
            if hasattr(c, "resized"):
                return c.resized(height=h)
            return c

        v = VideoFileClip(mp4_path)
        dur = float(getattr(v, "duration", 0.0) or 0.0)
        fps0 = int(getattr(v, "fps", 30) or 30)
        vw = int(getattr(v, "w", 720) or 720)
        vh = int(getattr(v, "h", 1280) or 1280)

        data = _json.loads(cues.read_text(encoding="utf-8"))
        mouth_cues = data.get("mouthCues", []) or []

        base = ImageClip(str(base_png))
        base = _dur(base, dur)
        layers = [base]

        for cue in mouth_cues:
            st = float(cue.get("start", 0))
            en = float(cue.get("end", st))
            if en <= st:
                continue
            mouth_png = avatar_dir / ("mouth_%s.png" % (str(cue.get("value") or "X").strip()))
            if mouth_png.exists():
                ic = ImageClip(str(mouth_png))
                ic = _start(ic, st)
                ic = _dur(ic, en - st)
                layers.append(ic)

        avatar = CompositeVideoClip(layers, size=base.size)
        avatar = _dur(avatar, dur)

        target_h = max(80, int(vh * float(scale)))
        avatar = _resize_h(avatar, target_h)

        mx = int(vw * 0.02)
        my = int(vh * 0.02)
        ax = max(0, vw - int(getattr(avatar, "w", 0) or 0) - mx)
        ay = max(0, vh - int(getattr(avatar, "h", 0) or 0) - my)
        avatar = _pos(avatar, (ax, ay))

        out = CompositeVideoClip([v, avatar], size=(vw, vh))
        out = _dur(out, dur)
        out = _aud(out, v.audio)

        out_path = str(Path(mp4_path).with_name(Path(mp4_path).stem + "_avatar.mp4"))
        out.write_videofile(
            out_path,
            fps=fps0,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            bitrate="3000k",
            audio_bitrate="128k",
            threads=2,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        )

        try:
            v.close()
            out.close()
        except Exception:
            pass

        print("[AVATAR] applied ->", out_path, flush=True)
        return out_path

    except Exception as e:
        print("[AVATAR][WARN] postprocess error:", type(e).__name__, str(e), flush=True)
        return mp4_path


def main():
    # args: <tmp_json> <tmp_cfg>
    if len(sys.argv) >= 3:
        tmp_json = sys.argv[1]
        tmp_cfg = sys.argv[2]
    else:
        raise SystemExit("Usage: python -u _runner_long.py <TEMP_JSON> <TEMP_CFG>")

    data = json.loads(Path(tmp_json).read_text(encoding="utf-8"))
    cfg = json.loads(Path(tmp_cfg).read_text(encoding="utf-8"))

    repo_root = str(cfg.get("repo_root") or "").strip()
    if repo_root:
        # ensure import path
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        os.chdir(repo_root)
        print("[LONG] chdir ->", os.getcwd(), flush=True)

    # refresh images cache if requested
    if bool(cfg.get("refresh_images", False)):
        try:
            shutil.rmtree("temp_long", ignore_errors=True)
            print("[IMG] temp_long cleared (refresh_images=ON)", flush=True)
        except Exception as e:
            print("[IMG][WARN] failed clear temp_long:", type(e).__name__, str(e), flush=True)

    # import after sys.path + chdir
    from ytlong.engine import build_long_video

    kwargs = {
        "tts_enabled": bool(cfg.get("tts_enabled", True)),
        "no_tts": (not bool(cfg.get("tts_enabled", True))),
        "tts_engine": cfg.get("tts_engine"),
        "voice_id": cfg.get("voice_id"),
        "tts_speed": float(cfg.get("tts_speed", 1.0)),

        "no_watermark": bool(cfg.get("no_watermark", False)),
        "watermark_text": cfg.get("watermark_text"),
        "watermark_position": cfg.get("watermark_position"),
        "watermark_opacity": int(cfg.get("watermark_opacity", 120)),

        "bgm_enabled": bool(cfg.get("bgm_enabled", True)),
        "bgm_volume": float(cfg.get("bgm_volume", 0.20)),

        "keyword_override": cfg.get("kw_global"),
        "image_keyword": cfg.get("kw_global"),
        "query_hint": cfg.get("kw_global"),
        "refresh_images": bool(cfg.get("refresh_images", False)),

        "avatar_enabled": bool(cfg.get("avatar_enabled", False)),
        "avatar_id": cfg.get("avatar_id"),
        "avatar_position": "bottom-right",
        "avatar_scale": float(cfg.get("avatar_scale", 0.20)),
    }

    safe_kwargs = {}
    try:
        sig = inspect.signature(build_long_video)
        allowed = set(sig.parameters.keys())
        safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    except Exception as e:
        print("[LONG][WARN] signature inspect failed:", type(e).__name__, str(e), flush=True)
        safe_kwargs = {}

    print("[LONG] safe_kwargs(keys):", sorted(list(safe_kwargs.keys())), flush=True)

    try:
        out = build_long_video(data, **safe_kwargs)
    except TypeError as e:
        print("[LONG][WARN] call with kwargs failed:", str(e), "-> retry build_long_video(data)", flush=True)
        out = build_long_video(data)

    out_mp4 = None
    if isinstance(out, (str, Path)):
        out_mp4 = str(out)

    if not out_mp4:
        # try results folder first
        out_mp4 = _find_newest_mp4(Path("results")) or _find_newest_mp4(Path("."))

    if not out_mp4:
        print("[LONG][WARN] cannot find output mp4", flush=True)
        return

    out_mp4 = str(Path(out_mp4).resolve())

    # ✅ Avatar first (lip sync uses voice audio; jangan campur bgm dulu)
    if bool(cfg.get("avatar_enabled", False)):
        out_mp4 = _apply_avatar_postprocess(
            out_mp4,
            avatar_id=str(cfg.get("avatar_id") or "neobyte"),
            scale=float(cfg.get("avatar_scale", 0.20)),
            avatars_root=str(cfg.get("avatars_dir") or "assets/avatars"),
        )
        out_mp4 = str(Path(out_mp4).resolve())

    # ✅ BGM after avatar
    if bool(cfg.get("bgm_enabled", True)):
        out_mp4 = _mix_bgm_ffmpeg(
            out_mp4,
            bgm_dir=str(cfg.get("bgm_dir") or "assets/bgm"),
            vol=float(cfg.get("bgm_volume", 0.20)),
        )
        out_mp4 = str(Path(out_mp4).resolve())

    print("OUTPUT_MP4:", out_mp4, flush=True)


if __name__ == "__main__":
    main()
