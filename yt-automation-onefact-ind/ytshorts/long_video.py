from __future__ import annotations

import os
import re
import json
import random
import subprocess
import datetime
from tqdm import tqdm 
from dataclasses import dataclass
from typing import List, Dict, Optional
from .tts_long import make_tts_segments
from .long_hook_overlay import write_ass_hook
from .long_caption_fact import write_ass_fact_only, burn_subs

# =========================
# Models
# =========================

@dataclass
class Segment:
    idx: int
    title: str
    text: str


#@dataclass
#class LongDoc:
#    title: str
#    topic: str
#    lang: str
#    caption_mode: str

#    bg_source: str     # "local" | "pexels"
#    bg_count: int
#    bg_every: float

#    bg_dir: str        # used if bg_source == local (relative under assets/)
#    bgm_path: Optional[str]
#    opening_video: str | None = None
#    hook: str
#    cta: str
#    segments: List[Segment]

from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class LongDoc:
    # REQUIRED (tanpa default) — taruh paling atas
    title: str
    topic: str
    segments: List["Segment"] = field(default_factory=list)

    # OPTIONAL (punya default)
    lang: str = "id"
    caption_mode: str = "sentence"

    bg_source: str = "local"          # "local" | "pexels"
    bg_count: int = 30
    bg_every: float = 7.0
    bg_dir: str = ""
    bgm_path: Optional[str] = None

    opening_video: Optional[str] = None  # <--- fitur opening

    hook: Optional[str] = None
    cta: Optional[str] = None


# =========================
# Parsing
# =========================

_HEADER_RE = re.compile(r"^\s*#\s*([A-Z_]+)\s*:\s*(.+?)\s*$", re.MULTILINE)
_SEG_RE = re.compile(r"^\s*\[SEGMENT:(\d+)\s*\|\s*title=(.+?)\]\s*$", re.MULTILINE)

def _ffmpeg_concat_videos(videos: list[str], out_mp4: str) -> None:
    """
    Concat video MP4 (opening + main) pakai concat demuxer (lossless).
    """
    import subprocess, tempfile, os

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for v in videos:
            f.write(f"file '{os.path.abspath(v)}'\n")
        list_path = f.name

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                out_mp4,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        os.remove(list_path)

def _merge_ass(out_ass: str, ass_a: str, ass_b: str) -> str:
    def read_events(p: str) -> str:
        txt = open(p, "r", encoding="utf-8").read()
        idx = txt.find("[Events]")
        if idx < 0:
            return ""
        return txt[idx:]  # include [Events] header

    a = open(ass_a, "r", encoding="utf-8").read()

    # ambil header dari A sampai sebelum [Events]
    idx = a.find("[Events]")
    head = a[:idx] if idx > 0 else a

    ev_a = read_events(ass_a)
    ev_b = read_events(ass_b)
    if ev_b.startswith("[Events]"):
        ev_b = "\n".join(ev_b.splitlines()[1:]) + "\n"

    os.makedirs(os.path.dirname(out_ass), exist_ok=True)  # <-- ganti _ensure_dir
    with open(out_ass, "w", encoding="utf-8") as f:
        f.write(head)
        f.write(ev_a)
        f.write("\n")
        f.write(ev_b)

    return out_ass

