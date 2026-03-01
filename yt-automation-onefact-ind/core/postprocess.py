from __future__ import annotations

import os
import subprocess
from pathlib import Path
from core.avatar_rhubarb import apply_avatar_rhubarb

import re
from pathlib import Path

def _stem_base(name: str) -> str:
    """
    Normalize stem untuk hapus suffix temp yang umum.
    Contoh:
      mobil_..._bgm_avatar_192712_web_fast -> mobil_...
    """
    stem = Path(name).stem
    # hapus tail pattern: _bgm, _avatar, _bgm_avatar, _web, _fast, timestamp 6 digit, kombinasi
    stem = re.sub(r"(_bgm_avatar|_bgm|_avatar)(?:_\d{6})?", "", stem)
    stem = re.sub(r"(_web_fast|_web|_fast)$", "", stem)
    stem = re.sub(r"_\d{6}$", "", stem)  # jaga-jaga timestamp di akhir
    return stem

def pick_final_and_cleanup(outp: Path) -> Path:
    """
    outp: path output terakhir (biasanya hasil postprocess).
    Cari semua varian dari base stem di folder yang sama, pilih final terbaik,
    rename jadi <base>.mp4, hapus intermediate.
    """
    outp = Path(outp).resolve()
    folder = outp.parent
    base = _stem_base(outp.name)

    # kumpulkan kandidat yang share base prefix
    candidates = sorted(folder.glob(base + "*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not candidates:
        return outp

    def score(p: Path) -> tuple[int, float]:
        n = p.name
        s = 0
        if "_web_fast" in n: s = 50
        elif "_web" in n: s = 40
        elif "_bgm_avatar" in n: s = 30
        elif "_avatar" in n: s = 25
        elif "_bgm" in n: s = 20
        else: s = 10
        return (s, p.stat().st_mtime)

    candidates.sort(key=score, reverse=True)
    final_src = candidates[0]

    final_dst = folder / f"{base}.mp4"

    # kalau sudah final name dan sama file -> tinggal cleanup
    if final_src != final_dst:
        # rename (replace kalau sudah ada)
        try:
            if final_dst.exists():
                final_dst.unlink()
        except Exception:
            pass
        final_src.rename(final_dst)
    else:
        final_dst = final_src

    # cleanup: hapus semua kandidat lain (intermediate)
    for p in candidates:
        if p == final_dst:
            continue
        try:
            p.unlink()
        except Exception:
            pass

    return final_dst

def ensure_web_playable(inp: Path) -> Path:
    inp = Path(inp).resolve()
    outp = inp.with_name(inp.stem + "_web" + inp.suffix)

    # normalize ke H264/AAC + faststart (paling aman untuk browser)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(outp),
    ]
    _run(cmd)   # pakai helper _run kamu (subprocess.run check=True)
    return outp

def faststart_remux(inp: Path) -> Path:
    inp = Path(inp).resolve()
    outp = inp.with_name(inp.stem + "_fast" + inp.suffix)
    _run(["ffmpeg", "-y", "-i", str(inp), "-c", "copy", "-movflags", "+faststart", str(outp)])
    return outp

def find_latest_video(ws_root: Path, topic: str) -> Path | None:
    ws_root = Path(ws_root).resolve()
    candidates = [
        ws_root / "outputs" / topic,
        ws_root / "outputs",
        ws_root / "renders" / topic,
        ws_root / "renders",
        ws_root / "out" / topic,
        ws_root / "out",
    ]
    mp4s: list[Path] = []
    for d in candidates:
        if d.exists():
            mp4s.extend(list(d.rglob("*.mp4")))
    if not mp4s:
        return None
    mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[0]


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def mute_audio_ffmpeg(inp: Path) -> Path:
    inp = Path(inp).resolve()
    outp = inp.with_name(inp.stem + "_muted" + inp.suffix)
    _run(["ffmpeg", "-y", "-i", str(inp), "-c:v", "copy", "-an", str(outp)])
    return outp


def _pick_latest_bgm(bgm_dir: Path) -> Path | None:
    exts = (".mp3", ".wav", ".m4a", ".aac")
    files = [p for p in bgm_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


def mix_bgm_ffmpeg(inp: Path, bgm_dir: Path, bgm_file: str | None, vol: float = 0.2) -> Path:
    inp = Path(inp).resolve()
    bgm_dir = Path(bgm_dir).resolve()

    if bgm_file and bgm_file not in ("(auto/latest)", "auto", "latest"):
        bgm = (bgm_dir / bgm_file).resolve()
        if not bgm.exists():
            bgm = _pick_latest_bgm(bgm_dir)
    else:
        bgm = _pick_latest_bgm(bgm_dir)

    if not bgm or not bgm.exists():
        return inp

    outp = inp.with_name(inp.stem + "_bgm" + inp.suffix)

    # loop bgm supaya sepanjang video, kecilkan volume, mix dengan audio original
    cmd = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-stream_loop", "-1", "-i", str(bgm),
        "-filter_complex",
        f"[1:a]volume={vol}[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-shortest",
        str(outp)
    ]
    _run(cmd)
    return outp


def _pick_avatar_overlay_file(avatars_dir: Path, avatar_id: str) -> Path | None:
    av_dir = (Path(avatars_dir) / avatar_id).resolve()
    if not av_dir.exists():
        return None

    vid_ext = (".mp4", ".webm", ".mov", ".m4v")
    img_ext = (".png", ".jpg", ".jpeg", ".webp")

    # exact preview.*
    for ext in img_ext:
        p = av_dir / f"preview{ext}"
        if p.exists():
            return p

    # 1) preview*.video dulu
    preview_vids = sorted([
        p for p in av_dir.iterdir()
        if p.is_file() and p.suffix.lower() in vid_ext and p.stem.lower().startswith("preview")
    ], key=lambda x: x.name.lower())
    if preview_vids:
        return preview_vids[0]

    # 2) baru preview*.image
    preview_imgs = sorted([
        p for p in av_dir.iterdir()
        if p.is_file() and p.suffix.lower() in img_ext and p.stem.lower().startswith("preview")
    ], key=lambda x: x.name.lower())
    if preview_imgs:
        return preview_imgs[0]

    # fallback image/video
    imgs = sorted([p for p in av_dir.iterdir() if p.is_file() and p.suffix.lower() in img_ext], key=lambda x: x.name.lower())
    if imgs:
        return imgs[0]

    vids = sorted([p for p in av_dir.iterdir() if p.is_file() and p.suffix.lower() in vid_ext], key=lambda x: x.name.lower())
    if vids:
        return vids[0]

    return None


def overlay_avatar_ffmpeg(inp: Path, avatars_dir: Path, avatar_id: str, scale: float = 0.2, pos: str = "bottom-right") -> Path:
    inp = Path(inp).resolve()
    avatars_dir = Path(avatars_dir).resolve()

    ov_path = _pick_avatar_overlay_file(avatars_dir, avatar_id)
    if not ov_path:
        return inp

    outp = inp.with_name(inp.stem + "_avatar" + inp.suffix)

    pad = 16
    if pos == "top-left":
        xy = f"{pad}:{pad}"
    elif pos == "top-right":
        xy = f"W-w-{pad}:{pad}"
    elif pos == "bottom-left":
        xy = f"{pad}:H-h-{pad}"
    else:
        xy = f"W-w-{pad}:H-h-{pad}"

    vf = f"[1:v]scale=iw*{scale}:-1[av];[0:v][av]overlay={xy}:format=auto"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-stream_loop","-1","-i", str(ov_path),
        "-i", str(ov_path),
        "-filter_complex", vf,
        "-map", "0:v",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(outp),
    ]
    _run(cmd)
    return outp

