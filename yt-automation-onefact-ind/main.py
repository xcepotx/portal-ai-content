import os
import re
import sys
import traceback
import glob
import random
import inspect
import subprocess
import argparse
import time
import shutil
import json
import urllib.request
import urllib.parse

from pathlib import Path
from ytshorts.content_loader import load_content
from ytshorts.image_fetcher import fetch_backgrounds_for_content
from ytshorts.video_word import build_onefact_video_word_captions
from ytshorts.tts import make_tts_files
from ytshorts.ytmeta import write_meta_md as write_simple_meta_md, slug_from_hook
from ytshorts.youtube_uploader import upload_short, UploadLimitExceeded
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ytshorts.review_manager import save_project_state, load_project_state, REVIEW_DIR

# youtube upload meta (title/desc/tags)
from ytshorts.youtube_meta import (
    write_meta_md as write_youtube_meta_md,
    DEFAULT_TAGS,
    make_title,
    make_description,
)
from ytshorts.youtube_uploader import upload_short

# +++ ADD
def _pick_latest_results_mp4(ws_root: Path) -> Path | None:
    d = (ws_root / "results").resolve()
    if not d.exists():
        return None
    mp4s = [p for p in d.glob("*.mp4") if p.is_file()]
    if not mp4s:
        return None
    mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[0]

def _ffmpeg_web_fix(inp: Path) -> None:
    # yuv420p + faststart biar browser/Windows aman
    tmp = inp.with_name(inp.stem + "_webfix.mp4")
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
        str(tmp),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if tmp.exists() and tmp.stat().st_size > 50_000:
            tmp.replace(inp)
    except Exception:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass

def run_long(args, root, contents_root, out_root, tts_dir, tmp_dir, assets_img_dir):
    # ws_root biasanya = cwd karena JobStore menjalankan main.py dengan cwd=ws_root
    ws_root = Path.cwd().resolve()

    long_json = str(getattr(args, "long_json", "") or "").strip()
    if not long_json:
        raise ValueError("--long-json wajib diisi untuk mode long.")

    data = json.loads(Path(long_json).read_text(encoding="utf-8", errors="replace") or "{}")

    # ===== jalankan engine long =====
    from ytlong.engine import build_long_video

    tts_engine = str(getattr(args, "tts_long", "gtts") or "gtts")
    voice_id   = str(getattr(args, "eleven_voice_long", "") or "").strip()
    no_wm      = bool(getattr(args, "no_watermark", False))
    wm_text    = "" if no_wm else str(getattr(args, "handle", "") or "").strip()
    hook_sub   = str(getattr(args, "hook_subtitle", "") or "").strip()

    build_long_video(
        data,
        tts_engine=tts_engine,
        voice_id=(voice_id or None),
        no_watermark=no_wm,
        watermark_text=wm_text,
        hook_subtitle=hook_sub,
    )

    # ===== ambil output dari results/ =====
    latest = _pick_latest_results_mp4(ws_root)
    if not latest or not latest.exists():
        raise RuntimeError("Long render selesai tapi file MP4 tidak ditemukan di results/")

    # ===== pindahkan ke out/long dengan nama long_<ts>.mp4 =====
    out_long_dir = Path(out_root).resolve()
    out_long_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    final = (out_long_dir / f"long_{ts}.mp4").resolve()

    # move/replace
    try:
        if final.exists():
            final.unlink()
    except Exception:
        pass

    latest.replace(final)

    # optional: make it web/windows friendly
    _ffmpeg_web_fix(final)

    # ===== IMPORTANT: print final path biar JobStore nangkap =====
    print(f"OUTPUT_MP4: {final}", flush=True)
    print(f"Done: {final}", flush=True)

def _size_from_orientation(ori: str) -> tuple[int, int]:
    o = (ori or "").strip().lower()
    if o in ("16:9", "16x9", "landscape"):
        return (1280, 720)
    if o in ("1:1", "1x1", "square"):
        return (1080, 1080)
    return (720, 1280)  # default portrait


def _force_mp4_size_ffmpeg(inp: str, w: int, h: int) -> str:
    """
    Paksa output MP4 jadi ukuran W×H (scale keep-aspect + pad) + yuv420p + faststart
    Overwrite file asli secara aman (pakai temp lalu replace).
    """
    p = Path(inp).resolve()
    if not p.exists():
        return inp

    tmp = p.with_name(p.stem + f"_fixed_{w}x{h}" + p.suffix)

    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(p),
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if tmp.exists() and tmp.stat().st_size > 50_000:
            tmp.replace(p)  # overwrite original
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    return str(p)

def _run_long_entry(args, root, contents_root, out_root, tts_dir, tmp_dir, assets_img_dir):
    """
    Entry runner untuk Long.
    Cari fungsi long yang ada di main.py, lalu panggil.
    """
    candidates = [
        "run_long",            # kalau suatu saat ada
        "run_long_video",      # umum
        "run_longform",        # variasi
        "run_long_form",       # variasi
        "run_generate_long",   # kadang dipisah generate+render
        "run_long_pipeline",   # variasi
    ]

    for name in candidates:
        fn = globals().get(name)
        if callable(fn):
            return fn(args, root, contents_root, out_root, tts_dir, tmp_dir, assets_img_dir)

    raise RuntimeError(
        "Long mode dipilih tapi tidak ada runner long di main.py. "
        "Cari fungsi long yang ada (mis: def run_long_video / run_generate_long) lalu tambahkan ke candidates."
    )

def _find_parent_named(p: Path, name: str) -> Path | None:
    """
    Return directory path whose name == `name` among p parents, else None.
    """
    pp = p.resolve()
    for parent in [pp] + list(pp.parents):
        if parent.name == name:
            return parent
    return None

def _infer_user_root_from_manifest(manifest_path: str | None) -> Path | None:
    """
    Infer workspace root from:
      user_root/manifests/<...>/file.json
    -> return user_root
    """
    if not manifest_path:
        return None
    try:
        mp = Path(manifest_path).expanduser().resolve()
        mdir = _find_parent_named(mp, "manifests")
        if mdir and mdir.parent.exists():
            return mdir.parent.resolve()
    except Exception:
        pass
    return None

def _resolve_out_root(manifest_path: str | None) -> Path:
    """
    Priority:
      1) ENV YTA_OUT_ROOT
      2) infer from manifest -> <user_root>/out
      3) fallback -> ./out (repo)
    """
    env_out = (os.getenv("YTA_OUT_ROOT") or "").strip()
    if env_out:
        return Path(env_out).expanduser().resolve()

    user_root = _infer_user_root_from_manifest(manifest_path)
    if user_root:
        return (user_root / "out").resolve()

    return Path("out").resolve()