def parse_long_script(md_path: str) -> LongDoc:
    raw = open(md_path, "r", encoding="utf-8").read()
    headers: Dict[str, str] = {k: v.strip() for k, v in _HEADER_RE.findall(raw)}

    title = headers.get("TITLE", os.path.splitext(os.path.basename(md_path))[0])
    topic = headers.get("TOPIC", "general")
    lang = headers.get("LANG", "id")
    caption = headers.get("CAPTION", "sentence").lower()

    bg_source = headers.get("BG_SOURCE", "local").strip().lower()
    bg_count = int(headers.get("BG_COUNT", "30"))
    bg_every = float(headers.get("BG_EVERY", "7"))

    bg_dir = headers.get("BG", f"local_bg/{topic}")
    bgm = headers.get("BGM", None)
    opening = headers.get("OPENING", None)

    hook = _extract_block(raw, "HOOK")
    cta = _extract_block(raw, "CTA")

    segments: List[Segment] = []
    seg_matches = list(_SEG_RE.finditer(raw))
    for i, m in enumerate(seg_matches):
        idx = int(m.group(1))
        seg_title = m.group(2).strip()

        start = m.end()
        end = seg_matches[i + 1].start() if i + 1 < len(seg_matches) else len(raw)
        seg_body = raw[start:end].strip()

        seg_body = re.split(r"^\s*\[(HOOK|CTA)\]\s*$", seg_body, maxsplit=1, flags=re.MULTILINE)[0].strip()
        segments.append(Segment(idx=idx, title=seg_title, text=seg_body))

    if not segments:
        raise ValueError(f"No segments found in {md_path}. Add [SEGMENT:1 | title=...] blocks.")

    print("[DBG] OPENING header =", headers.get("OPENING"))

    return LongDoc(
        title=title,
        topic=topic,
        lang=lang,
        caption_mode=caption if caption in ("sentence", "word") else "sentence",
        bg_source=bg_source if bg_source in ("local", "pexels") else "local",
        bg_count=max(5, bg_count),
        bg_every=max(2.0, bg_every),
        bg_dir=bg_dir,
        bgm_path=bgm,
        opening_video=opening,
        hook=hook,
        cta=cta,
        segments=segments,
    )


def _extract_block(raw: str, tag: str) -> str:
    pattern = re.compile(rf"^\s*\[{tag}\]\s*$", re.MULTILINE)
    m = pattern.search(raw)
    if not m:
        return ""
    start = m.end()
    endm = re.search(r"^\s*\[[A-Z_]+(?:[:\d].*?)?\]\s*$", raw[start:], flags=re.MULTILINE)
    end = start + endm.start() if endm else len(raw)
    return raw[start:end].strip()

def _pick_random_bgm(bgm_spec: str) -> Optional[str]:
    """
    bgm_spec:
      - "random" -> pick from assets/bgm/*
      - "random:subdir" -> pick from assets/bgm/subdir/*
      - else -> treated as normal path (assets/<bgm_spec>)
    return absolute/relative path (resolved to assets/...), or None if not found
    """
    if not bgm_spec:
        return None

    s = (bgm_spec or "").strip()
    if not s:
        return None

    exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}

    def list_audio(dir_path: str) -> List[str]:
        if not os.path.isdir(dir_path):
            return []
        out = []
        for name in os.listdir(dir_path):
            p = os.path.join(dir_path, name)
            if os.path.isfile(p) and os.path.splitext(name.lower())[1] in exts:
                out.append(p)
        return out

    # random mode
    if s.lower().startswith("random"):
        # default folder
        folder = os.path.join("assets", "bgm")
        # random:subdir
        if ":" in s:
            _, sub = s.split(":", 1)
            sub = sub.strip().strip("/")

            if sub:
                folder = os.path.join("assets", "bgm", sub)

        candidates = list_audio(folder)
        if not candidates:
            return None
        return random.choice(candidates)

    # normal path
    return _resolve_assets_path(s)


# =========================
# Output paths (NO episode folder)
# =========================

