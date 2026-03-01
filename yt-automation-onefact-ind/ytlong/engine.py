import traceback
import os
import time
import sys
import json
import random
import requests
import textwrap
import numpy as np
import math
from pathlib import Path
from dotenv import load_dotenv
from ytlong.hook_cta import make_hook_clip as make_hook_clip
from ytlong.hook_cta import make_cta_clip as make_cta_clip
from proglog import ProgressBarLogger
from ytlong.hook_cta import BannerStyle, apply_branding
from ytlong.hook_cta import make_fact_caption_clip, FactCaptionStyle

# --- MOVIEPY V2 IMPORTS ---
from moviepy import (
    AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
    concatenate_videoclips, ColorClip, CompositeAudioClip,
    vfx, afx
)

# --- AUDIO & SEARCH ---
from gtts import gTTS
try:
    from duckduckgo_search import DDGS
    HAS_DDG = True
except ImportError:
    HAS_DDG = False

load_dotenv()
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

# --- KONFIGURASI ---
TEMP_DIR = "temp_long"
RESULTS_DIR = "results"
ASSET_DIR = "assets"

for d in [TEMP_DIR, RESULTS_DIR, ASSET_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

def get_pexels_image(query):
    """Mengambil satu URL gambar dari Pexels berdasarkan query"""
    headers = {"Authorization": PEXELS_API_KEY}
    url = f"https://api.pexels.com/v1/search?query={query}&per_page=1"

    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        if data['photos']:
            return data['photos'][0]['src']['large2x']  # Ambil ukuran besar
    except Exception as e:
        print(f"❌ Pexels Error: {e}")

    # Fallback jika Pexels gagal (pake placeholder)
    return "https://via.placeholder.com/1920x1080.png?text=Image+Not+Found"

# ==========================================
# 1. HELPER: TEXT TO SPEECH
# ==========================================


def generate_tts(text, filename, engine="gtts", voice_id=None):
    global TEMP_DIR

    # Validasi input untuk mencegah error join lainnya
    if filename is None:
        filename = "temp_audio.mp3"

    path = os.path.join(TEMP_DIR, filename)

    text = str(text).strip()
    if not text: return None

    # Hapus file lama
    if os.path.exists(path):
        os.remove(path)

    # Coba gTTS
    try:
        tts = gTTS(text=text, lang='id', slow=False)
        tts.save(path)
        return path
    except Exception as e:
        print(f"❌ TTS Error (Cek Internet): {e}")
        return None

# ==========================================
# 2. HELPER: IMAGE SEARCH
# ==========================================


def get_image_url(query):
    if not HAS_DDG: return None
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=1))
            if results: return results[0]['image']
    except Exception as e:
        print(f"⚠️ Image Search Error (Cek Internet): {e}")
    return None


def download_image(url, filename):
    path = os.path.join(TEMP_DIR, filename)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Authorization": PEXELS_API_KEY
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            with open(path, "wb") as f:
                f.write(response.content)
            return path
    except Exception as e:
        print(f"❌ Download Error: {e}")
    return None

def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)

def make_bg_cover_clip(img_path: str, duration: float, frame_size=(1920, 1080)):
    W, H = frame_size
    duration = float(duration)

    clip = ImageClip(img_path).with_duration(duration)

    # cover + overscan
    iw, ih = clip.size
    base_scale = max(W / iw, H / ih)
    overscan = 1.18
    clip = clip.resized(base_scale * overscan)

    # seeded motion (konsisten per gambar)
    seed = abs(hash(img_path)) % (2**32)
    rng = random.Random(seed)

    # zoom halus
    z0, z1 = 1.00, 1.06
    def zoom(t):
        p = _smoothstep(t / max(duration, 0.001))
        return z0 + (z1 - z0) * p

    clip = clip.with_effects([vfx.Resize(lambda t: zoom(t))])

    # pan start->end
    max_dx = int(W * 0.05)
    max_dy = int(H * 0.03)
    dx0 = rng.randint(-max_dx, max_dx)
    dy0 = rng.randint(-max_dy, max_dy)
    dx1 = rng.randint(-max_dx, max_dx)
    dy1 = rng.randint(-max_dy, max_dy)

    def crop_x1(t):
        p = _smoothstep(t / max(duration, 0.001))
        dx = dx0 + (dx1 - dx0) * p
        x1 = int((clip.w - W) / 2 + dx)
        return x1

    def crop_y1(t):
        p = _smoothstep(t / max(duration, 0.001))
        dy = dy0 + (dy1 - dy0) * p
        y1 = int((clip.h - H) / 2 + dy)
        return y1

    # ✅ Dynamic crop TANPA moviepy.video.fx.Crop
    def _dyn_crop(gf, t):
        frame = gf(t)
        x1 = int(crop_x1(t))
        y1 = int(crop_y1(t))

        fh, fw = frame.shape[0], frame.shape[1]
        x1 = max(0, min(x1, fw - W))
        y1 = max(0, min(y1, fh - H))

        out = frame[y1:y1+H, x1:x1+W]
        return np.ascontiguousarray(out)

    if hasattr(clip, "transform"):
        clip = clip.transform(lambda gf, t: _dyn_crop(gf, t))
    else:
        clip = clip.fl(lambda gf, t: _dyn_crop(gf, t))

    # optional: lebih hidup
    try:
        clip = clip.with_effects([vfx.LumContrast(0, 0.08, 127)])
    except Exception:
        try:
            clip = clip.with_effects([vfx.LumContrast(0, 0.08)])
        except Exception:
            pass

    # pastikan ukuran final benar
    return CompositeVideoClip([clip], size=(W, H)).with_duration(duration)