def _resolve_out_dir(topic: str, manifest_path: str | None) -> Path:
    out_root = _resolve_out_root(manifest_path)
    out_dir = (out_root / (topic or "default")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_overlays").mkdir(parents=True, exist_ok=True)
    return out_dir

def prepare_auto_bg(doc, topic, assets_img_dir, out_root, args):
    attrib_path = os.path.join(out_root, f"attribution_{topic}_single.jsonl")
    tag = Path(doc.file_path).stem
    content_id = f"{topic}_{tag}"

    if args.refresh_bg:
        n_rm = _purge_bg_cache(assets_img_dir, content_id)
        print(f"[INFO] refresh-bg: removed {n_rm} cached BG files for {content_id}")

    bg_paths, _ = fetch_backgrounds_for_content(
        lines=doc.lines,
        topic=doc.topic if hasattr(doc, "topic") else topic,
        img_dir=assets_img_dir,
        attribution_path=attrib_path,
        n=4,
        content_id=content_id,
        used_global=set(),
        query_hint=getattr(doc, "query", None),
        bg=getattr(doc, "bg", None),
    )
    print("BG:", bg_paths)
    return bg_paths


# +++ ADD
def prepare_manual_bg(manifest_path: str):
    print("[MANUAL] manifest path:", manifest_path)

    t0 = time.time()
    print("[TIME] start mengolah bg")
    manual_bg_paths = _load_manual_bg_paths(manifest_path)
    print("[TIME] end mengolah bg:", time.time() - t0)

    bg_paths = cache_manual_bg_once(manual_bg_paths)
    print("[MANUAL] BG locals:", bg_paths)
    return bg_paths

def cache_manual_bg_once(
    bg_paths: list[str],
    *,
    cache_dir="assets/manual_bg_cache",
    max_long_side=1800,
) -> list[str]:
    from PIL import Image, ImageOps
    import hashlib, os

    os.makedirs(cache_dir, exist_ok=True)
    out_paths = []

    for p in bg_paths:
        try:
            st = os.stat(p)
            sig = f"{p}:{st.st_mtime_ns}:{max_long_side}"
        except Exception:
            sig = f"{p}:{max_long_side}"

        key = hashlib.sha1(sig.encode()).hexdigest()[:16]
        out = os.path.join(cache_dir, f"{key}.jpg")

        if not os.path.exists(out):
            img = Image.open(p)
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            img = img.convert("RGB")

            w, h = img.size
            long_side = max(w, h)
            if long_side > max_long_side:
                s = max_long_side / float(long_side)
                img = img.resize((int(w*s), int(h*s)), Image.BICUBIC)

            img.save(out, "JPEG", quality=90, subsampling=2, optimize=True)

        out_paths.append(out)

    return out_paths


def list_content_files(contents_root: str, topic: str) -> list[str]:
    folder = os.path.join(contents_root, topic)
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    return files

def _slug_from_filename(p: str) -> str:
    # samakan dengan slug UI (simple)
    stem = Path(p).stem
    out = []
    for ch in stem:
        if ch.isalnum():
            out.append(ch.lower())
        elif ch in ("-", "_"):
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    s = "".join(out).strip("_")
    return s[:80] or "content"

def title_to_slug(text: str, max_len: int = 48) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "_", text).strip("_")
    text = text[:max_len].strip("_")
    return text or "video"

def parse_publish_at(value: str | None, tz_name: str, default_time: str = "14:00") -> datetime | None:
    """
    Support:
      - "YYYY-MM-DD HH:MM"
      - "today HH:MM" / "tomorrow HH:MM"
      - "today" / "tomorrow" (pakai default_time)
    Return timezone-aware datetime.
    """
    if not value:
        return None

    tz = ZoneInfo(tz_name)
    raw = value.strip()

    # 1) format lama: YYYY-MM-DD HH:MM
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=tz)
    except Exception:
        pass

    # 2) shortcut: today/yesterday/tomorrow (+ optional time)
    m = re.match(r"^(today|tomorrow)(?:\s+(\d{1,2}:\d{2}))?$", raw, re.IGNORECASE)
    if not m:
        raise ValueError(f"Format --publish-at tidak dikenali: {value}")

    day_word = m.group(1).lower()
    hhmm = m.group(2) or default_time

    # validasi jam
    if not re.match(r"^\d{1,2}:\d{2}$", hhmm):
        raise ValueError(f"Jam tidak valid di --publish-at: {hhmm}")

    h, mi = hhmm.split(":")
    h = int(h)
    mi = int(mi)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"Jam tidak valid di --publish-at: {hhmm}")

    now = datetime.now(tz).replace(second=0, microsecond=0)
    base = now.replace(hour=h, minute=mi)

    if day_word == "today":
        return base
    if day_word == "tomorrow":
        return base + timedelta(days=1)

    return base

def send_telegram(text: str) -> bool:
    """
    Kirim pesan ke Telegram via Bot API.
    Gunakan env:
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_CHAT_ID
    Return True kalau sukses, False kalau gagal / env tidak ada.
    """
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:3800],            # batas aman (Telegram max 4096)
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            _ = r.read()
        return True
    except Exception:
        return False


def tg_safe(text: str):
    """
    Jangan bikin program crash kalau telegram fail.
    """
    ok = send_telegram(text)
    if not ok:
        print("[WARN] Telegram notif gagal / env belum di-set.")

def next_prime_datetime(now: datetime) -> datetime:
    """
    Prime time Shorts (WIB):
    - Weekdays: 12:00, 18:30, 21:00
    - Weekend: 10:30, 19:00, 21:30
    Pick the next slot >= now.
    """
    weekday = now.weekday()  # 0=Mon
    is_weekend = weekday >= 5

    if is_weekend:
        slots = [(10, 30), (19, 0), (21, 30)]
    else:
        slots = [(12, 0), (18, 30), (21, 0)]

    for h, m in slots:
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate >= now + timedelta(minutes=3):
            return candidate

    # else pick first slot tomorrow
    tomorrow = now + timedelta(days=1)
    h, m = (slots[0][0], slots[0][1])
    return tomorrow.replace(hour=h, minute=m, second=0, microsecond=0)


def to_rfc3339(dt: datetime) -> str:
    # dt should be timezone-aware
    return dt.isoformat(timespec="seconds")


def _fmt(sec: float) -> str:
    sec = float(sec or 0.0)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"

def _parse_voice_pool(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x and x.strip()]

def _purge_bg_cache(img_dir: str, content_id: str) -> int:
    """
    Hapus cache background berdasarkan content_id.
    Contoh file: bg_<content_id>_01.jpg, bg_<content_id>_gradient_01.jpg, dll.
    """
    patterns = [
        os.path.join(img_dir, f"bg_{content_id}_*.jpg"),
        os.path.join(img_dir, f"bg_{content_id}_*.jpeg"),
        os.path.join(img_dir, f"bg_{content_id}_*.png"),
        os.path.join(img_dir, f"bg_{content_id}_*.webp"),
    ]
    removed = 0
    for pat in patterns:
        for fp in glob.glob(pat):
            try:
                os.remove(fp)
                removed += 1
            except Exception:
                pass
    return removed

def cleanup_temp_files(root_dir="."):
    """Menghapus file sampah MoviePy yang tertinggal di root folder."""
    print("[INFO] Membersihkan file temporary...")
    try:
        for filename in os.listdir(root_dir):
            # Ciri-ciri file sampah MoviePy
            if "TEMP_MPY_wvf_snd" in filename and filename.endswith(".mp4"):
                file_path = os.path.join(root_dir, filename)
                try:
                    os.remove(file_path)
                    print(f"🗑️ Dihapus: {filename}")
                except Exception as e:
                    print(f"⚠️ Gagal hapus {filename}: {e}")
    except Exception as e:
        print(f"Error saat cleanup: {e}")