def ensure_long_paths(doc_topic: str, md_path: str) -> Dict[str, str]:
    base_dir = os.path.join("out", "long", doc_topic)
    os.makedirs(base_dir, exist_ok=True)

    script_name = os.path.splitext(os.path.basename(md_path))[0]
    # example: 20260124_154233_001_script
    parts = script_name.split("_")
    ymd = parts[0] if len(parts) >= 1 else datetime.date.today().strftime("%Y%m%d")
    idx = parts[2] if (len(parts) >= 3 and parts[2].isdigit()) else "001"

    # === time diambil dari waktu render (NOW)
    tm_now = datetime.datetime.now().strftime("%H%M%S")

    out_mp4 = os.path.join(base_dir, f"{ymd}_{tm_now}_{idx}_video.mp4")

    tmp_root = os.path.join(base_dir, "_tmp", f"{ymd}_{tm_now}_{idx}")
    paths = {
        "base_dir": base_dir,
        "tmp": tmp_root,
        "tmp_tts": os.path.join(tmp_root, "tts"),
        "tmp_renders": os.path.join(tmp_root, "renders"),
        "tmp_audio": os.path.join(tmp_root, "audio"),
        "tmp_bg_cache": os.path.join(tmp_root, "bg_cache"),
        "tmp_bg_clips": os.path.join(tmp_root, "bg_clips"),
        "video_mp4": out_mp4,
        "meta_json": out_mp4.replace("_video.mp4", "_youtube_meta.json"),
        "chapters_txt": out_mp4.replace("_video.mp4", "_chapters.txt"),
        "log_txt": out_mp4.replace("_video.mp4", "_logs.txt"),
        "tmp_caps": os.path.join(tmp_root, "captions"),
    }

    for k, p in paths.items():
        if p.endswith((".mp4", ".json", ".txt")):
            continue
        os.makedirs(p, exist_ok=True)

    return paths

def _ffmpeg_concat_audios(audio_files: List[str], out_wav: str) -> None:
    """
    Concat MP3 files into one WAV (stabil untuk durasi & mixing).
    """
    list_path = out_wav + ".txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for a in audio_files:
            f.write(f"file '{os.path.abspath(a)}'\n")

    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-ar", "44100",
        "-ac", "2",
        out_wav
    ])

# =========================
# Main builder
# =========================

