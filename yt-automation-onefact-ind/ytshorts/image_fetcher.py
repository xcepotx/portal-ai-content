
# ytshorts/image_fetcher.py
from __future__ import annotations

import os
import io
import json
import time
import hashlib
import random
import re
import shutil
from pathlib import Path
from collections import Counter
from typing import List, Tuple, Optional, Dict, Any
import requests
from PIL import Image, ImageDraw

UA = "Mozilla/5.0 (X11; Linux x86_64) yt-automation-onefact/1.0"

# Providers
PEXELS_API = "https://api.pexels.com/v1/search"
PIXABAY_API = "https://pixabay.com/api/"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

OK_MIMES = {"image/jpeg", "image/png", "image/webp"}
MIN_BYTES = 35_000
MAX_BYTES = 14_000_000

DISALLOW_EXT = {".svg"}


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _clean_query(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _derive_query_from_lines(lines: List[str], topic: str = "") -> str:
    """
    Derive query pencarian background dari content lines.
    Tujuan: query ringkas, relevan, tidak berisi stopword.
    """
    topic = (topic or "").strip().lower()

    # gabung beberapa baris awal (cukup untuk konteks)
    text = "\n".join([str(x) for x in (lines or []) if str(x).strip()])
    text = text.strip()
    if not text:
        return topic or "nature"

    head_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = " ".join(head_lines[:6])  # 6 baris awal
    head = re.sub(r"https?://\S+", " ", head)
    head = re.sub(r"[^A-Za-z0-9\u00C0-\u024F\u1E00-\u1EFF\s]", " ", head)
    head = re.sub(r"\s+", " ", head).strip().lower()

    stop = {
        # ID
        "yang","dan","di","ke","dari","ini","itu","atau","untuk","pada","dengan","jadi","karena",
        "kamu","anda","dia","mereka","kita","kami","aku","saya",
        "apa","bagaimana","mengapa","kenapa","kapan","dimana",
        # EN
        "the","and","to","of","in","on","for","with","is","are","was","were","a","an",
        # konten umum
        "fakta","fact","unik","cepat","tahukah","follow","subscribe","like","share",
    }
    if topic:
        stop.add(topic)

    toks = [t for t in head.split(" ") if len(t) >= 3 and t not in stop]
    if not toks:
        return topic or "nature"

    # ambil kata paling sering (maks 4 kata) → query ringkas
    top = [w for w, _ in Counter(toks).most_common(4)]
    q = " ".join(top).strip()

    return q or (topic or "nature")

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def _jsonl_append(path: Optional[str], obj: Dict[str, Any]) -> None:
    if not path:
        return
    _ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _is_valid_image_bytes(data: bytes) -> Tuple[bool, str]:
    if not data or len(data) < MIN_BYTES:
        return False, "too_small"
    head = data[:200].lower()
    if b"<html" in head or b"<!doctype html" in head:
        return False, "html_instead_of_image"
    try:
        im = Image.open(io.BytesIO(data))
        im.verify()
        return True, "ok"
    except Exception:
        return False, "pil_unidentified"


def _download_to_path(url: str, out_path: str, timeout: int = 25) -> Tuple[bool, str]:
    try:
        r = _session().get(url, timeout=timeout, stream=True)
        r.raise_for_status()

        cl = r.headers.get("Content-Length")
        if cl:
            try:
                if int(cl) > MAX_BYTES:
                    return False, "too_large"
            except Exception:
                pass

        data = r.content
        ok, reason = _is_valid_image_bytes(data)
        if not ok:
            return False, reason

        _ensure_dir(os.path.dirname(out_path))
        with open(out_path, "wb") as f:
            f.write(data)
        return True, "ok"
    except Exception as e:
        return False, f"download_error:{type(e).__name__}"


def _make_unique_gradient(out_path: str, w: int = 720, h: int = 1280, seed: str = "") -> str:
    _ensure_dir(os.path.dirname(out_path))
    rnd = random.Random(_sha1(seed))
    c1 = (rnd.randint(10, 80), rnd.randint(10, 80), rnd.randint(10, 80))
    c2 = (rnd.randint(120, 220), rnd.randint(120, 220), rnd.randint(120, 220))

    img = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    img.save(out_path, quality=92)
    return out_path


# ---------------------------
# PEXELS
# ---------------------------
def _pexels_search(query: str, per_page: int = 12) -> List[Dict[str, Any]]:
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return []

    q0 = _clean_query(query)
    if not q0:
        return []

    s = _session()
    s.headers.update({"Authorization": api_key})

    def _do(params: dict):
        try:
            r = s.get(PEXELS_API, params=params, timeout=25)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            return None

    # 1) attempt with preferred filters (kalau kamu tetap mau coba portrait dulu)
    params1 = {
        "query": q0,
        "per_page": min(int(per_page), 80),
        "orientation": "portrait",
        "size": "large",
    }
    data = _do(params1)

    photos = (data.get("photos") if isinstance(data, dict) else None) or []
    total = (data.get("total_results") if isinstance(data, dict) else None)

    # 2) fallback: retry WITHOUT orientation/size if empty
    if len(photos) == 0:
        params2 = {
            "query": q0,
            "per_page": min(int(per_page), 80),
            # no orientation, no size
        }
        data2 = _do(params2)
        photos2 = (data2.get("photos") if isinstance(data2, dict) else None) or []
        total2 = (data2.get("total_results") if isinstance(data2, dict) else None)

        if photos2:
            photos = photos2

    out = []
    for it in photos:
        src = it.get("src") or {}

        # pilih yang resolusinya tinggi (nanti kamu crop sendiri)
        url = (
            src.get("large2x")
            or src.get("large")
            or src.get("original")
            or src.get("portrait")
        )
        if not url:
            continue

        out.append({
            "source": "Pexels",
            "title": it.get("alt") or "Pexels Photo",
            "url": url,
            "page_url": it.get("url"),
            "author": it.get("photographer"),
            "author_url": it.get("photographer_url"),
            "license": "Pexels License",
            "mime": "image/jpeg",
        })

    random.shuffle(out)
    return out

def _pixabay_search(query: str, per_page: int = 12) -> List[Dict[str, Any]]:
    api_key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return []

    q0 = _clean_query(query)
    if not q0:
        return []

    params = {
        "key": api_key,
        "q": q0,
        "image_type": "photo",
        "safesearch": "true",
        "per_page": min(int(per_page), 200),
        # "orientation": "vertical",  # opsional; kalau mau lebih 9:16
    }

    try:
        s = _session()
        r = s.get(PIXABAY_API, params=params, timeout=25)
        if r.status_code != 200:
            return []
        data = r.json() or {}
    except Exception:
        return []

    hits = (data.get("hits") or [])
    out: List[Dict[str, Any]] = []
    for it in hits:
        url = (
            it.get("largeImageURL")
            or it.get("fullHDURL")
            or it.get("webformatURL")
        )
        if not url:
            continue

        out.append({
            "source": "Pixabay",
            "title": it.get("tags") or "Pixabay Photo",
            "url": url,
            "page_url": it.get("pageURL"),
            "author": it.get("user"),
            "author_url": None,
            "license": "Pixabay License",
            "mime": "image/jpeg",
        })

    random.shuffle(out)
    return out

# ---------------------------
# WIKIMEDIA
# ---------------------------
def _commons_search_images(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    def _simplify_commons_query(q: str) -> str:
        q = (q or "").lower().strip()
        # buang kata yg bikin Commons makin susah
        drop = {
            "automotif","automotive","realistic","photo","hd","4k",
            "background","portrait","highway","technology"
        }
        toks = re.findall(r"[a-z0-9]+", q)
        toks = [t for t in toks if t not in drop and len(t) > 2]
        # ambil beberapa token aja biar Commons dapet hasil
        toks = toks[:6]
        return " ".join(toks).strip() or q

    q = _clean_query(_simplify_commons_query(query))
    if not q:
        return []

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": q,
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
    }

    try:
        s = _session()
        r = s.get(COMMONS_API, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    pages = (data.get("query") or {}).get("pages") or {}
    results = []
    for _, p in pages.items():
        title = p.get("title")
        ii = (p.get("imageinfo") or [])
        if not ii:
            continue
        info = ii[0]
        url = info.get("url")
        mime = info.get("mime")
        size = info.get("size")
        ext = info.get("extmetadata") or {}

        if not url or not mime:
            continue
        if mime not in OK_MIMES:
            continue
        if isinstance(size, int) and size < MIN_BYTES:
            continue
        if title and any(title.lower().endswith(ex) for ex in DISALLOW_EXT):
            continue

        results.append({
            "source": "Wikimedia Commons",
            "title": title,
            "url": url,
            "mime": mime,
            "size": size,
            "extmetadata": ext,
        })

    random.shuffle(results)
    return results


# ---------------------------
# MAIN FETCH (TEMPLATE-DRIVEN)
# ---------------------------
def fetch_backgrounds_for_content(
    lines: List[str],
    topic: str,
    img_dir: str,
    attribution_path: Optional[str] = None,
    n: int = 3,
    content_id: Optional[str] = None,
    used_global: Optional[set] = None,
    query_hint: Optional[str] = None,
    bg: Optional[Dict[str, Any]] = None,   # v3
    manual_images=None
) -> Tuple[List[str], set]:
    """
    Priority: Pexels -> Wikimedia -> Gradient
    Unsplash disabled.
    """

    _ensure_dir(img_dir)
    if used_global is None:
        used_global = set()
    content_id = content_id or "content"

    # -----------------------
    # 0) MANUAL OVERRIDE
    # -----------------------

    if manual_images:
        if isinstance(manual_images, dict):
            sel = manual_images.get("selected") or []
            paths = [(it.get("local") or "").strip() for it in sel]
        else:
            paths = [str(p).strip() for p in (manual_images or [])]

        paths = [p for p in paths if p and os.path.exists(p)]
        if len(paths) < 1:
            raise ValueError("manual_images provided but no valid local paths found")

        # hapus cache lama untuk content ini
        for old in Path(img_dir).glob(f"bg_{content_id}_*.jpg"):
            try:
                old.unlink()
            except Exception:
                pass

        picked_paths = []
        for i in range(1, n + 1):
            src = paths[(i - 1) % len(paths)]
            out_path = os.path.join(img_dir, f"bg_{content_id}_{i:02d}.jpg")
            shutil.copyfile(src, out_path)
            picked_paths.append(out_path)
            used_global.add(out_path)

        return picked_paths, used_global


    # -----------------------
    # 1) build query list
    # -----------------------
    q_list: List[str] = []

    def _add_q(q: Optional[str]):
        q = _clean_query(q or "")
        if q and q not in q_list:
            q_list.append(q)

    # v3 primary
    if isinstance(bg, dict):
        _add_q(bg.get("query"))
        for v in (bg.get("variants") or []):
            _add_q(v)

    # v2 fallback
    if not q_list and query_hint:
        _add_q(query_hint)

    # derive fallback
    if not q_list:
        _add_q(_derive_query_from_lines(lines, topic))

    # hard anchor topic (optional)
    t = (topic or "").strip().lower()

    def _anchor_for_provider(topic_lc: str) -> str:
        # jangan pakai "automotif" sebagai keyword search
        if topic_lc == "automotif":
            return "automotive car"
        if topic_lc == "teknologi":
            return "technology"
        if topic_lc == "sains":
            return "science"
        return topic_lc

    if t:
        anchor = _anchor_for_provider(t)

        # kalau query sudah ada car/vehicle/automotive, jangan ditambah lagi
        def _needs_anchor(q: str) -> bool:
            ql = q.lower()
            return not any(x in ql for x in ["car", "vehicle", "automotive", "engine", "truck", "suv"])

        q_list = [q if not _needs_anchor(q) else f"{q} {anchor}" for q in q_list]

    # PEXELS biasanya lebih gampang kalau ada kata "car" utk automotif
    if (topic or "").lower() == "automotif":
        q_list2 = []
        for q in q_list:
            if "car" not in q.lower() and "vehicle" not in q.lower():
                q_list2.append(q + " car")
            q_list2.append(q)
        # uniq preserve order
        seen = set()
        q_list = []
        for q in q_list2:
            if q.lower() in seen:
                continue
            seen.add(q.lower())
            q_list.append(q)


    # -----------------------
    # 2) safety filters
    # -----------------------
    avoid_terms = []
    if isinstance(bg, dict):
        avoid_terms = [str(x).lower() for x in (bg.get("avoid") or []) if str(x).strip()]

    def is_safe_candidate(cand: Dict[str, Any]) -> bool:
        title = (cand.get("title") or "").lower()
        banned = ["portrait", "selfie", "model", "fashion", "singer", "idol"]
        banned += avoid_terms
        return not any(b in title for b in banned)

    picked_paths: List[str] = []
    picked_meta: List[Dict[str, Any]] = []

    def try_candidates(q: str, candidates: List[Dict[str, Any]], provider: str) -> None:
        nonlocal picked_paths, picked_meta, used_global

        for cand in candidates:
            if len(picked_paths) >= n:
                return

            if not is_safe_candidate(cand):
                continue

            url = cand.get("url")
            if not url or url in used_global:
                continue

            k = len(picked_paths) + 1
            out_path = os.path.join(img_dir, f"bg_{content_id}_{k:02d}.jpg")

            # cached file exists -> accept
            if os.path.exists(out_path) and os.path.getsize(out_path) >= MIN_BYTES:
                used_global.add(url)
                used_global.add(out_path)
                picked_paths.append(out_path)
                picked_meta.append({
                    "topic": topic,
                    "query": q,
                    "cached": True,
                    "fallback": False,
                    "provider": provider,
                    **cand,
                    "path": out_path,
                })
                continue

            ok, reason = _download_to_path(url, out_path)
            if not ok:
                picked_meta.append({
                    "topic": topic,
                    "query": q,
                    "cached": False,
                    "fallback": True,
                    "provider": provider,
                    "source": cand.get("source"),
                    "title": cand.get("title"),
                    "url": url,
                    "mime": cand.get("mime"),
                    "note": "download_failed",
                    "error": reason,
                    "path": None,
                })
                continue

            used_global.add(url)
            used_global.add(out_path)
            picked_paths.append(out_path)
            picked_meta.append({
                "topic": topic,
                "query": q,
                "cached": False,
                "fallback": False,
                "provider": provider,
                **cand,
                "path": out_path,
            })

    # -----------------------
    # 3) PROVIDERS
    # -----------------------

    # A) Provider choice: Pexels / Pixabay / Both (via ENV)
    pref = (os.getenv("YTA_BG_SOURCE") or "pexels").strip().lower()
    providers = ["pexels", "pixabay"]

    if pref == "pexels":
        providers = ["pexels", "pixabay"]
    elif pref == "pixabay":
        providers = ["pixabay", "pexels"]
    else:
        # both: random order tiap content biar mix
        random.shuffle(providers)

    for q in q_list:
        if len(picked_paths) >= n:
            break

        for prov in providers:
            if len(picked_paths) >= n:
                break

            if prov == "pexels":
                cands = _pexels_search(q, per_page=30)
                if cands:
                    try_candidates(q, cands, provider="pexels")

            elif prov == "pixabay":
                cands = _pixabay_search(q, per_page=30)
                if cands:
                    try_candidates(q, cands, provider="pixabay")

        time.sleep(0.4)

    # B) WIKIMEDIA fallback (2-pass query: raw + simplified)
    def _commons_search_images_2pass(query: str, limit: int = 30) -> List[Dict[str, Any]]:
        raw_q = _clean_query(query)
        if not raw_q:
            return []

        def _simplify_commons_query(q: str) -> str:
            q = (q or "").lower().strip()
            drop = {"automotif","automotive","realistic","photo","hd","4k","background","technology","highway"}
            toks = re.findall(r"[a-z0-9]+", q)
            toks = [t for t in toks if t not in drop and len(t) > 2]
            toks = toks[:8]
            return " ".join(toks).strip() or q

        simp_q = _clean_query(_simplify_commons_query(raw_q))

        out: List[Dict[str, Any]] = []
        seen = set()
        for qq in [raw_q, simp_q]:
            for it in _commons_search_images(qq, limit=limit):
                u = it.get("url")
                if not u or u in seen:
                    continue
                seen.add(u)
                out.append(it)
        random.shuffle(out)
        return out

    for q in q_list:
        if len(picked_paths) >= n:
            break
        cands = _commons_search_images_2pass(q, limit=40)
        if cands:
            try_candidates(q, cands, provider="commons")
        time.sleep(0.12)

    # C) GRADIENT fallback
    while len(picked_paths) < n:
        k = len(picked_paths) + 1
        grad_path = os.path.join(img_dir, f"bg_{content_id}_gradient_{k:02d}.jpg")
        if grad_path in used_global:
            grad_path = os.path.join(img_dir, f"bg_{content_id}_gradient_{k:02d}_{_sha1(content_id+str(k))}.jpg")

        _make_unique_gradient(grad_path, w=720, h=1280, seed=f"{content_id}:{k}:{q_list[0] if q_list else ''}")
        used_global.add(grad_path)
        picked_paths.append(grad_path)

        _jsonl_append(attribution_path, {
            "topic": topic,
            "query": (q_list[0] if q_list else ""),
            "cached": False,
            "fallback": True,
            "source": "gradient",
            "title": None,
            "url": None,
            "mime": "image/jpeg",
            "note": "fallback_gradient",
            "error": None,
            "path": grad_path,
        })

    # attribution logs
    for m in picked_meta:
        if m.get("path") and m["path"] in picked_paths:
            _jsonl_append(attribution_path, m)

    return picked_paths, used_global