def __load_manual_bg_paths(manifest_path: str) -> list[str]:
    mp = Path(manifest_path)
    if not mp.exists():
        raise FileNotFoundError(f"manual manifest not found: {mp}")
    mdata = json.loads(mp.read_text(encoding="utf-8"))
    sel = mdata.get("selected") or []
    bg_paths = [(x.get("local") or "").strip() for x in sel]
    bg_paths = [p for p in bg_paths if p and os.path.exists(p)]
    if len(bg_paths) != 5:
        raise ValueError(f"manual manifest must have 5 valid local images, got {len(bg_paths)}")
    return bg_paths

def _load_manual_bg_paths(manifest_path: str):
    p = Path(manifest_path)
    if not p.exists():
        raise ValueError(f"manifest not found: {p}")

    data = json.loads(p.read_text(encoding="utf-8"))

    # support beberapa kemungkinan key
    paths = (
        data.get("images")
        or data.get("bg_paths")
        or data.get("backgrounds")
        or data.get("bg")
        or []
    )

    print("[MANUAL] keys in manifest:", list(data.keys()))
    print("[MANUAL] raw paths count:", len(paths))
    print("[MANUAL] raw paths:", paths)

    if not isinstance(paths, list):
        raise ValueError("manifest images/bg_paths must be a list")

    # normalize + validate
    valid = []
    for x in paths:
        if not x:
            continue
        px = Path(x).expanduser()
        if not px.is_absolute():
            px = (p.parent / px).resolve()
        if px.exists() and px.is_file() and px.stat().st_size > 0:
            if px.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                valid.append(str(px))

    if len(valid) != 5:
        raise ValueError(f"manual manifest must have 5 valid local images, got {len(valid)}")

    return valid