def build_long_video(md_path: str, *, caption: str = "fact") -> str:
    doc = parse_long_script(md_path)
    hook_text = getattr(doc, "hook", None) or doc.title
    outp = ensure_long_paths(doc.topic, md_path)

    seg_texts = [s.text for s in doc.segments]
    base_steps = len(seg_texts) + 6
    pbar = tqdm(total=base_steps, desc="Generating long video", unit="step")

    # 1) TTS per segmen
    engine = os.getenv("LONG_TTS_ENGINE", "gtts").strip().lower()
    voice_long = os.getenv("LONG_ELEVEN_VOICE", "").strip() or None

    tts_files = make_tts_segments(
        seg_texts,
        out_dir=outp["tmp_tts"],
        lang=doc.lang,
        engine=engine,
        eleven_voice_id=voice_long,
    )
    pbar.update(len(seg_texts))

    print("[DBG] doc.opening_video =", getattr(doc, "opening_video", None))

    # 2) concat audio -> voice.wav
    voice_wav = os.path.join(outp["tmp_audio"], "voice.wav")
    _ffmpeg_concat_audios(tts_files, voice_wav)
    dur = _ffprobe_duration(voice_wav)
    pbar.update(1)

    # 3) fetch backgrounds
    if doc.bg_source == "pexels":
        bg_files = _fetch_bg_pexels_images(doc, outp)
    else:
        bg_dir = _resolve_assets_path(doc.bg_dir)
        bg_files = _list_bg_files(bg_dir)

    if not bg_files:
        pbar.close()
        raise FileNotFoundError("No background files available.")
    pbar.update(1)

    # extend progress total for clip generation
    n_clips = max(1, int(dur // doc.bg_every) + 1)
    pbar.total += n_clips
    pbar.refresh()

    # 4) build bg silent video
    silent_mp4 = os.path.join(outp["tmp_renders"], "video_silent.mp4")
    _make_bg_silent_video(
        bg_files=bg_files,
        duration=dur,
        out_mp4=silent_mp4,
        clips_dir=outp["tmp_bg_clips"],
        change_every=doc.bg_every,
        progress_cb=lambda: pbar.update(1),
    )
    pbar.update(1)  # concat BG step

    # 5) mix bgm (optional)
    final_audio = voice_wav
    if getattr(doc, "bgm_path", None):
        bgm_path = _pick_random_bgm(doc.bgm_path)
        if bgm_path:
            mixed = os.path.join(outp["tmp_audio"], "audio_final.wav")
            _ffmpeg_mix_bgm(
                voice_wav,
                bgm_path,
                mixed,
                bgm_volume=getattr(doc, "bgm_volume", 0.15),
            )
            final_audio = mixed
    pbar.update(1)

    # 6) FINAL mux (caption toggle)
    if caption == "off":
        _ffmpeg_mux(silent_mp4, final_audio, outp["video_mp4"])
    else:
        from ytshorts.long_caption_fact import write_ass_fact_only, burn_subs

        durations = [_ffprobe_duration(a) for a in tts_files]
        ass_fact = os.path.join(outp["tmp_caps"], "facts.ass")

        write_ass_fact_only(
            segments_text=[s.text for s in doc.segments],
            durations_sec=durations,
            out_ass=ass_fact,
            fontsize=42,
            x=120,
            y=140,
            show_sec=8.0,
        )

        ass_hook = os.path.join(outp["tmp_caps"], "hook.ass")
        write_ass_hook(
            out_ass=ass_hook,
            hook_text=hook_text,
            duration_sec=3.2,
            fontsize=64,
            y=220,
        )

        ass_master = os.path.join(outp["tmp_caps"], "master.ass")
        _merge_ass(ass_master, ass_hook, ass_fact)

        burn_subs(
            run_ffmpeg=_run,
            video_mp4=silent_mp4,
            audio_in=final_audio,
            subs_path=ass_fact,
            out_mp4=outp["video_mp4"],
        )
    pbar.update(1)

    # 7) write meta
    chapters = _build_chapters_placeholder(doc)
    with open(outp["chapters_txt"], "w", encoding="utf-8") as f:
        f.write("\n".join(chapters))

    meta = {
        "title": doc.title,
        "topic": doc.topic,
        "lang": doc.lang,
        "description": _build_description(doc),
        "chapters": chapters,
        "bg_source": doc.bg_source,
        "bg_count": doc.bg_count,
        "bg_every": doc.bg_every,
        "script_path": md_path,
    }
    with open(outp["meta_json"], "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    pbar.update(1)
    pbar.close()

    # ======================
    # OPTIONAL: prepend opening video
    # ======================
    opening = getattr(doc, "opening_video", None)
    print("[DBG] opening raw =", opening)

    if opening:
        opening_path = _resolve_assets_path(opening)
        print("[DBG] opening resolved =", opening_path)
        print("[DBG] exists =", os.path.exists(opening_path) if opening_path else None)

        if opening_path and os.path.exists(opening_path):
            final_with_opening = outp["video_mp4"].replace("_video.mp4", "_with_opening.mp4")

            _ffmpeg_concat_videos(
                [opening_path, outp["video_mp4"]],
                final_with_opening,
            )

            return final_with_opening

    return outp["video_mp4"]

# =========================
# Pexels fetch via existing image_fetcher.py
# =========================

def _fetch_bg_pexels_images(doc: LongDoc, outp: Dict[str, str]) -> List[str]:
    from ytshorts import image_fetcher

    hint_lines = [doc.title] + [s.title for s in doc.segments[:5]]
    query_hint = " ".join(hint_lines)

    img_paths, _ = image_fetcher.fetch_backgrounds_for_content(
        lines=[s.text for s in doc.segments[:3]],
        topic=doc.topic,
        img_dir=outp["tmp_bg_cache"],
        attribution_path=os.path.join("out", f"attribution_{doc.topic}_long.jsonl"),
        n=doc.bg_count,
        content_id="longbg",
        used_global=set(),
        query_hint=query_hint,
    )
    random.shuffle(img_paths)
    return img_paths


# =========================
# FFmpeg helpers
# =========================

def _run(cmd: List[str], quiet: bool = True) -> None:
    """
    quiet=True: sembunyikan output ffmpeg (stdout/stderr).
    Kalau error, exception tetap keluar.
    """
    if quiet:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(cmd, check=True)

def _ffprobe_duration(path: str) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]).decode("utf-8").strip()
    return float(out)


def _ffmpeg_concat_wavs(wavs: List[str], out_wav: str) -> None:
    list_path = out_wav + ".txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for w in wavs:
            f.write(f"file '{os.path.abspath(w)}'\n")

    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_wav])


