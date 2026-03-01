from __future__ import annotations

import json
import shutil
import tempfile
import time
import subprocess
from pathlib import Path


def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return (p.returncode == 0), ((p.stderr or p.stdout or "")[-2500:])
    except Exception as e:
        return False, str(e)


def _find_first_image(dirp: Path, patterns: list[str]) -> Path | None:
    for pat in patterns:
        cands = sorted(dirp.glob(pat), key=lambda x: x.name.lower())
        for p in cands:
            if p.is_file():
                return p
    return None


def _find_base_png(avatar_dir: Path) -> Path | None:
    return _find_first_image(
        avatar_dir,
        ["char_base*.png", "base*.png", "*base*.png", "preview*.png", "*.png"]
    )


def _find_mouth_png(avatar_dir: Path, value: str) -> Path | None:
    v = (value or "X").strip()
    p = avatar_dir / f"mouth_{v}.png"
    if p.exists():
        return p
    return _find_first_image(avatar_dir, [f"*mouth*{v}*.png", f"*MOUTH*{v}*.png"])


def apply_avatar_rhubarb(
    mp4_path: Path,
    avatars_dir: Path,
    avatar_id: str,
    scale: float = 0.20,
    pos: str = "bottom-right",
) -> Path:
    """
    Engine yang sama seperti tombol Test Avatar:
    - ffmpeg extract wav
    - rhubarb -> mouth_cues.json
    - moviepy composite base+mouth -> overlay ke video
    """
    mp4_path = Path(mp4_path).resolve()
    if (not mp4_path.exists()) or mp4_path.stat().st_size < 50_000:
        raise RuntimeError(f"mp4 invalid: {mp4_path}")

    if shutil.which("rhubarb") is None:
        raise RuntimeError("rhubarb tidak ada di PATH")

    avatar_dir = (Path(avatars_dir) / str(avatar_id)).resolve()
    if not avatar_dir.exists():
        raise RuntimeError(f"avatar_dir tidak ada: {avatar_dir}")

    base_png = _find_base_png(avatar_dir)
    if not base_png:
        raise RuntimeError(f"base png tidak ditemukan di: {avatar_dir}")

    work = Path(tempfile.mkdtemp(prefix="avatar_rhubarb_"))
    wav = work / "audio.wav"
    cues = work / "mouth_cues.json"

    try:
        ok, err = _run_cmd(["ffmpeg", "-y", "-i", str(mp4_path), "-vn", "-ac", "1", "-ar", "48000", str(wav)])
        if (not ok) or (not wav.exists()):
            raise RuntimeError(f"ffmpeg wav gagal: {err}")

        ok, err = _run_cmd(["rhubarb", "-r", "phonetic", "-f", "json", "-o", str(cues), str(wav)])
        if (not ok) or (not cues.exists()):
            raise RuntimeError(f"rhubarb gagal: {err}")

        # moviepy import (support v2 & v1)
        try:
            from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
        except Exception:
            from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip  # type: ignore

        def _dur(c, d):
            return c.with_duration(d) if hasattr(c, "with_duration") else c.set_duration(d)
        def _start(c, t):
            return c.with_start(t) if hasattr(c, "with_start") else c.set_start(t)
        def _pos(c, p):
            if hasattr(c, "with_position"):
                return c.with_position(p)
            return c.set_position(p)
        def _aud(c, a):
            return c.with_audio(a) if hasattr(c, "with_audio") else c.set_audio(a)

        data = json.loads(cues.read_text(encoding="utf-8"))
        mouth_cues = data.get("mouthCues", []) or []

        v = VideoFileClip(str(mp4_path))
        dur = float(getattr(v, "duration", 0.0) or 0.0)
        fps0 = int(getattr(v, "fps", 30) or 30)

        base = ImageClip(str(base_png))
        base = _dur(base, dur)

        layers = [base]
        for cue in mouth_cues:
            stt = float(cue.get("start", 0))
            enn = float(cue.get("end", stt))
            if enn <= stt:
                continue
            val = str(cue.get("value") or "X").strip()
            mouth_png = _find_mouth_png(avatar_dir, val)
            if mouth_png and mouth_png.exists():
                ic = ImageClip(str(mouth_png))
                ic = _start(ic, stt)
                ic = _dur(ic, enn - stt)
                layers.append(ic)

        avatar = CompositeVideoClip(layers, size=base.size)
        avatar = _dur(avatar, dur)

        target_h = max(80, int(v.h * float(scale)))
        if hasattr(avatar, "resize"):
            avatar = avatar.resize(height=target_h)
        elif hasattr(avatar, "resized"):
            avatar = avatar.resized(height=target_h)

        pad = 18
        if pos == "top-left":
            xy = (pad, pad)
        elif pos == "top-right":
            xy = (v.w - avatar.w - pad, pad)
        elif pos == "bottom-left":
            xy = (pad, v.h - avatar.h - pad)
        else:
            xy = (v.w - avatar.w - pad, v.h - avatar.h - pad)

        avatar = _pos(avatar, xy)

        out = CompositeVideoClip([v, avatar], size=v.size)
        out = _dur(out, dur)
        out = _aud(out, v.audio)

        ts = time.strftime("%H%M%S")
        out_path = mp4_path.with_name(mp4_path.stem + f"_avatar_{ts}.mp4")

        out.write_videofile(
            str(out_path),
            fps=fps0,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            bitrate="3000k",
            audio_bitrate="128k",
            threads=2,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            logger=None,
        )

        try:
            v.close()
            out.close()
        except Exception:
            pass

        return out_path

    finally:
        shutil.rmtree(work, ignore_errors=True)