def run_short(args, root: str, contents_root: str, out_root: str, tts_dir: str, tmp_dir: str, assets_img_dir: str):
    topic = args.topic or "automotif"

    generated_files: list[str] = []
    files: list[str] = []

    # Counters + error collector (untuk report batch/single)
    uploaded = 0
    upload_errors = 0
    render_errors = 0
    errors: list[str] = []

    # ======================
    # GENERATE RANDOM TXT
    # ======================
    if args.generate and args.generate > 0:
        from ytshorts.content_random import write_random_contents

        tpl_path = args.template or os.path.join(root, "templates", f"{topic}.json")

        try:
            generated_files = write_random_contents(
                contents_root=contents_root,
                topic=topic,
                template_path=tpl_path,
                n=int(args.generate),
                allow_repeat=bool(getattr(args, "allow_repeat", False)),
            )
        except FileNotFoundError:
            print(f"[ERROR] Template tidak ditemukan: {tpl_path}")

            if getattr(args, "tg", False):
                tg_safe(
                    "❌ <b>GENERATE ERROR</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    f"<b>Template:</b> {tpl_path}\n"
                    "Error: Template tidak ditemukan."
                )

            return
        except ValueError as e:
            print(f"[ERROR] generate gagal: {e}")
            print("       Cek jumlah facts di template, atau pakai --allow-repeat")

            if getattr(args, "tg", False):
                tg_safe(
                    "❌ <b>GENERATE ERROR</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    f"<b>Error:</b> {type(e).__name__}: {e}"
                )

            return
        except Exception as e:
            print(f"[ERROR] generate crash: {type(e).__name__}: {e}")

            if getattr(args, "tg", False):
                tb = traceback.format_exc()
                tg_safe(
                    "❌ <b>GENERATE CRASH</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    f"<b>Error:</b> {type(e).__name__}: {e}\n"
                    f"<pre>{tb[-1500:]}</pre>"
                )
            raise

        if not generated_files:
            print("[ERROR] generate selesai tapi tidak menghasilkan file (generated_files kosong).")
            if getattr(args, "tg", False):
                tg_safe(
                    "❌ <b>GENERATE ERROR</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    "generated_files kosong."
                )
            return

        print("Random content created:")
        for p in generated_files:
            print(" -", p)

        if getattr(args, "tg", False):
            tg_safe(
                "📝 <b>GENERATED CONTENT</b>\n"
                f"<b>Topic:</b> {topic}\n"
                f"<b>Count:</b> {len(generated_files)}\n"
                + "\n".join([f"• {os.path.basename(x)}" for x in generated_files[:10]])
                + (f"\n• ... (+{len(generated_files)-10} lagi)" if len(generated_files) > 10 else "")
            )

        if not args.batch:
            return

    # global anti-duplicate BG for this run
    used_bg = set()

    # ======================
    # BATCH MODE
    # ======================
    if args.batch:
        if args.only_generated:
            if not generated_files:
                print("[INFO] --only-generated aktif, tapi tidak ada file hasil --generate pada run ini.")
                return
            files = generated_files[:]
        else:
            files = list_content_files(contents_root, topic)

        # jangan potong dulu, biar bisa loncatin existing dan tetap ngejar limit "made"
        target_make = max(1, int(args.limit))

        if args.shuffle:
            random.shuffle(files)

        made = 0
        skipped = 0

        if not files:
            raise FileNotFoundError(
                f"Tidak ada file konten di: {os.path.join(contents_root, topic)}\n"
                f"Buat contoh: {os.path.join(contents_root, topic, '001.txt')}"
            )

        out_topic_dir = os.path.join(out_root, topic)
        os.makedirs(out_topic_dir, exist_ok=True)

        batch_report = []
        made = 0
        skipped = 0
        t_batch0 = time.perf_counter()

        for idx, fp in enumerate(files):
            if made >= target_make:
                break

            t_item0 = time.perf_counter()

            doc = load_content(contents_root, topic=topic, file_path=fp, seconds_override=args.seconds)

            hook_title = doc.lines[0] if getattr(doc, "lines", None) else Path(fp).stem
            base_slug = title_to_slug(hook_title)

            tag = Path(fp).stem
            slug = f"{base_slug}_{tag}"

            ori = str(m.get("orientation") or (m.get("video") or {}).get("orientation") or "9:16").strip()
            if ori == "16:9":
                out_mp4 = os.path.join(out_topic_dir, f"{slug}_1280x720.mp4")
            else:
                out_mp4 = os.path.join(out_topic_dir, f"{slug}_720x1280.mp4")

            if args.skip_existing and os.path.exists(out_mp4):
                print("Skip existing:", out_mp4)
                batch_report.append({"file": Path(fp).name, "output": Path(out_mp4).name, "status": "skipped", "seconds": 0.0})
                skipped += 1
                continue

            print("\n=== BATCH ITEM ===")
            print("File:", fp)
            print("Out :", out_mp4)

            attrib_path = os.path.join(out_root, f"attribution_{topic}_{tag}.jsonl")
            content_id = f"{topic}_{tag}"

            if args.refresh_bg:
                n_rm = _purge_bg_cache(assets_img_dir, content_id)
                print(f"[INFO] refresh-bg: removed {n_rm} cached BG files for {content_id}")

            bg_paths, used_bg = fetch_backgrounds_for_content(
                lines=doc.lines,
                topic=doc.topic if hasattr(doc, "topic") else topic,
                img_dir=assets_img_dir,
                attribution_path=attrib_path,
                n=4,
                content_id=content_id,
                used_global=used_bg,
                query_hint=getattr(doc, "query", None),
                bg=getattr(doc, "bg", None),
                manual_images=manual_images,
            )
            print("BG:", bg_paths)

            # TTS
            try:
                if args.tts == "elevenlabs":
                    tts_files = make_tts_files(
                        doc.lines,
                        out_dir=tts_dir,
                        engine="elevenlabs",
                        eleven_voice_mode="random_video",
                        eleven_voice_pool=[
                           # "pVnrL6sighQX7hVz89cp",
                           # "UaYTS0wayjmO9KD1LR4R",
                            "1k39YpzqXZn52BgyLyGO",
                            "7VqWGAWwo2HMrylfKrcm",
                        ],
                        seed=123,

                        # edge
                        edge_voice=getattr(args, "edge_voice", None),
                        edge_rate=getattr(args, "edge_rate", "+0%"),
                    )
                else:
                    tts_files = make_tts_files(doc.lines, out_dir=tts_dir, engine="gtts")
            except TypeError:
                if args.tts == "elevenlabs" and args.eleven_voice:
                    tts_files = make_tts_files(doc.lines, out_dir=tts_dir, engine="elevenlabs", eleven_voice_id=args.eleven_voice)
                else:
                    tts_files = make_tts_files(doc.lines, out_dir=tts_dir)

            print("TTS files:", len(tts_files))

            # === LOGIKA BARU: PREPARE REVIEW ===
            if args.prepare_review:
                print(f"[INFO] Mode Prepare Review aktif. Menyimpan project...")

                proj_path = save_project_state(
                    topic=topic,
                    slug=slug,
                    lines=doc.lines,
                    tts_files=tts_files,
                    bg_paths=bg_paths
                )
                print(f"REVIEW_READY:{os.path.basename(proj_path)}") # Keyword untuk ditangkap App.py
                # Stop loop, jangan render video
                return

            # === LOGIKA RENDER BIASA (KODE LAMA) ===

            try:
                wm_text = "" if args.no_watermark else (args.handle or "").strip()

                # ✅ FIX BATCH MODE: set curiosity per file
                my_curiosity = getattr(doc, "hook", None)
                if not my_curiosity and getattr(doc, "lines", None):
                    my_curiosity = doc.lines[0]
                if not my_curiosity:
                    my_curiosity = "Nonton Sampai Habis!"

                build_onefact_video_word_captions(
                    bg_paths, doc.lines, tts_files, out_root, out_mp4,
                    hook_subtitle=args.hook_subtitle,
                    watermark_text=wm_text,
                    watermark_opacity=int(args.watermark_opacity),
                    watermark_position=str(args.watermark_position), doc=doc,
                    cinematic=bool(getattr(args, "cinematic", False)),
                    curiosity_text=my_curiosity 
                )

            except Exception as e:
                render_errors += 1
                msg = f"Render error: {Path(fp).name} | {type(e).__name__}: {e}"
                errors.append(msg)
                print("[ERROR]", msg)
                if getattr(args, "tg", False):
                    tb = traceback.format_exc()
                    tg_safe(
                        "❌ <b>RENDER ERROR</b>\n"
                        f"<b>Topic:</b> {topic}\n"
                        f"<b>File:</b> {Path(fp).name}\n"
                        f"<b>Error:</b> {type(e).__name__}: {e}\n"
                        f"<pre>{tb[-1500:]}</pre>"
                    )
                # lanjut ke konten berikutnya
                continue

            hook = doc.lines[0]
            meta_dir = os.path.join(out_topic_dir, "meta")
            os.makedirs(meta_dir, exist_ok=True)

            md_path = write_youtube_meta_md(
                meta_dir=meta_dir,
                slug=slug,
                hook=hook,
                lines=doc.lines,
                topic=topic,
                channel=args.handle,
                tags=DEFAULT_TAGS,
                auto_hashtags=args.hashtags_auto,
            )

            upload_disabled = False

            # ===============================
            # OPTIONAL UPLOAD BATCH
            # ===============================
            if args.upload and not upload_disabled:
                yt_title = make_title(hook_title)
                bg_variants = []
                if getattr(doc, "bg", None) and isinstance(doc.bg, dict):
                    bg_variants = doc.bg.get("variants") or []

                yt_desc = make_description(
                    doc.lines,
                    channel=args.handle,
                    topic=topic,
                    auto_hashtags=args.hashtags_auto,
                    bg_variants=bg_variants
                )

                publish_rfc3339 = None
                if args.publish_at or args.prime:
                    tz = ZoneInfo(args.tz)

                    # base time untuk item pertama
                    if args.publish_at:
                        base_dt = parse_publish_at(args.publish_at, args.tz, default_time="14:00")
                    else:
                        base_dt = next_prime_datetime(datetime.now(tz))

                    # offset per item batch
                    idx = made  # made sudah +1 setiap video sukses dibuat sebelum upload? kalau belum, pakai enumerate index
                    # Lebih aman: pakai index loop. Jadi tambahkan enumerate di for fp in files:

                    dt = base_dt + timedelta(minutes=int(args.stagger_minutes) * int(idx))
                    publish_rfc3339 = to_rfc3339(dt)
                    print("Scheduled publishAt:", publish_rfc3339)
                try:
                    vid = upload_short(
                        video_path=out_mp4,
                        title=yt_title,
                        description=yt_desc,
                        tags=DEFAULT_TAGS,
                        privacy="unlisted",
                        publish_at_rfc3339=publish_rfc3339,
                    )
                    print("Uploaded:", vid)

                    uploaded += 1
                    if getattr(args, "tg", False):
                        tg_safe(
                            "✅ <b>UPLOADED</b>\n"
                            f"<b>Topic:</b> {topic}\n"
                            f"<b>Video:</b> {Path(out_mp4).name}\n"
                            f"<b>YT ID:</b> {vid}"
                        )

                except UploadLimitExceeded as e:
                    print("[UPLOAD STOPPED]", str(e))
                    print("[INFO] Video berikutnya tetap akan dirender, tapi upload di-skip untuk run ini.")
                    upload_disabled = True  # <= ini kuncinya

                    upload_errors += 1
                    msg = f"Upload limit: {Path(out_mp4).name} | {e}"
                    errors.append(msg)
                    if getattr(args, "tg", False):
                        tg_safe(
                            "⛔ <b>UPLOAD LIMIT</b>\n"
                            f"<b>Topic:</b> {topic}\n"
                            f"<b>Video:</b> {Path(out_mp4).name}\n"
                            f"<b>Msg:</b> {e}"
                        )

                except Exception as e:
                    print(f"[UPLOAD ERROR] {type(e).__name__}: {e}")
                    # pilihan aman: jangan stop run, tapi lanjut ke next video

                    upload_errors += 1
                    msg = f"Upload error: {Path(out_mp4).name} | {type(e).__name__}: {e}"
                    errors.append(msg)
                    if getattr(args, "tg", False):
                        tb = traceback.format_exc()
                        tg_safe(
                            "⚠️ <b>UPLOAD ERROR</b>\n"
                            f"<b>Topic:</b> {topic}\n"
                            f"<b>Video:</b> {Path(out_mp4).name}\n"
                            f"<b>Error:</b> {type(e).__name__}: {e}\n"
                            f"<pre>{tb[-1500:]}</pre>"
                        )

            # meta .md (optional)
            if args.meta:
                meta_dir = os.path.join(out_topic_dir, "meta")
                hook = doc.lines[0] if doc.lines else Path(fp).stem
                meta_slug = f"{slug_from_hook(hook)}_{tag}"
                md_path = write_simple_meta_md(
                    meta_dir=meta_dir,
                    slug=meta_slug,
                    hook=hook,
                    lines=doc.lines,
                    topic=topic,
                    channel=args.handle,
                )
                print("Meta:", md_path)

            elapsed = time.perf_counter() - t_item0
            batch_report.append({"file": Path(fp).name, "output": Path(out_mp4).name, "status": "ok", "seconds": float(elapsed)})
            made += 1
            print("Item time:", _fmt(elapsed))

        total_time = time.perf_counter() - t_batch0
        ok_times = [r.get("seconds", 0.0) for r in batch_report if r.get("status") == "ok"]
        avg_ok = (sum(ok_times) / len(ok_times)) if ok_times else 0.0

        print("\n===== BATCH SUMMARY =====")
        print(f"Topic   : {topic}")
        print(f"Total   : {len(files)}")
        print(f"Made    : {made}")
        print(f"Skipped : {skipped}")
        print(f"Time    : total {_fmt(total_time)} | avg(ok) {_fmt(avg_ok)}")

        for r in batch_report:
            print(f"{r['status']:7} | {_fmt(r.get('seconds', 0.0)):>9} | {r['file']:12} -> {r['output']}")

        if getattr(args, "tg", False):
            err_lines = ""
            if errors:
                top = errors[:10]
                err_lines = "\n".join([f"• {e}" for e in top])
                if len(errors) > 10:
                    err_lines += f"\n• ... (+{len(errors)-10} lagi)"

            tg_safe(
                "📊 <b>BATCH REPORT</b>\n"
                f"<b>Topic:</b> {topic}\n"
                f"<b>Total files:</b> {len(files)}\n"
                f"<b>Rendered ok:</b> {made}\n"
                f"<b>Skipped:</b> {skipped}\n"
                f"<b>Uploaded:</b> {uploaded}\n"
                f"<b>Render errors:</b> {render_errors}\n"
                f"<b>Upload errors:</b> {upload_errors}\n"
                f"<b>Total time:</b> {_fmt(total_time)}\n"
                + (f"\n<b>Errors:</b>\n{err_lines}" if err_lines else "")
            )
        return

    # ======================
    # SINGLE / MANUAL MODE (UNIFIED)
    # ======================

    # === UNCHANGED
    doc = load_content(contents_root, topic=topic, file_path=args.file, seconds_override=args.seconds)

    hook_title = doc.lines[0] if getattr(doc, "lines", None) else (Path(args.file).stem if args.file else topic)
    slug = title_to_slug(hook_title)

    out_topic_dir = os.path.join(out_root, topic)
    os.makedirs(out_topic_dir, exist_ok=True)

    meta_dir = os.path.join(out_topic_dir, "meta")
    os.makedirs(meta_dir, exist_ok=True)
    meta_slug = slug

    # === MOVE (nama output dibedakan di manual)
    out_mp4 = os.path.join(out_topic_dir, f"{slug}_720x1280.mp4")


    # ======================
    # TTS (SAMA)
    # ======================
    if args.tts == "gtts":
        t0 = time.time()
        print("[TIME] start TTS")
        tts_files = make_tts_files(doc.lines, out_dir=tts_dir, engine="gtts", lang="id", prefix="single")
        print("[TIME] end TTS:", time.time() - t0)
    else:
        tts_files = make_tts_files(
            doc.lines,
            out_dir=tts_dir,
            engine="elevenlabs",
            prefix="single",
            eleven_voice_id=(args.eleven_voice or None),
            eleven_voice_mode="random_video",
            eleven_voice_pool=[
                "1k39YpzqXZn52BgyLyGO",
                "7VqWGAWwo2HMrylfKrcm",
            ],
            seed=123,

            # edge
            edge_voice=getattr(args, "edge_voice", None),
            edge_rate=getattr(args, "edge_rate", "+0%"),
        )

    print("TTS files:", len(tts_files))


    # ======================
    # BG PIPELINE (SATU-SATUNYA YANG BEDA)
    # ======================
    if getattr(args, "manual", False):
        bg_paths = prepare_manual_bg(args.manual_manifest)
        out_mp4 = os.path.join(out_topic_dir, f"{slug}_manual_720x1280.mp4")
        print("[MANUAL] OUT:", out_mp4)
    else:
        bg_paths = prepare_auto_bg(doc, topic, assets_img_dir, out_root, args)


    # ======================
    # PREPARE REVIEW (UNCHANGED)
    # ======================
    if args.prepare_review:
        print(f"[INFO] Mode Prepare Review (Single) aktif. Menyimpan project...")
        proj_path = save_project_state(
            topic=topic,
            slug=slug,
            lines=doc.lines,
            tts_files=tts_files,
            bg_paths=bg_paths
        )
        print(f"REVIEW_READY:{os.path.basename(proj_path)}")
        return


    # ======================
    # RENDER (SAMA)
    # ======================
    try:
        wm_text = "" if args.no_watermark else (args.handle or "").strip()

        my_curiosity = getattr(doc, "hook", None)
        if not my_curiosity and doc.lines:
            my_curiosity = doc.lines[0]

        # +++ ADD (TEPAT SEBELUM BUILD)
        render_mode = "manual" if getattr(args, "manual", False) else "auto"
        print(f"[RENDER MODE] {render_mode}")  # DEBUG sementara


        build_onefact_video_word_captions(
            bg_paths,
            doc.lines,
            tts_files,
            out_root,
            out_mp4,
            hook_subtitle=args.hook_subtitle,
            watermark_text=wm_text,
            watermark_opacity=int(args.watermark_opacity),
            watermark_position=str(args.watermark_position),
            cinematic=bool(getattr(args, "cinematic", False)),
            curiosity_text=my_curiosity,
            render_mode=render_mode
        )
        print(f"[RENDER MODE] {render_mode}")

    except Exception as e:
        render_errors += 1
        msg = f"Render error (single/manual): {Path(out_mp4).name} | {type(e).__name__}: {e}"
        errors.append(msg)
        print("[ERROR]", msg)
        return


    # ======================
    # POST RENDER (UNCHANGED)
    # ======================
    md_path = write_youtube_meta_md(
        meta_dir=meta_dir,
        slug=meta_slug,
        hook=doc.lines[0],
        lines=doc.lines,
        topic=topic,
        channel=args.handle,
        tags=DEFAULT_TAGS,
        auto_hashtags=args.hashtags_auto,
    )
    print("Meta saved:", md_path)
	
    # ======================
    # OPTION UPLOAD SINGLE MODE
    # ======================
    if args.upload:
        yt_title = make_title(hook)
        yt_desc = make_description(
            doc.lines,
            channel=args.handle,
            topic=topic,
            auto_hashtags=args.hashtags_auto,
        )

        publish_rfc3339 = None
        if args.publish_at or args.prime:
            tz = ZoneInfo(args.tz)
            now = datetime.now(tz)

            if args.publish_at:
                dt = parse_publish_at(args.publish_at, args.tz, default_time="14:00")
            else:
                dt = next_prime_datetime(now)

            publish_rfc3339 = to_rfc3339(dt)
            print("Scheduled publishAt:", publish_rfc3339)
        try:
            vid = upload_short(
                video_path=out_mp4,
                title=yt_title,
                description=yt_desc,
                tags=DEFAULT_TAGS,
                privacy="unlisted",
                publish_at_rfc3339=publish_rfc3339,
            )

            print("Uploaded:", vid)

            uploaded += 1
            if getattr(args, "tg", False):
                tg_safe(
                    "✅ <b>UPLOADED (SINGLE)</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    f"<b>Video:</b> {Path(out_mp4).name}\n"
                    f"<b>YT ID:</b> {vid}"
                )

        except UploadLimitExceeded as e:
            print("[UPLOAD STOPPED]", str(e))

            upload_errors += 1
            if getattr(args, "tg", False):
                tg_safe(
                    "⛔ <b>UPLOAD LIMIT (SINGLE)</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    f"<b>Video:</b> {Path(out_mp4).name}\n"
                    f"<b>Msg:</b> {e}"
                )

        except Exception as e:
            print(f"[UPLOAD ERROR] {type(e).__name__}: {e}")

            upload_errors += 1
            if getattr(args, "tg", False):
                tb = traceback.format_exc()
                tg_safe(
                    "⚠️ <b>UPLOAD ERROR (SINGLE)</b>\n"
                    f"<b>Topic:</b> {topic}\n"
                    f"<b>Video:</b> {Path(out_mp4).name}\n"
                    f"<b>Error:</b> {type(e).__name__}: {e}\n"
                    f"<pre>{tb[-1500:]}</pre>"
                )

    if args.meta:
        meta_dir = os.path.join(out_root, topic, "meta")
        md_path = write_simple_meta_md(meta_dir, slug_from_hook(hook_title), topic, doc.lines, channel_handle=args.handle)
        print("Meta:", md_path)

    print("Done:", out_mp4)


