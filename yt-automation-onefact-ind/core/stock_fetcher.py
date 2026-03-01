import os
import random
import time
import json
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

import requests


@dataclass
class StockClip:
    provider: str               # "pexels" | "pixabay"
    id: str
    url: str                    # direct downloadable video url (mp4)
    width: int
    height: int
    duration: float             # seconds (may be missing for some sources; best-effort)
    preview_url: Optional[str] = None


class StockFetchError(RuntimeError):
    pass


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_get(d: Dict[str, Any], keys: List[str], default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "ContentGenerator/1.0"})
    return s


def _pick_best_pexels_file(video_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Prefer:
    - mp4
    - reasonably high resolution but not extreme
    - returns dict with link, width, height
    """
    if not video_files:
        return None

    # Sort by "quality" preference
    def score(vf: Dict[str, Any]) -> Tuple[int, int]:
        w = int(vf.get("width") or 0)
        h = int(vf.get("height") or 0)
        # prefer larger area, but cap effect by penalizing ultra large
        area = w * h
        penalty = 0
        if area >= 3840 * 2160:  # 4K
            penalty = 10_000_000
        return (area - penalty, -abs(w - 1080))  # mild preference toward ~1080-ish widths

    candidates = [vf for vf in video_files if isinstance(vf, dict) and vf.get("link")]
    if not candidates:
        return None
    candidates.sort(key=score, reverse=True)
    return candidates[0]


def search_pexels_videos(
    query: str,
    per_page: int = 30,
    page: int = 1,
    orientation: Optional[str] = None,  # "portrait"|"landscape"|"square"|None
    min_duration: Optional[int] = None,
    max_duration: Optional[int] = None,
    seed: Optional[int] = None,
) -> List[StockClip]:
    api_key = (os.getenv("PEXELS_API_KEY") or "").strip()
    if not api_key:
        raise StockFetchError("PEXELS_API_KEY env var belum diset.")

    s = _requests_session()
    s.headers.update({"Authorization": api_key})

    params = {"query": query, "per_page": per_page, "page": page}
    if orientation:
        params["orientation"] = orientation
    if min_duration:
        params["min_duration"] = int(min_duration)
    if max_duration:
        params["max_duration"] = int(max_duration)

    url = "https://api.pexels.com/videos/search"
    r = s.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise StockFetchError(f"Pexels API error {r.status_code}: {r.text[:2000]}")

    data = r.json()
    videos = data.get("videos") or []
    out: List[StockClip] = []

    for v in videos:
        vid = str(v.get("id", ""))
        duration = float(v.get("duration") or 0.0)
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
        vf = _pick_best_pexels_file(v.get("video_files") or [])
        if not vf:
            continue
        link = vf.get("link")
        fw = int(vf.get("width") or w or 0)
        fh = int(vf.get("height") or h or 0)
        preview = _safe_get(v, ["video_pictures", 0, "picture"])
        out.append(
            StockClip(
                provider="pexels",
                id=vid,
                url=link,
                width=fw,
                height=fh,
                duration=duration,
                preview_url=preview,
            )
        )

    if seed is not None:
        random.Random(seed).shuffle(out)
    return out


def search_pixabay_videos(
    query: str,
    per_page: int = 50,
    page: int = 1,
    seed: Optional[int] = None,
) -> List[StockClip]:
    api_key = (os.getenv("PIXABAY_API_KEY") or "").strip()
    if not api_key:
        raise StockFetchError("PIXABAY_API_KEY env var belum diset.")

    s = _requests_session()

    params = {
        "key": api_key,
        "q": query,
        "per_page": per_page,
        "page": page,
        "video_type": "all",
        "safesearch": "true",
    }
    url = "https://pixabay.com/api/videos/"
    r = s.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise StockFetchError(f"Pixabay API error {r.status_code}: {r.text[:2000]}")

    data = r.json()
    hits = data.get("hits") or []
    out: List[StockClip] = []

    # Pixabay returns multiple sizes; pick "large" else "medium" else any.
    pref_keys = ["large", "medium", "small", "tiny"]
    for h in hits:
        vid = str(h.get("id", ""))
        videos = h.get("videos") or {}
        chosen = None
        for k in pref_keys:
            if k in videos and isinstance(videos[k], dict) and videos[k].get("url"):
                chosen = videos[k]
                break
        if not chosen:
            continue
        url_mp4 = chosen.get("url")
        w = int(chosen.get("width") or 0)
        hh = int(chosen.get("height") or 0)
        duration = float(h.get("duration") or 0.0)
        preview = h.get("picture_id")
        preview_url = None
        if preview:
            # not always stable, but can be used for UI preview if needed
            preview_url = f"https://i.vimeocdn.com/video/{preview}_640x360.jpg"
        out.append(
            StockClip(
                provider="pixabay",
                id=vid,
                url=url_mp4,
                width=w,
                height=hh,
                duration=duration,
                preview_url=preview_url,
            )
        )

    if seed is not None:
        random.Random(seed).shuffle(out)
    return out


def download_url_to_file(url: str, out_path: Path, timeout: int = 120) -> None:
    _ensure_dir(out_path.parent)
    s = _requests_session()
    with s.get(url, stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise StockFetchError(f"Gagal download {url} (HTTP {r.status_code})")
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
        tmp.replace(out_path)


def fetch_random_clips(
    query: str,
    source: str,               # "pexels" | "pixabay" | "both"
    clip_count: int,
    seed: Optional[int] = None,
    pexels_orientation: Optional[str] = None,
    min_duration: Optional[int] = None,
    max_duration: Optional[int] = None,
) -> List[StockClip]:
    """
    Fetch candidates from provider(s), then sample random clips.
    Graceful fallback:
    - if selected source returns empty, try the other (when source == both OR fallback).
    """
    clip_count = max(1, int(clip_count))

    rng = random.Random(seed) if seed is not None else random.Random()

    candidates: List[StockClip] = []
    errors: List[str] = []

    def try_pexels():
        nonlocal candidates
        try:
            res = search_pexels_videos(
                query=query,
                per_page=40,
                page=1,
                orientation=pexels_orientation,
                min_duration=min_duration,
                max_duration=max_duration,
                seed=seed,
            )
            candidates.extend(res)
        except Exception as e:
            errors.append(f"Pexels: {e}")

    def try_pixabay():
        nonlocal candidates
        try:
            res = search_pixabay_videos(query=query, per_page=80, page=1, seed=seed)
            candidates.extend(res)
        except Exception as e:
            errors.append(f"Pixabay: {e}")

    src = source.lower().strip()
    if src not in ("pexels", "pixabay", "both"):
        raise StockFetchError("source harus: pexels | pixabay | both")

    if src == "pexels":
        try_pexels()
        if not candidates:  # fallback
            try_pixabay()
    elif src == "pixabay":
        try_pixabay()
        if not candidates:  # fallback
            try_pexels()
    else:
        # both: pull from both then random sample
        try_pexels()
        try_pixabay()

    # remove duplicates by (provider,id)
    uniq: Dict[Tuple[str, str], StockClip] = {}
    for c in candidates:
        if c.id:
            uniq[(c.provider, c.id)] = c
    candidates = list(uniq.values())

    if not candidates:
        msg = "Tidak ada video ditemukan dari provider. "
        if errors:
            msg += " | ".join(errors)
        raise StockFetchError(msg)

    # -----------------------------
    # ✅ keep relevance: sample from top results
    # -----------------------------
    def _dur_ok(c: StockClip) -> bool:
        d = getattr(c, "duration", None)
        if d is None:
            return True
        try:
            d = float(d)
        except Exception:
            return True
        if min_duration is not None and d < float(min_duration):
            return False
        if max_duration is not None and d > float(max_duration):
            return False
        return True

    candidates = [c for c in candidates if _dur_ok(c)]
    if not candidates:
        raise StockFetchError("Video ada, tapi semuanya tidak lolos filter durasi.")

    # biasanya API return sorted by relevance (page=1)
    TOP_K = min(40, len(candidates))  # bisa Anda tweak: 20/30/40
    top = candidates[:TOP_K]
    rest = candidates[TOP_K:]

    need = min(clip_count, len(candidates))

    # random dari top-k (tetap relevan)
    if len(top) <= need:
        picked = top[:]
        # kalau top kurang, tambah dari rest sedikit
        if len(picked) < need and rest:
            rng.shuffle(rest)
            picked += rest[: (need - len(picked))]
        return picked

    picked = rng.sample(top, k=need)
    return picked


def download_clips(
    clips: List[StockClip],
    out_dir: Path,
    prefix: str = "clip",
) -> List[Path]:
    """
    Download direct mp4 urls to local files.
    """
    _ensure_dir(out_dir)
    out_paths: List[Path] = []
    for i, c in enumerate(clips, start=1):
        fname = f"{prefix}_{i:02d}_{c.provider}_{c.id}.mp4"
        p = out_dir / fname
        if not p.exists() or p.stat().st_size < 100_000:
            download_url_to_file(c.url, p)
        out_paths.append(p)
    return out_paths
