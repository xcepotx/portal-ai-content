import os
import re
import hashlib
from typing import List, Optional, Literal

import requests
from gtts import gTTS

TTSEngine = Literal["gtts", "elevenlabs"]


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _safe_slug(s: str, max_len: int = 32) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if s else "seg"


def _tts_gtts(text: str, out_path: str, lang: str = "id") -> None:
    tts = gTTS(text=text, lang=lang, slow=False)
    tts.save(out_path)


def _tts_elevenlabs(
    text: str,
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

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={output_format}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": float(stability),
            "similarity_boost": float(similarity_boost),
            "style": float(style),
            "use_speaker_boost": bool(use_speaker_boost),
        },
    }

    r = requests.post(url, headers=headers, json=payload, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:400]}")

    with open(out_path, "wb") as f:
        f.write(r.content)


def make_tts_segments(
    segments_text: List[str],
    out_dir: str,
    *,
    lang: str = "id",
    prefix: str = "",
    engine: TTSEngine = "gtts",
    eleven_api_key: Optional[str] = None,
    eleven_voice_id: Optional[str] = None,
    eleven_model_id: str = "eleven_multilingual_v2",
    stability: float = 0.45,
    similarity_boost: float = 0.80,
    style: float = 0.10,
    use_speaker_boost: bool = True,
    output_format: str = "mp3_44100_128",
) -> List[str]:
    """
    Long TTS:
    - 1 file per segment (bukan per-line short)
    - cache by hash of segment + voice
    """
    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []

    engine = (engine or "gtts").strip().lower()
    if engine not in ("gtts", "elevenlabs"):
        raise ValueError(f"engine tidak dikenal: {engine}")

    if engine == "elevenlabs":
        eleven_api_key = eleven_api_key or os.getenv("ELEVENLABS_API_KEY", "")
        if not eleven_api_key:
            raise ValueError("ELEVENLABS_API_KEY kosong. Set env ELEVENLABS_API_KEY.")
        eleven_voice_id = eleven_voice_id or os.getenv("ELEVENLABS_VOICE_ID", "")
        if not eleven_voice_id:
            raise ValueError("ELEVENLABS_VOICE_ID kosong. Set env ELEVENLABS_VOICE_ID atau isi eleven_voice_id.")

    voice_tag = ""
    if engine == "elevenlabs":
        voice_tag = f"_{(eleven_voice_id or '')[:8]}"

    for i, text in enumerate(segments_text, start=1):
        text = (text or "").strip()
        if not text:
            text = " "

        h = _sha1(text)
        slug = _safe_slug(text)

        name = (
            f"{prefix}_long_{engine}{voice_tag}_seg_{i:02d}_{slug}_{h}.mp3"
            if prefix
            else f"long_{engine}{voice_tag}_seg_{i:02d}_{slug}_{h}.mp3"
        )
        out_path = os.path.join(out_dir, name)

        if os.path.exists(out_path) and os.path.getsize(out_path) >= 5_000:
            paths.append(out_path)
            continue

        tmp_path = out_path + ".tmp"

        if engine == "gtts":
            _tts_gtts(text, tmp_path, lang=lang)
        else:
            _tts_elevenlabs(
                text,
                tmp_path,
                api_key=eleven_api_key or "",
                voice_id=eleven_voice_id or "",
                model_id=eleven_model_id,
                stability=stability,
                similarity_boost=similarity_boost,
                style=style,
                use_speaker_boost=use_speaker_boost,
                output_format=output_format,
            )

        os.replace(tmp_path, out_path)
        paths.append(out_path)

    return paths