def load_auto_stock_manifest(manifest_path: str) -> dict:
    mp = Path(manifest_path)
    if not mp.exists():
        raise FileNotFoundError(f"auto-stock manifest not found: {mp}")
    data = json.loads(mp.read_text(encoding="utf-8"))
    if data.get("mode") != "auto_stock":
        raise ValueError("Manifest bukan mode auto_stock.")
    return data

def run_auto_stock(args, root: str, out_root: str, tts_dir: str, tmp_dir: str):
    """
    Entry point untuk:
      python main.py --auto-stock --manifest <path>
    Manifest dibuat dari tab Streamlit.
    """
    if not args.manifest:
        raise ValueError("--manifest wajib diisi untuk --auto-stock")

    m = load_auto_stock_manifest(args.manifest)

    print("[AUTO-STOCK] manifest keys:", list(m.keys()))
    print("[AUTO-STOCK] manifest video.orientation:", (m.get("video") or {}).get("orientation"))
    print("[AUTO-STOCK] manifest render.orientation:", (m.get("render") or {}).get("orientation"))
    print("[AUTO-STOCK] manifest orientation:", m.get("orientation"))
    print("[AUTO-STOCK] manifest variant:", m.get("variant"), (m.get("render") or {}).get("variant"))

    # output folder: tetap pakai out_root seperti pipeline anda
    topic = (args.topic or "automotif").strip()
    out_topic_dir = os.path.join(out_root, topic)
    os.makedirs(out_topic_dir, exist_ok=True)

    # 1) Ambil scenes + clip paths
    scenes = m.get("scenes") or []
    clip_paths = []
    for s in scenes:
        cp = (s.get("clip_path") or "").strip()
        if cp and os.path.exists(cp):
            clip_paths.append(cp)

    if not clip_paths:
        raise ValueError("Manifest tidak punya clip_path yang valid (tidak ditemukan).")

    print("PROGRESS: 5%")
    print("[AUTO-STOCK] clips:", len(clip_paths))

    # 2) Audio (TTS) dari manifest (jika ada)
    audio = (m.get("audio") or {})
    audio_path = audio.get("audio_path")
    has_audio = bool(audio.get("tts_enabled") and audio_path and os.path.exists(audio_path))

    tts_files = []
    if has_audio:
        # Copy audio ke out_root/tts biar konsisten
        os.makedirs(tts_dir, exist_ok=True)
        dst = os.path.join(tts_dir, f"auto_stock_{Path(args.manifest).stem}.mp3")
        try:
            shutil.copy2(audio_path, dst)
        except Exception:
            dst = audio_path
        tts_files = [dst]

    print("PROGRESS: 10%")
    print("[AUTO-STOCK] audio:", "yes" if tts_files else "no")

    # 3) Captions SRT
    captions = (m.get("captions") or {})
    cap_font = int(captions.get("font_size") or 20)
    cap_pos  = str(captions.get("position_name") or "Bottom")
    srt_path = captions.get("srt_path")
    if srt_path and not os.path.exists(srt_path):
        srt_path = None

    # 4) Output file name + ukuran dari orientation manifest
    output_cfg = (m.get("output") or {}) if isinstance(m, dict) else {}
    video_cfg  = (m.get("video") or {}) if isinstance(m, dict) else {}
    render_cfg = (m.get("render") or {}) if isinstance(m, dict) else {}

    ori = str(
        video_cfg.get("orientation")
        or render_cfg.get("orientation")
        or output_cfg.get("orientation")
        or output_cfg.get("aspect")
        or m.get("orientation")
        or "9:16"
    ).strip()
    W, H = _size_from_orientation(ori)

    ts = m.get("created_at") or time.strftime("%Y%m%d_%H%M%S")
    slug = f"auto_stock_{ts}"
    out_mp4 = os.path.join(out_topic_dir, f"{slug}_{W}x{H}.mp4")

    print("PROGRESS: 15%")
    print("[AUTO-STOCK] orientation:", ori, "size:", f"{W}x{H}")
    print("[AUTO-STOCK] OUT:", out_mp4)

    from ytshorts.video_word import build_onefact_video_stock_captions

    print("[AUTO-STOCK] audio_path:", audio_path)
    if audio_path:
        print("[AUTO-STOCK] audio_exists:", os.path.exists(audio_path), "size:", os.path.getsize(audio_path) if os.path.exists(audio_path) else 0)

    print("PROGRESS: 20%")
    print("[AUTO-STOCK] captions font_size:", cap_font, "pos:", cap_pos, flush=True)

    wm_text = "" if getattr(args, "no_watermark", False) else (getattr(args, "handle", "") or "").strip()

    # --- BGM settings from manifest ---
    bgm_cfg = (m.get("bgm") or {})
    bgm_enabled = bool(bgm_cfg.get("enabled", True))
    bgm_volume  = float(bgm_cfg.get("volume", 0.20))

    # --- AVATAR ---
    avatar_cfg = ((m.get("render") or {}).get("avatar") or {})
    avatar_enabled = bool(avatar_cfg.get("enabled"))
    assets_dir = (m.get("assets_dir") or "")

    # ✅ BUILD kwargs dulu
    kwargs = {
        "scenes": scenes,
        "out_mp4": out_mp4,
        "audio_path": (tts_files[0] if tts_files else None),
        "captions_srt": srt_path,
        "caption_font_size": cap_font,
        "caption_position": cap_pos,
        "hook_subtitle": getattr(args, "hook_subtitle", "FAKTA CEPAT"),
        "watermark_text": wm_text,
        "watermark_opacity": int(getattr(args, "watermark_opacity", 120)),
        "watermark_position": str(getattr(args, "watermark_position", "top-right")),
        "cinematic": bool(getattr(args, "cinematic", False)),
        "bgm_enabled": bgm_enabled,
        "bgm_volume": bgm_volume,
        "avatar_cfg": (avatar_cfg if avatar_enabled else None),
        "assets_dir": assets_dir,
    }

    # ✅ kalau builder support size, inject ukuran
    try:
        sig = inspect.signature(build_onefact_video_stock_captions)
        params = sig.parameters
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

        # kalau fungsi TIDAK punya **kwargs, buang key yang tidak dikenal biar aman
        if not has_varkw:
            kwargs = {k: v for k, v in kwargs.items() if k in params}

        if "frame_size" in params:
            kwargs["frame_size"] = (W, H)
        elif "size" in params:
            kwargs["size"] = (W, H)
        elif "target_size" in params:
            kwargs["target_size"] = (W, H)
    except Exception:
        pass

    # ✅ CALL sekali saja
    out_final = build_onefact_video_stock_captions(**kwargs)

    if out_final:
        out_final = _force_mp4_size_ffmpeg(str(out_final), W, H)

    final_print = str(out_final or out_mp4)
    print(f"OUTPUT_MP4: {final_print}", flush=True)
    print("PROGRESS: 100%")
    print("Done:", final_print)