def pick_random_bgm(bgm_dir="assets/bgm"):
    """Ambil 1 file audio random dari folder assets/bgm (mp3/wav/m4a/aac/ogg)."""
    p = Path(bgm_dir)
    if not p.exists():
        return None

    exts = ("*.mp3", "*.wav", "*.m4a", "*.aac", "*.ogg")
    files = []
    for e in exts:
        files.extend(p.glob(e))

    if not files:
        return None

    return str(random.choice(files))

# ==========================================
# 3. MAIN BUILDER
# ==========================================


def build_long_video(data, tts_engine="gtts", voice_id=None, no_watermark=False, watermark_text=None, hook_subtitle=None):
    title = data.get('title', 'Untitled')
    print(f"🚀 Starting Engine for: {title}", flush=True)

    print("DEBUG engine cwd:", os.getcwd(), flush=True)
    print("DEBUG engine python:", sys.executable, flush=True)
    print("DEBUG engine tts_engine:", tts_engine, "voice_id:", voice_id, flush=True)

    segments = []
    clips = []

    # 1. Tambahkan Hook
    hook_text = (data.get("hook") or "").strip()
    cta_text = (data.get("cta") or "").strip()
    if hook_text:
        segments.append({
            "type": "hook",
            "heading": hook_text,   # tampilkan HOOK sebagai caption
            "text": hook_text,
            "query": f"{title} intro",
        })

    # 2. Tambahkan Konten Utama (content_flow / chapters)
    content_list = data.get("content_flow") or data.get("chapters") or []
    for item in content_list:
        seg_title = (item.get("segmen") or item.get("heading") or "FAKTA").strip()
        seg_text  = (item.get("narasi") or item.get("text") or "").strip()
        if not seg_text:
            continue  # skip kalau kosong biar TTS tidak error

        segments.append({
            "type": "chapter",
            "heading": seg_title,   # chapter tampil sebagai heading pendek
            "text": seg_text,       # narasi untuk TTS
            "query": f"{title} {seg_title}",
        })

    # 3. Tambahkan CTA
    if cta_text:
        segments.append({
            "type": "cta",
            "heading": cta_text,    # tampilkan CTA sebagai caption
            "text": cta_text,
            "query": f"{title} closing",
        })

    total_segs = len(segments)
    print(f"✅ Total Segmen yang akan diproses: {total_segs}", flush=True)

    for i, seg in enumerate(segments):
        try:
            print(f"⚙️ Processing Segment {i+1}/{total_segs}: {seg['heading']}", flush=True)
            FRAME_SIZE = (1920, 1080)

            # audio
            audio_filename = f"temp_audio_{i}.mp3"
            audio_full_path = os.path.join(TEMP_DIR, audio_filename)
            generate_tts(seg['text'], audio_filename)

            if not os.path.exists(audio_full_path):
                raise RuntimeError("Audio TTS gagal dibuat")

            audio_clip = AudioFileClip(audio_full_path)
            duration = audio_clip.duration

            if duration <= 0:
                raise RuntimeError("Durasi audio 0")

            # image
            img_url = get_pexels_image(seg['query'])
            img_filename = f"temp_img_{i}.jpg"
            img_local_path = download_image(img_url, img_filename)

            print("DEBUG before BG | img_local_path:", img_local_path, flush=True)
            print("DEBUG before BG | duration:", duration, "FRAME_SIZE:", FRAME_SIZE, flush=True)

            if img_local_path and os.path.exists(img_local_path):
                img_clip = make_bg_cover_clip(img_local_path, duration, FRAME_SIZE)
            else:
                img_clip = ColorClip(size=FRAME_SIZE, color=(0,0,0)).with_duration(duration)

            # hitung total chapter untuk numbering caption (FAKTA 1/N)
            chapter_total = sum(1 for s in segments if s.get("type") == "chapter")
            chapter_idx = 0  # naik hanya saat chapter

            overlays = [img_clip]

            if seg.get("type") == "chapter":
                chapter_idx += 1

                # setelah img_clip dibuat
                fact_style = FactCaptionStyle(
                    y_ratio=0.64,          # posisi aman
                    text_size_ratio=0.052, # agak gede
                )

                cap = make_fact_caption_clip(
                    FRAME_SIZE,
                    seg["heading"],              # <-- judul segmen / fakta
                    segment_index=chapter_idx,
                    segment_total=chapter_total,
                    subtitle="",      # opsional (boleh None)
                    duration=min(2.4, duration), # jangan lebih panjang dari segmen
                    start=0.10,
                    style=fact_style,
                )
                overlays.append(cap)

            # gabung
            final_seg = CompositeVideoClip(overlays, size=FRAME_SIZE)
            final_seg = final_seg.with_audio(audio_clip).with_fps(24)

            clips.append(final_seg)
            print(f"✅ Segment {i+1} OK", flush=True)

        except Exception as e:
            print(f"❌ Segment {i+1} FAILED: {e}", flush=True)
            traceback.print_exc()
            continue

    print(f"DEBUG: total clips dibuat = {len(clips)}", flush=True)
    if not clips:
        print("❌ GAGAL TOTAL: Tidak ada klip yang berhasil dibuat")
        return

    if not clips:
        print("❌ GAGAL TOTAL: Tidak ada klip yang berhasil dibuat")
        return

    print("🔗 Stitching Video...")
    final_video = concatenate_videoclips(clips, method="compose")

    bgm_path = pick_random_bgm("assets/bgm")
    if bgm_path:
        try:
            print(f"🎵 Using BGM: {bgm_path}", flush=True)
            bg_music = AudioFileClip(bgm_path)

            if bg_music.duration < final_video.duration:
                bg_music = afx.audio_loop(bg_music, duration=final_video.duration)
            else:
                bg_music = bg_music.subclipped(0, final_video.duration)

            bg_music = bg_music.with_volume_scaled(0.15)  # atur volume bgm
            new_audio = CompositeAudioClip([final_video.audio, bg_music])
            final_video = final_video.with_audio(new_audio)
        except Exception as e:
            print(f"⚠️ BGM Error: {e}", flush=True)
    else:
        print("ℹ️ No BGM found in assets/bgm/", flush=True)

    # =========================
    # HOOK + CTA BANNER OVERLAY (LONG ONLY)
    # =========================
    try:
        # pastikan hook_text/cta_text masih ada (dia didefinisikan di atas)
        _hook = (hook_text or "").strip()
        _cta  = (cta_text or "").strip()

        if _hook or _cta:
            # simpan audio dulu biar aman
            _audio = final_video.audio

            style = BannerStyle(
                title_size_ratio=0.100,
                cta_size_ratio=0.100,
                subtitle_size_ratio=0.075,
                bottom_y=0.18,
            )

            final_video = apply_branding(
                final_video,
                hook=(data.get("hook") or "").strip(),
                hook_subtitle=(hook_subtitle or "").strip(),
                cta=(data.get("cta") or "").strip(),
                wm_handle=watermark_text or "",   # <- ini isinya wm_handle dari UI kamu
                no_watermark=no_watermark,
                watermark_position="top-right",
                watermark_opacity=120,
                style=style,
            )

            # re-attach audio kalau perlu (aman untuk jaga-jaga)
            if _audio is not None:
                final_video = final_video.with_audio(_audio)

            print("✅ Hook/CTA banner applied (overlay).", flush=True)

    except Exception as e:
        tb = traceback.format_exc()
        print("⚠️ Hook/CTA overlay error:", repr(e), flush=True)
        print(tb, flush=True)

        with open("hook_cta_error.log", "a", encoding="utf-8") as f:
            f.write("\n\n" + "="*70 + "\n")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(repr(e) + "\n")
            f.write(tb)

    # --- 6. RENDER ---
    timestamp = int(time.time())
    safe_title = "".join([c for c in data.get('title', 'video') if c.isalnum() or c==' ']).strip().replace(' ', '_')
    out_path = os.path.join(RESULTS_DIR, f"{safe_title}_{timestamp}.mp4")

    # ✅ jadikan absolute supaya JobStore bisa detect
    out_path = str(Path(out_path).resolve())

    print(f"💾 Rendering to: {out_path}", flush=True)

    final_video.write_videofile(
        out_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        bitrate="3000k",
        audio_bitrate="128k",
        threads=2,
        preset="ultrafast",
    )

    # ✅ penting untuk JobStore parser
    print(f"OUTPUT_MP4: {out_path}", flush=True)
    print("Done:", out_path, flush=True)
    return out_path

if __name__ == "__main__":
    # Test
    build_long_video({"title": "Test Offline", "hook": "Test", "chapters": [{"heading": "A", "text": "Isi A"}]})
