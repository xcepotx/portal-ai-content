import json
import os
import time
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Literal, Dict, Any, List

from core.stock_fetcher import fetch_random_clips, download_clips, StockFetchError
from core.scene_builder import build_scenes, SceneBuildError
from core.tts_engine import synthesize_tts, probe_audio_duration_seconds, TTSError
from core.caption_engine import (
    build_caption_timeline,
    style_preset,
    position_preset,
    write_srt,
    CaptionStyle,
    CaptionPos,
    build_caption_timeline_from_durations,
)


class ManifestBuildError(RuntimeError):
    pass


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def read_text_file(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def strip_hash_meta(content: str) -> str:
    """
    Hapus baris metadata yang diawali '#'.
    Juga buang separator line seperti '=====' agar tidak kebaca.
    """
    out_lines = []
    for line in (content or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        # optional: skip separator
        if set(s) <= set("=_-"):
            continue
        out_lines.append(line.rstrip())
    return "\n".join(out_lines).strip()


def extract_keyword_simple(content: str) -> str:
    """
    Simple keyword extraction:
    - take first 1–2 sentences
    - strip common stopwords-ish noise
    - return short query string
    """
    t = content.strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return ""

    # first 2 sentences or 180 chars
    parts = re.split(r"(?<=[\.\!\?])\s+", t)
    first = " ".join(parts[:2]).strip() if parts else t
    first = first[:180]

    # remove urls, numbers-heavy
    first = re.sub(r"https?://\S+", "", first)
    first = re.sub(r"[^a-zA-Z0-9\u00C0-\u024F\u1E00-\u1EFF\s]", " ", first)
    first = re.sub(r"\s+", " ", first).strip()

    # keep top N words
    words = first.split()
    if len(words) > 8:
        first = " ".join(words[:8])
    return first

def extract_keyword_visual(content: str) -> str:
    """
    Keyword yang lebih 'visual' untuk stock video.
    Ambil kata benda/scene yang umum dan buang stopwords.
    Output 2–6 kata.
    """
    t = (content or "").lower()
    t = re.sub(r"[^a-z0-9\u00C0-\u024F\u1E00-\u1EFF\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    stop = set("""
    yang dan di ke dari untuk dengan pada ini itu jadi bisa sangat lagi mau kamu kalian
    fakta unik cepat nonton sampai habis follow like subscribe save share
    """.split())

    anchors = [
        "danau", "es", "salju", "beku", "gunung", "hutan", "laut", "pantai",
        "mobil", "motor", "jalan", "mesin",
        "kota", "gedung", "malam", "langit", "awan", "hujan",
        "api", "asap", "air", "sungai", "batu",
    ]

    words = [w for w in t.split() if w and w not in stop and len(w) >= 3]

    picked = []
    for a in anchors:
        if a in words and a not in picked:
            picked.append(a)

    for w in words:
        if w not in picked:
            picked.append(w)
        if len(picked) >= 6:
            break

    q = " ".join(picked[:6]).strip()

    # bonus: kalau ada sinyal winter/ice, tambahin english tags (pexels/pixabay sering english)
    if any(k in q for k in ["danau", "es", "beku", "salju"]):
        q = (q + " frozen lake ice winter").strip()

    return q


@dataclass
class AutoStockSettings:
    content_file: str
    stock_source: Literal["pexels", "pixabay", "both"] = "both"
    target_duration: float = 30.0
    orientation: Literal["9:16", "16:9", "1:1"] = "9:16"
    random_seed: Optional[int] = None
    clip_count: int = 7

    hook_text: str = ""
    cta_text: str = ""
    caption_style: CaptionStyle = "Modern Subtitle"
    caption_position: CaptionPos = "Bottom"
    font_size: int = 56

    tts_enabled: bool = True
    tts_engine: Literal["edge", "gtts", "elevenlabs"] = "edge"  # ✅ add elevenlabs
    tts_voice: str = "id-ID-ArdiNeural"
    tts_speed: float = 1.0
    keyword_override: Optional[str] = None

    # --- NEW: Avatar overlay (global) ---
    avatar_enabled: bool = False
    avatar_id: str = "cat_v1"
    avatar_position: str = "bottom-right"
    avatar_scale: float = 0.20

    handle: str = "@yourchannel"
    watermark_enabled: bool = True
    watermark_position: str = "top-right"
    hook_subtitle: str = "FAKTA CEPAT"
    watermark_opacity: int = 120

    bgm_enabled: bool = True
    bgm_volume: float = 0.20

def build_manifest(
    settings: AutoStockSettings,
    base_dir: Path,
) -> Path:
    """
    Creates:
    - downloads stock clips to contents/generated/auto_stock_assets/<ts>/
    - optional TTS audio to contents/generated/auto_stock_assets/<ts>/tts.mp3
    - captions srt to same folder
    - manifest JSON to manifests/auto_stock_<ts>.json

    Return manifest path.
    """
    content_path = base_dir / settings.content_file
    if not content_path.exists():
        raise ManifestBuildError(f"Content file tidak ditemukan: {content_path}")

    content_text_raw = read_text_file(content_path)
    if not content_text_raw.strip():
        raise ManifestBuildError("Isi content kosong.")

    # ✅ CLEAN: skip metadata lines "#..."
    content_text = strip_hash_meta(content_text_raw)
    lines = [x.strip() for x in content_text.splitlines() if x.strip()]

    if not content_text.strip():
        raise ManifestBuildError("Isi content kosong setelah buang metadata (#...).")

    # ✅ keyword: override (UI) > visual extractor > fallback simple
    keyword = ""
    if getattr(settings, "keyword_override", None):
        keyword = (settings.keyword_override or "").strip()

    if not keyword:
        keyword = extract_keyword_visual(content_text)

    if not keyword:
        keyword = extract_keyword_simple(content_text)

    if not keyword:
        raise ManifestBuildError("Gagal ekstrak keyword dari content.")

    ts = _now_ts()
    assets_dir = base_dir / "contents" / "generated" / "auto_stock_assets" / ts
    _ensure_dir(assets_dir)

    # Determine Pexels orientation
    pexels_ori = None
    if settings.orientation == "9:16":
        pexels_ori = "portrait"
    elif settings.orientation == "16:9":
        pexels_ori = "landscape"
    elif settings.orientation == "1:1":
        pexels_ori = "square"

    # Fetch & download clips
    try:
        clips = fetch_random_clips(
            query=keyword,
            source=settings.stock_source,
            clip_count=settings.clip_count,
            seed=settings.random_seed,
            pexels_orientation=pexels_ori,
            min_duration=2,
            max_duration=15,
        )
    except StockFetchError as e:
        raise ManifestBuildError(str(e))

    try:
        local_clip_paths = download_clips(clips, assets_dir / "clips", prefix="stock")
    except Exception as e:
        raise ManifestBuildError(f"Gagal download clip: {e}")

    # Build preliminary scenes (target_total = settings.target_duration)
    try:
        scenes = build_scenes(
            local_clips=local_clip_paths,
            target_total=float(settings.target_duration),
            hook_text=settings.hook_text,
            cta_text=settings.cta_text,
            seed=settings.random_seed,
        )
    except SceneBuildError as e:
        raise ManifestBuildError(str(e))

    # TTS
    audio_path = None
    audio_duration = None
    tts_parts = None
    tts_part_durations = None
    tts_error = None  # ✅ ADD

    if settings.tts_enabled:
        try:
            from core.tts_engine import synthesize_tts_lines

            tts_parts, merged = synthesize_tts_lines(
                lines=lines,
                out_dir=assets_dir / "tts_parts",
                engine=settings.tts_engine,
                voice=settings.tts_voice,
                speed=float(settings.tts_speed),
            )
            audio_path = merged
            audio_duration = probe_audio_duration_seconds(audio_path)

            # probe durasi tiap part untuk caption sync
            tts_part_durations = []
            for mp3p in tts_parts:
                wavp = Path(str(mp3p)).with_suffix(".wav")
                if wavp.exists():
                    tts_part_durations.append(probe_audio_duration_seconds(wavp))
                else:
                    tts_part_durations.append(probe_audio_duration_seconds(mp3p))

            print("[TTS] OK engine=", settings.tts_engine, "merged=", audio_path, "dur=", audio_duration, flush=True)

        except TTSError as e:
            tts_error = str(e)
            print("[TTS][WARN] FAILED:", tts_error, flush=True)
            try:
                (assets_dir / "tts_error.txt").write_text(tts_error, encoding="utf-8")
            except Exception:
                pass

            audio_path = None
            audio_duration = None
            tts_parts = None
            tts_part_durations = None

    # Decide final duration: follow audio if active and sane, else target_duration
    final_duration = float(settings.target_duration)
    if audio_duration and 8.0 <= audio_duration <= 90.0:
        # Keep it "shorts" friendly but allow up to 90
        final_duration = float(audio_duration)

    # Rebuild scenes to fit final duration (prevents mismatch)
    try:
        scenes = build_scenes(
            local_clips=local_clip_paths,
            target_total=final_duration,
            hook_text=settings.hook_text,
            cta_text=settings.cta_text,
            seed=settings.random_seed,
        )
    except SceneBuildError as e:
        raise ManifestBuildError(str(e))

    # Captions timeline follows final_duration (most important for sync)
    if tts_part_durations:
        cap_lines = build_caption_timeline_from_durations(
            lines=lines,
            durations=tts_part_durations,
            pad_start=0.0,
            pad_end=0.05,
        )
    else:
        cap_lines = build_caption_timeline(
            text=content_text,
            total_duration=final_duration,
            min_line=1.2,
            max_line=4.0,
        )

    srt_path = write_srt(cap_lines, assets_dir / "captions.srt")

    cap_style = style_preset(settings.caption_style, int(settings.font_size))
    cap_pos = position_preset(settings.caption_position)

    manifest: Dict[str, Any] = {
        "version": "1.0",
        "mode": "auto_stock",
        "created_at": ts,
        "input": {
            "content_file": str(content_path),
            "keyword": keyword,
            "stock_source": settings.stock_source,
        },
        "output": {
            "orientation": settings.orientation,
            "target_duration": float(settings.target_duration),
            "final_duration": float(final_duration),
            "format": "mp4",
            "aspect": "9:16" if settings.orientation == "9:16" else settings.orientation,
            "output_dir": str((base_dir / "contents" / "generated").resolve()),
        },
        "audio": {
            "tts_enabled": bool(settings.tts_enabled and audio_path is not None),
            "tts_engine": settings.tts_engine,
            "tts_voice": settings.tts_voice,
            "tts_speed": float(settings.tts_speed),
            "audio_path": str(audio_path) if audio_path else None,
            "audio_duration": float(audio_duration) if audio_duration else None,
            "tts_error": tts_error,  # ✅ ADD
        },
        "captions": {
            "enabled": True,
            "srt_path": str(srt_path),

            "font_size": int(settings.font_size),              # ✅ ADD (ini dari slider)
            "style_name": str(settings.caption_style),         # "Bold White" / ...
            "position_name": str(settings.caption_position),   # "Bottom" / "Center" / "Dynamic"

            # optional: kalau Anda masih mau simpan dict untuk engine lain, biarkan tapi JANGAN dipakai untuk ffmpeg burn
            "style": cap_style,
            "position": cap_pos,
        },
        "bgm": {
            "enabled": bool(settings.bgm_enabled),
            "volume": float(settings.bgm_volume),
        },
        "render": {
            "handle": str(settings.handle or "").strip(),
            "watermark_enabled": bool(settings.watermark_enabled),
            "watermark_opacity": int(settings.watermark_opacity),
            "watermark_position": str(settings.watermark_position),
            "hook_subtitle": str(settings.hook_subtitle or "").strip(),
        },
        "scenes": [
            {
                "kind": sc.kind,
                "duration": float(sc.duration),
                "clip_path": sc.clip_path,
                "text_overlay": sc.text_overlay,
            }
            for sc in scenes
        ],
        "assets_dir": str(assets_dir),
    }

    # ✅ compatibility: biar main.py bisa baca orientation
    manifest["orientation"] = settings.orientation
    manifest.setdefault("video", {})["orientation"] = settings.orientation

    # render sudah ada, jadi tinggal tambah
    manifest.setdefault("render", {})
    manifest["render"]["orientation"] = settings.orientation

    # optional: penanda jenis
    manifest["variant"] = "Long" if settings.orientation == "16:9" else "Short"

    # --- Avatar overlay (ONLY if enabled AND TTS audio exists) ---
    avatar_ok = bool(
        getattr(settings, "avatar_enabled", False)
        and bool(settings.tts_enabled)
        and (audio_path is not None)
    )

    if avatar_ok:
        manifest["render"]["avatar"] = {
            "enabled": True,
            "id": getattr(settings, "avatar_id", "cat_v1"),
            "position": getattr(settings, "avatar_position", "bottom-right"),
            "scale": float(getattr(settings, "avatar_scale", 0.20)),
        }
        
    manifest_dir = base_dir / "manifests"
    _ensure_dir(manifest_dir)
    manifest_path = manifest_dir / f"auto_stock_{ts}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest_path