def purge_bg_cache_for_topic(assets_img_dir: str, topic: str) -> int:
    """
    Pindahkan file BG cache yg berkaitan dengan topic ke:
      assets/images/_purge_bg_cache/<topic>/<timestamp>/
    Pattern yg dipindah:
      bg_<topic>_*.jpg/.png/.webp
    """
    topic = (topic or "").strip().lower()
    if not topic:
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    dst_dir = os.path.join(assets_img_dir, "_purge_bg_cache", topic, ts)
    os.makedirs(dst_dir, exist_ok=True)

    moved = 0
    exts = ("*.jpg", "*.jpeg", "*.png", "*.webp")

    # contoh file: bg_automotif_single_01.jpg, bg_automotif_auto_001_01.jpg, dll
    for ext in exts:
        pat = os.path.join(assets_img_dir, f"bg_{topic}_*{ext[1:]}")
        # catatan: glob pattern di atas "aneh", jadi kita pakai glob manual yang jelas:
    patterns = []
    for ext in exts:
        patterns.append(os.path.join(assets_img_dir, f"bg_{topic}_*{ext[1:]}"))  # fallback
        patterns.append(os.path.join(assets_img_dir, f"bg_{topic}_*{ext}"))      # normal

    seen = set()
    for pat in patterns:
        for fp in glob.glob(pat):
            if fp in seen:
                continue
            seen.add(fp)
            try:
                shutil.move(fp, os.path.join(dst_dir, os.path.basename(fp)))
                moved += 1
            except Exception:
                pass

    # juga pindahkan gradient cache yg biasanya bg_<topic>_..._gradient_XX.jpg
    for fp in glob.glob(os.path.join(assets_img_dir, f"bg_{topic}_*gradient*.*")):

        if fp in seen:
            continue
        try:
            shutil.move(fp, os.path.join(dst_dir, os.path.basename(fp)))
            moved += 1
        except Exception:
            pass

    return moved


