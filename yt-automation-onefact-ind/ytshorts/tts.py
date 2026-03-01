import os
import re
import hashlib
import random
from typing import List, Optional, Literal

import requests
from gtts import gTTS

TTSEngine = Literal["gtts", "edge", "elevenlabs"]


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


def _safe_slug(s: str, max_len: int = 32) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if s else "line"


def _tts_gtts(line: str, out_path: str, lang: str = "id") -> None:
    tts = gTTS(text=line, lang=lang, slow=False)
    tts.save(out_path)

def _tts_edge(
    line: str,
    out_path: str,
    *,
    voice: str = "id-ID-ArdiNeural",
    rate: str = "+0%",   # edge-tts format: "+0%" atau "-10%" dsb
) -> None:
    try:
        import asyncio
        import edge_tts
    except Exception as e:
        raise RuntimeError(f"edge-tts tidak tersedia: {e}")

    async def _do():
        communicate = edge_tts.Communicate(line, voice=voice, rate=rate)
        await communicate.save(out_path)

    try:
        asyncio.run(_do())
    except RuntimeError:
        # kalau sudah ada event loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_do())
        finally:
            loop.close()


def _tts_elevenlabs(
    line: str,
    out_path: str,
    *,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_multilingual_v2",
    stability: float = 0.45,
    similarity_boost: float = 0.80,
    style: float = 0.10,
    use_speaker_boost: bool = True,
    output_format: str = "mp3_44100_128",
) -> None:
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY kosong. Set env ELEVENLABS_API_KEY.")
    if not voice_id:
        raise ValueError("ELEVENLABS_VOICE_ID kosong. Set env ELEVENLABS_VOICE_ID atau pakai --eleven-voice.")

    # Paksa 44.1kHz supaya durasi tidak “ngaco/cepat”
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={output_format}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": line,
        "model_id": model_id,
        "voice_settings": {
            "stability": float(stability),
            "similarity_boost": float(similarity_boost),
            "style": float(style),
            "use_speaker_boost": bool(use_speaker_boost),
        },
    }

    r = requests.post(url, headers=headers, json=payload, timeout=120)
    # kalau key invalid/unauthorized -> lempar error khusus agar bisa fallback
    if r.status_code in (401, 403):
        raise RuntimeError("ELEVENLABS_AUTH_ERROR")
    raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:400]}")

    with open(out_path, "wb") as f:
        f.write(r.content)


def make_tts_files(
    lines: List[str],
    out_dir: str,
    lang: str = "id",
    prefix: str = "",
    *,
    engine: TTSEngine = "gtts",
    seed: Optional[int] = None,  # random deterministik (None = random tiap render)

    # Edge
    edge_voice: Optional[str] = None,
    edge_rate: str = "+0%",

    # ElevenLabs
    eleven_api_key: Optional[str] = None,
    eleven_voice_id: Optional[str] = None,  # untuk mode fixed
    eleven_voice_pool: Optional[List[str]] = None,  # untuk random (isi 3 voice_id)
    eleven_voice_mode: Literal["fixed", "random_video", "random_line"] = "fixed",
    eleven_model_id: str = "eleven_multilingual_v2",
    stability: float = 0.45,
    similarity_boost: float = 0.80,
    style: float = 0.10,
    use_speaker_boost: bool = True,
    output_format: str = "mp3_44100_128",
) -> List[str]:
    """
    Cepat:
    - Cache per line (file name pakai engine + hash + voice_id_tag).
    - ElevenLabs random voice dibatasi pool (misal 3 voice_id).
      * random_video: pilih 1 voice untuk semua line di video
      * random_line : pilih random per line (lebih variatif, tone berubah2)
      * fixed       : pakai eleven_voice_id / env ELEVENLABS_VOICE_ID
    """
    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []

    engine = (engine or "gtts").strip().lower()
    if engine not in ("gtts", "edge", "elevenlabs"):
        raise ValueError(f"engine tidak dikenal: {engine}")

    rng = random.Random(seed)

    chosen_voice_video: Optional[str] = None
    pool: List[str] = []

    if engine == "elevenlabs":
        eleven_api_key = eleven_api_key or os.getenv("ELEVENLABS_API_KEY", "")
        if not eleven_api_key:
            raise ValueError("ELEVENLABS_API_KEY kosong. Set env ELEVENLABS_API_KEY.")

        if eleven_voice_mode == "fixed":
            eleven_voice_id = eleven_voice_id or os.getenv("ELEVENLABS_VOICE_ID", "")
            if not eleven_voice_id:
                raise ValueError("ELEVENLABS_VOICE_ID kosong. Set env ELEVENLABS_VOICE_ID atau isi eleven_voice_id.")
        else:
            pool = [v.strip() for v in (eleven_voice_pool or []) if v and v.strip()]
            if len(pool) < 1:
                raise ValueError("eleven_voice_pool kosong. Isi minimal 1 voice_id (misal 3 voice_id).")
            if eleven_voice_mode == "random_video":
                chosen_voice_video = rng.choice(pool)

    for i, line in enumerate(lines, 1):
        line = (line or "").strip()
        if not line:
            line = " "  # avoid empty

        # tentukan voice untuk line ini
        voice_id_used = ""
        if engine == "elevenlabs":
            if eleven_voice_mode == "fixed":
                voice_id_used = eleven_voice_id or ""
            elif eleven_voice_mode == "random_video":
                voice_id_used = chosen_voice_video or ""
            else:  # random_line
                # kalau seed diberikan, hasil tetap stabil per line
                if seed is not None:
                    line_rng = random.Random(seed + i * 99991)
                    voice_id_used = line_rng.choice(pool)
                else:
                    voice_id_used = rng.choice(pool)

        h = _sha1(line)
        slug = _safe_slug(line)

        # cache aman: include voice tag untuk elevenlabs
        voice_tag = ""
        if engine == "elevenlabs":
            voice_tag = f"_{voice_id_used[:8]}" if voice_id_used else ""

        name = (
            f"{prefix}_{engine}{voice_tag}_tts_{i:02d}_{slug}_{h}.mp3"
            if prefix
            else f"{engine}{voice_tag}_tts_{i:02d}_{slug}_{h}.mp3"
        )
        out_path = os.path.join(out_dir, name)

        if os.path.exists(out_path) and os.path.getsize(out_path) >= 5_000:
            paths.append(out_path)
            continue

        tmp_path = out_path + ".tmp"

        if engine == "gtts":
            _tts_gtts(line, tmp_path, lang=lang)
        elif engine == "edge":
             v = (edge_voice or os.getenv("EDGE_TTS_VOICE", "") or "id-ID-ArdiNeural").strip()
             _tts_edge(line, tmp_path, voice=v, rate=edge_rate)
        else:
            try:
                _tts_elevenlabs(
                    line,
                    tmp_path,
                    api_key=eleven_api_key or "",
                    voice_id=voice_id_used or "",
                    model_id=eleven_model_id,
                    stability=stability,
                    similarity_boost=similarity_boost,
                    style=style,
                    use_speaker_boost=use_speaker_boost,
                    output_format=output_format,
                )
            except RuntimeError as e:
                if str(e) == "ELEVENLABS_AUTH_ERROR":
                    print("[TTS][WARN] ElevenLabs API key invalid/unauthorized → fallback to gTTS", flush=True)
                    # fallback gtts
                    _tts_gtts(...)
                else:
                    raise

        os.replace(tmp_path, out_path)
        paths.append(out_path)

    return paths