def _make_bg_silent_video(
    bg_files: List[str],
    duration: float,
    out_mp4: str,
    clips_dir: str,
    change_every: float = 7.0,
    progress_cb=None,   # NEW
) -> None:
    os.makedirs(clips_dir, exist_ok=True)

    n = max(1, int(duration // change_every) + 1)
    picks = (bg_files * ((n // max(1, len(bg_files))) + 1))[:n]

    clip_paths: List[str] = []
    for i, src in enumerate(picks, start=1):
        clip = os.path.join(clips_dir, f"clip_{i:04d}.mp4")
        _make_one_clip(src, clip, change_every)
        clip_paths.append(clip)
        if progress_cb:
            progress_cb()

    concat_list = out_mp4 + ".txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for c in clip_paths:
            f.write(f"file '{os.path.abspath(c)}'\n")

    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-t", str(duration),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        out_mp4
    ])


def _make_one_clip(src: str, out_clip: str, t: float) -> None:
    ext = os.path.splitext(src.lower())[1]
    is_img = ext in {".jpg", ".jpeg", ".png", ".webp"}
    vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,format=yuv420p"

    if is_img:
        _run([
            "ffmpeg", "-y",
            "-loop", "1",
            "-t", str(t),
            "-i", src,
            "-vf", vf,
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            out_clip
        ])
    else:
        _run([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-t", str(t),
            "-i", src,
            "-vf", vf,
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            out_clip
        ])


def _ffmpeg_mix_bgm(
    voice_wav: str,
    bgm_path: str,
    out_wav: str,
    *,
    bgm_volume: float = 0.15,   # 15% volume BGM (ideal untuk voice-over)
) -> None:
    """
    Mix voice + bgm.
    - voice: full volume
    - bgm  : lowered volume
    """
    _run([
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-nostats",
        "-y",
        "-i", voice_wav,
        "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={bgm_volume}[bgm];"
        f"[0:a][bgm]amix=inputs=2:weights=1 1:dropout_transition=3",
        "-ar", "44100",
        "-ac", "2",
        out_wav
    ])

def _ffmpeg_mux(video_mp4: str, audio_wav: str, out_mp4: str) -> None:
    _run([
        "ffmpeg", "-y",
        "-i", video_mp4,
        "-i", audio_wav,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_mp4
    ])


# =========================
# Assets helpers
# =========================

def _resolve_assets_path(rel_or_abs: str) -> str:
    if not rel_or_abs:
        return rel_or_abs

    rel_or_abs = rel_or_abs.strip()

    if os.path.isabs(rel_or_abs):
        return rel_or_abs

    p_norm = rel_or_abs.replace("\\", "/")
    if p_norm.startswith("assets/"):
        return rel_or_abs

    return os.path.join("assets", rel_or_abs)


def _list_bg_files(bg_dir: str) -> List[str]:
    exts = {".mp4", ".mov", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
    if not os.path.isdir(bg_dir):
        return []
    out = []
    for name in os.listdir(bg_dir):
        p = os.path.join(bg_dir, name)
        if os.path.isfile(p) and os.path.splitext(name.lower())[1] in exts:
            out.append(p)
    random.shuffle(out)
    return out


# =========================
# Meta helpers (simple placeholder)
# =========================

def _build_chapters_placeholder(doc: LongDoc) -> List[str]:
    lines = ["00:00 Opening"]
    t = 10
    for seg in doc.segments:
        mm = t // 60
        ss = t % 60
        lines.append(f"{mm:02d}:{ss:02d} {seg.title}")
        t += 45
    return lines


def _build_description(doc: LongDoc) -> str:
    parts = []
    if doc.hook:
        parts.append(doc.hook)
        parts.append("")
    parts.append("Chapters:")
    parts.extend(_build_chapters_placeholder(doc))
    if doc.cta:
        parts.append("")
        parts.append(doc.cta)
    return "\n".join(parts)