def run_generate(args, root: str, contents_root: str, assets_img_dir: str) -> list[str]:
    """
    Generate konten dari template dan rapihin BG cache biar tidak nyampur.
    Return: list file .txt yang dibuat
    """
    from ytshorts.content_random import write_random_contents

    topic = args.topic or "automotif"
    tpl_path = args.template or os.path.join(root, "templates", f"{topic}.json")

    # Purge BG cache dulu biar fresh (biar nggak “ketuker” sama cache lama)
    moved = purge_bg_cache_for_topic(assets_img_dir, topic)
    if moved:
        print(f"[INFO] Purged BG cache: moved {moved} file(s) to assets/images/_purge_bg_cache/{topic}/...")

    generated_files = write_random_contents(
        contents_root=contents_root,
        topic=topic,
        template_path=tpl_path,
        n=int(args.generate),
        allow_repeat=bool(getattr(args, "allow_repeat", False)),
    )
    return generated_files

def main():
    parser = argparse.ArgumentParser(
        prog="yt-automation-onefact",
        description="Generate YouTube Shorts otomatis (content -> BG -> TTS -> video + meta + optional upload).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("-t", "--topic", default="automotif", help="Topic folder inside contents/<topic>")
    parser.add_argument("-f", "--file", default=None, help="Path file konten .txt (mode single)")
    parser.add_argument("--seconds", type=int, default=None, help="Override target seconds (optional)")

    # meta
    parser.add_argument("--meta", action="store_true", help="Generate title+description .md (YouTube ready)")
    parser.add_argument("--handle", default="@yourchannel", help="Channel handle untuk CTA (contoh: @AutoFactID)")

    # Watermark
    parser.add_argument("--no-watermark", action="store_true", help="Disable watermark")
    parser.add_argument("--watermark-opacity", type=int, default=120, help="0-255")
    parser.add_argument("--watermark-position", default="top-right",
                    choices=["top-right","top-left","bottom-right","bottom-left"])

    # random content generator
    parser.add_argument("--generate", type=int, default=0, help="Buat random content sebanyak N ke contents/<topic>/")
    parser.add_argument("--template", default=None, help="Path template JSON (default: templates/<topic>.json)")
    parser.add_argument(
        "--only-generated",
        action="store_true",
        help="Jika dipakai bersama --generate dan --batch: render hanya file yang baru dibuat",
    )
    parser.add_argument(
        "--allow-repeat",
        action="store_true",
        help="Saat --generate: boleh mengulang fakta yang sudah pernah digenerate (default: tidak)",
    )

    # upload youtube
    parser.add_argument("--upload", action="store_true", help="Upload ke YouTube setelah render")
    parser.add_argument("--publish-at", default=None, help='Jadwalkan tayang: "YYYY-MM-DD HH:MM" (local tz)')
    parser.add_argument("--prime", action="store_true", help="Auto schedule jam prime berikutnya")
    parser.add_argument("--tz", default="Asia/Jakarta", help="Timezone untuk schedule (default Asia/Jakarta)")
    parser.add_argument("--hashtags-auto", action="store_true", help="Auto hashtags berdasarkan topic")
    parser.add_argument(
        "--stagger-minutes",
        type=int,
        default=0,
        help="Kalau upload batch: jeda menit antar publishAt (contoh 30 => tiap video beda 30 menit).",
    )

    # TTS
    parser.add_argument("--tts", default="gtts", choices=["gtts", "edge", "elevenlabs"], help="Pilih TTS engine")
    parser.add_argument("--eleven-voice", default=None, help="Override ElevenLabs voice_id (optional)")
    parser.add_argument(
        "--eleven-voice-mode",
        default="fixed",
        choices=["fixed", "random_video", "random_line"],
        help="Mode pemilihan voice ElevenLabs",
    )
    parser.add_argument(
        "--eleven-voice-pool",
        default=None,
        help='List voice_id dipisah koma untuk mode random. Contoh: "id1,id2"',
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed random untuk pemilihan voice (biar rerender konsisten).",
    )
    parser.add_argument("--edge-voice", default=os.getenv("EDGE_TTS_VOICE", "id-ID-ArdiNeural"))
    parser.add_argument("--edge-rate", default="+0%")

    # batch
    parser.add_argument("--batch", action="store_true", help="Generate banyak video dari folder topic")
    parser.add_argument("--limit", type=int, default=5, help="Jumlah video untuk batch (default 5)")
    parser.add_argument("--shuffle", action="store_true", help="Acak urutan file konten")
    parser.add_argument("--skip-existing", action="store_true", help="Skip kalau output mp4 sudah ada")

    # long video
    parser.add_argument(
        "--generate-long",
        action="store_true",
        help="Generate long script otomatis dari templates/<topic>.json -> long/<topic>/YYYYMMDD_HHMMSS_NNN_script.md",
    )
    parser.add_argument(
        "--nseg",
        type=int,
        default=7,
        help="Jumlah segmen/fakta untuk long script (default 7). Diambil random dari template JSON.",
    )
    parser.add_argument(
        "--render-long",
        action="store_true",
        help="Jika dipakai bersama --generate-long: setelah script dibuat langsung render long video.",
    )
    parser.add_argument(
        "--mode",
        choices=["short", "long"],
        default="short",
        help="Pilih mode: short (default) atau long. Long butuh --script.",
    )
    parser.add_argument(
        "--script",
        default=None,
        help="Path script long (.md). Contoh: long/automotif/20260124_154233_001_script.md",
    )
    parser.add_argument(
        "--tts-long",
        default="gtts",
        choices=["gtts", "elevenlabs"],
        help="TTS untuk long video (pisah dari shorts).",
    )
    parser.add_argument(
        "--eleven-voice-long",
        default=None,
        help="Voice ID ElevenLabs untuk long (kalau --tts-long elevenlabs).",
    )
    parser.add_argument(
        "--long-caption",
        choices=["off", "fact"],
        default="fact",
        help="Caption untuk long video: off=tanpa caption, fact=tampilkan FAKTA di awal segmen",
    )
    parser.add_argument(
        "--long-json",
        dest="long_json",
        default=None,
        help="Path JSON long (workspace user templates/*.json).",
    )
    # BG
    parser.add_argument(
        "--refresh-bg",
        action="store_true",
        help="Paksa refresh background: hapus cache bg untuk content_id sebelum fetch",
    )
    # Cinematic
    parser.add_argument(
        "--cinematic",
        action="store_true",
        help="Aktifkan cinematic look (matte overlay halus). Default OFF.",
    )
    # === ADD: hook subtitle ===
    parser.add_argument(
        "--hook-subtitle",
        default="FAKTA CEPAT",
        help='Subtitle hook impact (contoh: "FAKTA CEPAT")',
    )

    # Manual generate image render 
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Gunakan images manual dari manifest temp/manual_manifests/<topic>_<slug>_manual_5images.json (default: False)",
    )
    parser.add_argument(
        "--manual-manifest",
        default=None,
        help="Path manifest manual (optional). Kalau kosong, auto-cari berdasarkan --file dan --topic.",
    )

    # Telegram Notification
    parser.add_argument("--tg", action="store_true", help="Kirim report ke Telegram")

    # Edit rendering
    parser.add_argument("--prepare-review", action="store_true", help="Hanya download aset & TTS, simpan ke review folder, JANGAN render video.")
    parser.add_argument("--render-review", type=str, default=None, help="Nama folder project di review_projects/ untuk dirender (misal: automotif_fakta_01).")

    # === AUTO STOCK VIDEO (NEW) ===
    parser.add_argument(
        "--auto-stock",
        action="store_true",
        help="Auto short video dari stock clips berdasarkan manifest JSON (generated by Streamlit tab).",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path manifest JSON untuk --auto-stock.",
    )

    args = parser.parse_args()

    topic = args.topic or "automotif"

    # paths (WAJIB sebelum generate)
    root = os.getcwd()
    contents_root = os.path.join(root, "contents")
    out_root = os.path.join(root, "out")
    tts_dir = os.path.join(out_root, "tts")
    tmp_dir = os.path.join(out_root, "_tmp")

    os.makedirs(out_root, exist_ok=True)
    os.makedirs(tts_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    assets_img_dir = os.path.join(root, "assets", "images")
    os.makedirs(assets_img_dir, exist_ok=True)

    # ======================
    # GENERATE RANDOM TXT
    # ======================
    if args.generate and int(args.generate) > 0:
        generated_files = run_generate(args, root, contents_root, assets_img_dir)

        print("Random content created:")
        for p in generated_files:
            print(" -", p)

        if not args.batch:
            return

    # ======================
    # AUTO STOCK pipeline (NEW)
    # ======================
    if getattr(args, "auto_stock", False):
        run_auto_stock(args, root, out_root, tts_dir, tmp_dir)
        return

    # ======================
    # SHORT pipeline (batch/single)
    # ======================
        # AUTO STOCK
    if getattr(args, "auto_stock", False):
        run_auto_stock(args, root, out_root, tts_dir, tmp_dir)
        return

    # LONG MODE (wajib lebih dulu daripada short)
    if (
        str(getattr(args, "mode", "short")).lower() == "long"
        or bool(getattr(args, "long_json", None))
        or bool(getattr(args, "generate_long", False))
        or bool(getattr(args, "render_long", False))
        or bool(getattr(args, "script", None))
    ):
        # Pastikan kamu memang punya fungsi run_long(...)
        run_long(args, root, contents_root, out_root, tts_dir, tmp_dir, assets_img_dir)
        return

    # DEFAULT SHORT
    run_short(args, root, contents_root, out_root, tts_dir, tmp_dir, assets_img_dir)

if __name__ == "__main__":
    main()