def run_postprocess(ws_root: Path, topic: str, post: dict, env: dict, inp_mp4: Path | None = None) -> Path | None:
    # ✅ prefer input mp4 explicit (anti race saat job parallel)
    latest = None
    if inp_mp4 is not None:
        p = Path(inp_mp4).resolve()
        if p.exists() and p.stat().st_size > 50_000:
            latest = p

    if latest is None:
        latest = find_latest_video(ws_root, topic)

    if not latest:
        return None

    outp = Path(latest).resolve()

    # 1) TTS OFF => mute
    if post.get("tts_on") is False:
        outp = mute_audio_ffmpeg(outp)

    # 2) BGM
    if post.get("bgm_on") is True:
        bgm_dir = Path(env.get("YTA_BGM_DIR", "") or "").resolve()
        if bgm_dir.exists():
            outp = mix_bgm_ffmpeg(outp, bgm_dir, post.get("bgm_file"), float(post.get("bgm_vol", 0.2)))

    # 3) Avatar (✅ jangan bikin abort kalau gagal)
    if post.get("avatar_on") is True:
        avatars_dir = Path(env.get("YTA_AVATARS_DIR", "") or "").resolve()
        if avatars_dir.exists():
            try:
                outp = apply_avatar_rhubarb(
                    Path(outp),
                    avatars_dir=avatars_dir,
                    avatar_id=str(post.get("avatar_id") or ""),
                    scale=float(post.get("avatar_scale", 0.20)),
                    pos=str(post.get("avatar_position", "bottom-right")),
                )
            except Exception:
                # skip avatar kalau input invalid (contoh: moov atom not found)
                pass

    # 4) web playable + faststart + cleanup
    outp = ensure_web_playable(outp)
    try:
        outp = faststart_remux(outp)
    except Exception:
        pass

    outp = pick_final_and_cleanup(Path(outp))

    # ✅ optional rename final jika diminta
    try:
        final_name = str((post or {}).get("final_name") or "").strip()
        if final_name and outp.exists():
            final_dst = outp.with_name(final_name)
            if final_dst != outp:
                try:
                    if final_dst.exists():
                        final_dst.unlink()
                except Exception:
                    pass
                outp.rename(final_dst)
                outp = final_dst
    except Exception:
        pass

    # ✅ NEW: optional move ke folder output (mis: out/long)
    try:
        final_dir = str((post or {}).get("final_dir") or "").strip()  # contoh: "out/long"
        if final_dir and outp.exists():
            dest_dir = (Path(ws_root).resolve() / final_dir).resolve()
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = (dest_dir / outp.name).resolve()
            try:
                if dest.exists():
                    dest.unlink()
            except Exception:
                pass
            outp.replace(dest)   # move/rename (satu filesystem)
            outp = dest
    except Exception:
        pass

    return outp
