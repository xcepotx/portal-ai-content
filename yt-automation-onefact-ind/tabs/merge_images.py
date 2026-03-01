from __future__ import annotations

import os
import re
import json
import html
import time
import sys
import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import requests

from core.job_store import JobStore  # ✅ background jobs (muncul di Jobs List)

TAB_KEY = "smr"  # keep prefix compatible

PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"
GLOBAL_PROFILE_NAME = "__global__"


# =========================
# Path helpers (ctx-safe)
# =========================
def _coerce_path(v) -> Path | None:
    if v is None:
        return None
    if isinstance(v, Path):
        return v.expanduser().resolve()
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        return Path(s).expanduser().resolve()
    try:
        s = str(v).strip()
        if not s:
            return None
        return Path(s).expanduser().resolve()
    except Exception:
        return None


def _legacy_root() -> Path:
    # yt-automation-onefact-ind/
    return Path(__file__).resolve().parents[1]


def _ctx_paths(ctx) -> dict:
    return (ctx.get("paths") or {}) if isinstance(ctx, dict) else {}


def _ctx_profile(ctx) -> dict:
    return (ctx.get("profile") or {}) if isinstance(ctx, dict) else {}


def _ctx_global_profile(ctx) -> dict:
    return (ctx.get("global_profile") or {}) if isinstance(ctx, dict) else {}


def _ctx_api_keys(ctx) -> dict:
    return (ctx.get("api_keys") or {}) if isinstance(ctx, dict) else {}


def _ctx_get_username(ctx) -> str:
    if not isinstance(ctx, dict):
        return ""
    return (ctx.get("auth_user") or ctx.get("user") or ctx.get("username") or "").strip()


def _role_from_ctx(ctx) -> str:
    r = ""
    if isinstance(ctx, dict):
        r = str(ctx.get("auth_role") or "").strip()
    if not r:
        r = str(st.session_state.get("auth_role") or "").strip()
    return r.lower()


def _ws_root(ctx, legacy_root: Path) -> Path:
    p = _coerce_path(_ctx_paths(ctx).get("user_root"))
    if p:
        # ensure baseline dirs
        (p / "contents").mkdir(parents=True, exist_ok=True)
        (p / "logs").mkdir(parents=True, exist_ok=True)
        (p / "manifests").mkdir(parents=True, exist_ok=True)
        (p / "uploads").mkdir(parents=True, exist_ok=True)
        (p / "out").mkdir(parents=True, exist_ok=True)
        (p / "jobs").mkdir(parents=True, exist_ok=True)
        return p
    return legacy_root


def _ws_dirs(ctx, ws_root: Path) -> dict[str, Path]:
    paths = _ctx_paths(ctx)
    logs = _coerce_path(paths.get("logs")) or (ws_root / "logs")
    manifests = _coerce_path(paths.get("manifests")) or (ws_root / "manifests")
    contents = _coerce_path(paths.get("contents")) or (ws_root / "contents")
    out = _coerce_path(paths.get("out")) or (ws_root / "out")
    uploads = (ws_root / "uploads")

    for p in [logs, manifests, contents, out, uploads]:
        p.mkdir(parents=True, exist_ok=True)

    return {
        "logs": logs.resolve(),
        "manifests": manifests.resolve(),
        "contents": contents.resolve(),
        "out": out.resolve(),
        "uploads": uploads.resolve(),
    }


def _inject_env_keys(ctx) -> None:
    keys = _ctx_api_keys(ctx)
    if not keys:
        return
    if (keys.get("pexels") or "").strip():
        os.environ["PEXELS_API_KEY"] = (keys.get("pexels") or "").strip()
    if (keys.get("pixabay") or "").strip():
        os.environ["PIXABAY_API_KEY"] = (keys.get("pixabay") or "").strip()
    if (keys.get("elevenlabs") or "").strip():
        os.environ["ELEVENLABS_API_KEY"] = (keys.get("elevenlabs") or "").strip()
    if (keys.get("gemini") or "").strip():
        os.environ["GEMINI_API_KEY"] = (keys.get("gemini") or "").strip()
        os.environ["GOOGLE_API_KEY"] = (keys.get("gemini") or "").strip()


def _list_avatar_ids(legacy_root: Path) -> list[str]:
    avatars_dir = (legacy_root / "assets" / "avatars").resolve()
    if not avatars_dir.exists():
        return []
    ids = [p.name for p in avatars_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    ids.sort(key=lambda x: x.lower())
    return ids


# =========================
# Profile utilities
# =========================
def _parse_csv(s: str) -> list[str]:
    s = (s or "").replace("\n", ",")
    return [x.strip() for x in s.split(",") if x.strip()]


def _opacity_to_255(v, default: int = 120) -> int:
    try:
        f = float(v)
        if 0.0 <= f <= 1.0:
            f *= 255.0
        i = int(round(f))
        return max(0, min(255, i))
    except Exception:
        return max(0, min(255, int(default)))


def _edge_voice_pool(ctx, global_prof: dict) -> list[str]:
    # ctx render pool (if exists)
    try:
        if isinstance(ctx, dict):
            pool = ((ctx.get("render") or {}).get("edge_voice_pool")) or []
            if isinstance(pool, list) and pool:
                out = [str(x).strip() for x in pool if str(x).strip()]
                if out:
                    return out
    except Exception:
        pass

    g_rd = (global_prof.get("render_defaults") or {}) if isinstance(global_prof, dict) else {}
    csv = (g_rd.get("edge_voice_pool_csv") or "").strip()
    if csv:
        out = _parse_csv(csv)
        if out:
            return out

    csv = (os.getenv("EDGE_VOICE_POOL_CSV") or "").strip()
    if csv:
        out = _parse_csv(csv)
        if out:
            return out

    return ["id-ID-ArdiNeural", "id-ID-GadisNeural", "en-US-GuyNeural", "en-US-JennyNeural"]


# =========================
# Content listing
# =========================
def _list_topics(contents_root: Path) -> list[str]:
    if not contents_root.exists():
        return []
    topics = []
    for p in contents_root.iterdir():
        if p.is_dir() and p.name.lower() != "generated":
            topics.append(p.name)
    topics.sort(key=lambda x: x.lower())
    return topics


def _list_txt_files(topic_dir: Path) -> list[Path]:
    if not topic_dir.exists():
        return []
    return sorted([p for p in topic_dir.glob("*.txt") if p.is_file()], key=lambda p: p.name.lower())


def _preview_txt(path: Path, max_lines: int = 10) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        head = lines[:max_lines]
        return "\n".join(head) if head else "(file kosong)"
    except Exception as e:
        return f"(gagal baca file: {e})"


# =========================
# Pexels downloader
# =========================
@dataclass
class PexelsPhoto:
    id: int
    photographer: str
    url: str
    src_original: str
    src_large: str


class PexelsDownloader:
    def __init__(self, api_key: str, download_dir: Path):
        self.api_key = (api_key or "").strip()
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def search_photos(self, query: str, per_page: int = 12, orientation: str = "portrait", size: str = "large") -> list[PexelsPhoto]:
        if not self.api_key:
            return []
        headers = {"Authorization": self.api_key, "Accept": "application/json"}
        params = {"query": query, "per_page": int(per_page), "orientation": orientation, "size": size}
        r = requests.get(PEXELS_PHOTO_API, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        photos: list[PexelsPhoto] = []
        for p in data.get("photos", []):
            src = p.get("src") or {}
            photos.append(
                PexelsPhoto(
                    id=int(p.get("id")),
                    photographer=str(p.get("photographer") or ""),
                    url=str(p.get("url") or ""),
                    src_original=str(src.get("original") or ""),
                    src_large=str(src.get("large") or src.get("large2x") or src.get("original") or ""),
                )
            )
        return photos

    def _safe_name(self, url: str) -> str:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        ext = ".jpg"
        m = re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url.lower())
        if m:
            ext = "." + m.group(1)
        return f"pexels_{h}{ext}"

    def download(self, photo_url: str) -> Path:
        if not self.api_key:
            raise RuntimeError("PEXELS_API_KEY kosong")

        out = self.download_dir / self._safe_name(photo_url)
        if out.exists() and out.stat().st_size > 0:
            return out

        r = requests.get(photo_url, stream=True, timeout=30)
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 128):
                if chunk:
                    f.write(chunk)
        return out


# =========================
# Manual manifest
# =========================
@dataclass
class ManualManifest:
    topic: str
    source_txt_path: str
    images: list[str]
    render_options: dict[str, Any]
    created_at: str
    version: str = "manual_manifest_v1"


class ManifestBuilder:
    def __init__(self, manifest_dir: Path):
        self.manifest_dir = Path(manifest_dir)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        topic: str,
        source_txt_path: Path,
        images: list[Path],
        render_options: dict[str, Any],
        created_at: str,
        slug: str,
    ) -> Path:
        mf = ManualManifest(
            topic=topic,
            source_txt_path=str(Path(source_txt_path).resolve()),
            images=[str(Path(p).resolve()) for p in images],
            render_options=render_options,
            created_at=created_at,
        )
        safe_slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in slug)[:60] or "manifest"
        out_path = (self.manifest_dir / f"manual_{created_at}_{safe_slug}.json").resolve()
        out_path.write_text(json.dumps(asdict(mf), ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path


# =========================
# Image selection state
# =========================
def _init_state():
    st.session_state.setdefault(f"{TAB_KEY}_images", [])  # list[str abs]
    st.session_state.setdefault(f"{TAB_KEY}_pexels_results", [])
    st.session_state.setdefault(f"{TAB_KEY}_pexels_downloaded", [])
    st.session_state.setdefault(f"{TAB_KEY}_local_uploaded", [])
    st.session_state.setdefault(f"{TAB_KEY}_manifest_path", None)

    # background job state
    st.session_state.setdefault(f"{TAB_KEY}_last_job_id", None)
    st.session_state.setdefault(f"{TAB_KEY}_last_output", None)
    st.session_state.setdefault(f"{TAB_KEY}_cleaned_for_job", None)


def _images_as_paths() -> list[Path]:
    out: list[Path] = []
    for p in (st.session_state.get(f"{TAB_KEY}_images") or []):
        pp = Path(p)
        if pp.exists():
            out.append(pp)
    return out


def _add_image_path(p: Path):
    imgs: list[str] = st.session_state.get(f"{TAB_KEY}_images") or []
    if len(imgs) >= 5:
        return
    rp = str(p.resolve())
    if rp in imgs:
        return
    imgs.append(rp)
    st.session_state[f"{TAB_KEY}_images"] = imgs


def _remove_image(idx: int):
    imgs: list[str] = st.session_state.get(f"{TAB_KEY}_images") or []
    if 0 <= idx < len(imgs):
        imgs.pop(idx)
    st.session_state[f"{TAB_KEY}_images"] = imgs


def _move_image(idx: int, direction: int):
    imgs: list[str] = st.session_state.get(f"{TAB_KEY}_images") or []
    j = idx + direction
    if 0 <= idx < len(imgs) and 0 <= j < len(imgs):
        imgs[idx], imgs[j] = imgs[j], imgs[idx]
    st.session_state[f"{TAB_KEY}_images"] = imgs


def _cleanup_run_files(reset_pexels_query: bool = True):
    for p in (st.session_state.get(f"{TAB_KEY}_pexels_downloaded") or []):
        try:
            pp = Path(p)
            if pp.exists():
                pp.unlink()
        except Exception:
            pass
    st.session_state[f"{TAB_KEY}_pexels_downloaded"] = []

    for p in (st.session_state.get(f"{TAB_KEY}_local_uploaded") or []):
        try:
            pp = Path(p)
            if pp.exists():
                pp.unlink()
        except Exception:
            pass
    st.session_state[f"{TAB_KEY}_local_uploaded"] = []

    st.session_state[f"{TAB_KEY}_pexels_results"] = []
    st.session_state[f"{TAB_KEY}_images"] = []
    st.session_state[f"{TAB_KEY}_manifest_path"] = None

    if reset_pexels_query:
        for k in [f"{TAB_KEY}_pexels_q", f"{TAB_KEY}_pexels_n", f"{TAB_KEY}_pexels_ori", f"{TAB_KEY}_pexels_size"]:
            st.session_state.pop(k, None)

def _clear_selected_images_only():
    # hapus pilihan saja, tidak menghapus cache search
    st.session_state[f"{TAB_KEY}_images"] = []

def _clear_pexels_search(reset_query: bool = True):
    # hapus hasil search + downloaded list (opsional reset query ui)
    st.session_state[f"{TAB_KEY}_pexels_results"] = []
    st.session_state[f"{TAB_KEY}_pexels_downloaded"] = []
    if reset_query:
        for k in [f"{TAB_KEY}_pexels_q", f"{TAB_KEY}_pexels_n", f"{TAB_KEY}_pexels_ori", f"{TAB_KEY}_pexels_size"]:
            st.session_state.pop(k, None)

# =========================
# UI blocks
# =========================
def _render_selected_images():
    st.caption("✅ Selected Images")
    imgs = st.session_state.get(f"{TAB_KEY}_images") or []
    if not imgs:
        st.info("Belum ada image dipilih.")
        return

    # grid 5 items max: 5 kolom biar ringkas
    cols = st.columns(5)
    for i, pstr in enumerate(imgs):
        p = Path(pstr)
        with cols[i % 5]:
            if p.exists():
                st.image(str(p), use_container_width=True)
            st.caption(f"#{i+1} • {p.name}")  # ✅ nama file saja

            c1, c2, c3 = st.columns(3)
            with c1:
                st.button("▲", key=f"{TAB_KEY}_up_{i}", on_click=_move_image, args=(i, -1), disabled=(i == 0), use_container_width=True)
            with c2:
                st.button("▼", key=f"{TAB_KEY}_down_{i}", on_click=_move_image, args=(i, 1), disabled=(i == len(imgs)-1), use_container_width=True)
            with c3:
                st.button("🗑️", key=f"{TAB_KEY}_del_{i}", on_click=_remove_image, args=(i,), use_container_width=True)

def _render_local_uploader(upload_dir: Path):
    st.markdown("### ⬆️ Upload Image Lokal (opsional)")
    files = st.file_uploader(
        "Upload image (jpg/png/webp) - maksimal 5 total terpilih",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key=f"{TAB_KEY}_local_upload",
    )
    if not files:
        return

    for uf in files:
        if len(st.session_state.get(f"{TAB_KEY}_images") or []) >= 5:
            st.warning("Sudah 5 images, upload berikutnya diabaikan.")
            break

        out = upload_dir / uf.name
        if out.exists():
            out = upload_dir / f"{out.stem}_{int(time.time())}{out.suffix}"

        out.write_bytes(uf.getbuffer())

        ul = st.session_state.get(f"{TAB_KEY}_local_uploaded") or []
        rp = str(out.resolve())
        if rp not in ul:
            ul.append(rp)
        st.session_state[f"{TAB_KEY}_local_uploaded"] = ul

        _add_image_path(out)

    st.success("Upload selesai.")


def _render_pexels_picker(content_text: str, api_key: str, upload_dir: Path):
    st.caption("🔎 Pexels Search")
    if not api_key:
        st.warning("PEXELS API key kosong. Set global key di My Profile (admin) atau env PEXELS_API_KEY.")
        return

    default_q = content_text.strip().split("\n")[0][:80] if content_text.strip() else ""
    q = st.text_input("Keyword Pexels", value=st.session_state.get(f"{TAB_KEY}_pexels_q", default_q), key=f"{TAB_KEY}_pexels_q")

    per_page = st.slider("Jumlah hasil", 6, 20, int(st.session_state.get(f"{TAB_KEY}_pexels_n", 12)), key=f"{TAB_KEY}_pexels_n")

    c1, c2 = st.columns(2)
    with c1:
        orientation = st.selectbox(
            "Orientation",
            ["portrait", "landscape", "square"],
            index=["portrait", "landscape", "square"].index(st.session_state.get(f"{TAB_KEY}_pexels_ori", "portrait")),
            key=f"{TAB_KEY}_pexels_ori",
        )
    with c2:
        size = st.selectbox(
            "Size",
            ["large", "medium", "small"],
            index=["large", "medium", "small"].index(st.session_state.get(f"{TAB_KEY}_pexels_size", "large")),
            key=f"{TAB_KEY}_pexels_size",
        )

    if st.button("🔍 Search", key=f"{TAB_KEY}_pexels_search", use_container_width=True):
        try:
            dl = PexelsDownloader(api_key=api_key, download_dir=upload_dir)
            res = dl.search_photos(q, per_page=per_page, orientation=orientation, size=size)
            st.session_state[f"{TAB_KEY}_pexels_results"] = res
        except Exception as e:
            st.error(f"Gagal search Pexels: {e}")

    results = st.session_state.get(f"{TAB_KEY}_pexels_results") or []
    if not results:
        st.caption("Belum ada hasil Pexels.")
        return

    st.caption("Klik Download untuk memilih (maks 5).")
    dl = PexelsDownloader(api_key=api_key, download_dir=upload_dir)

    cols = st.columns(3)
    for idx, photo in enumerate(results):
        c = cols[idx % 3]
        with c:
            st.image(photo.src_large, caption=f"ID {photo.id} • {photo.photographer}", use_container_width=True)
            if st.button("Download + Pilih", key=f"{TAB_KEY}_px_dl_{photo.id}"):
                if len(st.session_state.get(f"{TAB_KEY}_images") or []) >= 5:
                    st.warning("Sudah 5 images. Hapus salah satu dulu.")
                else:
                    try:
                        p = dl.download(photo.src_large or photo.src_original)
                        _add_image_path(p)
                        dl_list = st.session_state.get(f"{TAB_KEY}_pexels_downloaded") or []
                        rp = str(p.resolve())
                        if rp not in dl_list:
                            dl_list.append(rp)
                        st.session_state[f"{TAB_KEY}_pexels_downloaded"] = dl_list
                        st.success(f"Downloaded: {p.name}")
                    except Exception as e:
                        st.error(f"Gagal download: {e}")


def _render_options_form(rd: dict, edge_pool: list[str], has_eleven: bool, avatar_ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    # -------------------
    # TTS
    # -------------------
    with st.expander("🗣️ TTS Settings", expanded=False):
        tts_enabled = st.toggle(
            "Enable TTS",
            value=bool(st.session_state.get(f"{TAB_KEY}_tts_on", True)),
            key=f"{TAB_KEY}_tts_on",
        )

        cur_tts = (rd.get("tts_engine") or "gtts")
        if cur_tts == "edge-tts":
            cur_tts = "edge"

        tts_opts = ["edge", "gtts"] + (["elevenlabs"] if has_eleven else [])
        if cur_tts not in tts_opts:
            cur_tts = tts_opts[0]

        tts_engine = st.selectbox(
            "TTS engine",
            tts_opts,
            index=tts_opts.index(st.session_state.get(f"{TAB_KEY}_tts", cur_tts)),
            disabled=not tts_enabled,
            key=f"{TAB_KEY}_tts",
            format_func=(lambda x: "edge-tts (gratis)" if x == "edge" else x),
        )

        tts_speed = st.slider(
            "Speed",
            min_value=0.6, max_value=1.4,
            value=float(st.session_state.get(f"{TAB_KEY}_tts_speed", 1.0)),
            step=0.05,
            disabled=not tts_enabled,
            key=f"{TAB_KEY}_tts_speed",
        )

        tts_voice = ""
        if tts_engine == "edge":
            edge_default = (rd.get("edge_voice") or (edge_pool[0] if edge_pool else "id-ID-ArdiNeural")).strip()
            if edge_pool and edge_default not in edge_pool:
                edge_default = edge_pool[0]

            tts_voice = st.selectbox(
                "Voice (edge-tts)",
                edge_pool,
                index=edge_pool.index(st.session_state.get(f"{TAB_KEY}_edge_voice", edge_default)) if edge_pool else 0,
                disabled=not tts_enabled,
                key=f"{TAB_KEY}_edge_voice",
            )

        elif tts_engine == "elevenlabs":
            pool = _parse_csv(str(rd.get("voice_id", "") or ""))
            pool_default_text = ", ".join(pool)
            pool_raw = st.text_area(
                "ElevenLabs voice_id pool (pisahkan koma / newline)",
                value=str(st.session_state.get(f"{TAB_KEY}_eleven_pool", pool_default_text)),
                key=f"{TAB_KEY}_eleven_pool",
                height=80,
                disabled=not tts_enabled,
            )
            pool2 = [x.strip() for x in pool_raw.replace("\n", ",").split(",") if x.strip()]
            if not pool2:
                st.warning("ElevenLabs dipilih tapi pool kosong. Isi render_defaults.voice_id di My Profile.")
                tts_voice = ""
            else:
                tts_voice = pool2[0]
                st.caption(f"✅ ElevenLabs voice digunakan: `{tts_voice}`")

        else:
            st.caption("gTTS tidak butuh voice dropdown.")
            tts_voice = ""

    out["tts_enabled"] = bool(tts_enabled)
    out["tts"] = str(tts_engine)
    out["tts_voice"] = str(tts_voice or "").strip()
    out["tts_speed"] = float(tts_speed)

    # -------------------
    # Watermark
    # -------------------
    with st.expander("🏷️ Watermark", expanded=False):
        handle_list = _parse_csv(str(rd.get("watermark_handles_csv", "") or ""))
        if not handle_list and (rd.get("watermark_handle") or "").strip():
            handle_list = [(rd.get("watermark_handle") or "").strip()]
        handle_default = (rd.get("watermark_handle") or "@yourchannel").strip()

        wm_enabled = st.toggle(
            "Enable Watermark",
            value=bool(st.session_state.get(f"{TAB_KEY}_wm_on", True)),
            key=f"{TAB_KEY}_wm_on",
        )

        if handle_list:
            cur = st.session_state.get(f"{TAB_KEY}_handle", handle_default)
            if cur not in handle_list:
                cur = handle_list[0]
            handle = st.selectbox("Watermark handle", handle_list, index=handle_list.index(cur), disabled=not wm_enabled, key=f"{TAB_KEY}_handle")
        else:
            handle = st.text_input("Watermark handle", value=st.session_state.get(f"{TAB_KEY}_handle", handle_default), disabled=not wm_enabled, key=f"{TAB_KEY}_handle")

        wm_pos_list = ["top-right", "top-left", "bottom-right", "bottom-left"]
        pos_default = (rd.get("watermark_position") or "top-right").strip()
        if pos_default not in wm_pos_list:
            pos_default = "top-right"

        wm_pos = st.selectbox(
            "Position",
            wm_pos_list,
            index=wm_pos_list.index(st.session_state.get(f"{TAB_KEY}_wm_pos", pos_default)),
            key=f"{TAB_KEY}_wm_pos",
            disabled=not wm_enabled,
        )

        op_default = _opacity_to_255(rd.get("watermark_opacity", 120), default=120)
        wm_opacity = st.slider(
            "Opacity (0–255)",
            0, 255,
            int(st.session_state.get(f"{TAB_KEY}_wm_op", op_default)),
            key=f"{TAB_KEY}_wm_op",
            disabled=not wm_enabled,
        )

    out["handle"] = str(handle or "").strip()
    out["no_watermark"] = (not bool(wm_enabled))
    out["watermark_opacity"] = int(wm_opacity)
    out["watermark_position"] = str(wm_pos)

    # -------------------
    # Hook subtitle + misc
    # -------------------
    with st.expander("✨ Hook & Misc", expanded=False):
        hook_list = _parse_csv(str(rd.get("hook_subtitles_csv", "") or ""))
        if not hook_list and (rd.get("hook_sub") or "").strip():
            hook_list = [(rd.get("hook_sub") or "").strip()]
        hook_default = (rd.get("hook_sub") or "FAKTA CEPAT").strip()
        hook_on_default = bool(rd.get("hook_subtitle_default", True))

        hook_on = st.toggle("Hook subtitle ON", value=bool(st.session_state.get(f"{TAB_KEY}_hook_on", hook_on_default)), key=f"{TAB_KEY}_hook_on")

        if hook_list:
            cur = st.session_state.get(f"{TAB_KEY}_hook_sub", hook_default)
            if cur not in hook_list:
                cur = hook_list[0]
            hook_subtitle = st.selectbox("--hook-subtitle", hook_list, index=hook_list.index(cur), disabled=not hook_on, key=f"{TAB_KEY}_hook_sub")
        else:
            hook_subtitle = st.text_input("--hook-subtitle", value=st.session_state.get(f"{TAB_KEY}_hook_sub", hook_default), disabled=not hook_on, key=f"{TAB_KEY}_hook_sub")

        seconds = st.number_input("--seconds (opsional, 0=auto)", min_value=0, max_value=120, value=int(st.session_state.get(f"{TAB_KEY}_seconds", 0)), key=f"{TAB_KEY}_seconds")
        cinematic = st.toggle("--cinematic", value=bool(st.session_state.get(f"{TAB_KEY}_cin", False)), key=f"{TAB_KEY}_cin")

    out["hook_subtitle"] = (hook_subtitle if hook_on else "")
    out["seconds"] = int(seconds) if int(seconds) > 0 else None
    out["cinematic"] = bool(cinematic)

    # -------------------
    # Avatar (postprocess via JobStore -> core.postprocess)
    # -------------------
    with st.expander("🧑‍⚕️ Avatar (Lipsync Rhubarb)", expanded=False):
        avatar_enabled = st.checkbox(
            "Enable Avatar (lipsync mengikuti audio)",
            value=bool(st.session_state.get(f"{TAB_KEY}_avatar_on", False)),
            key=f"{TAB_KEY}_avatar_on",
            help="Butuh rhubarb terinstall. Avatar overlay (postprocess).",
        )

        if not avatar_ids:
            st.warning("Folder avatar tidak ditemukan: `assets/avatars/` (legacy repo).")
            avatar_ids = ["cat_v1"]

        cur_id = str(st.session_state.get(f"{TAB_KEY}_avatar_id", avatar_ids[0]) or avatar_ids[0]).strip()
        if cur_id not in avatar_ids:
            cur_id = avatar_ids[0]

        avatar_pick = st.selectbox(
            "Pilih Avatar",
            avatar_ids,
            index=avatar_ids.index(cur_id),
            key=f"{TAB_KEY}_avatar_pick",
            disabled=not avatar_enabled,
        )
        st.session_state[f"{TAB_KEY}_avatar_id"] = avatar_pick

        avatar_scale = st.slider(
            "Avatar scale (proporsi tinggi video)",
            min_value=0.10,
            max_value=0.35,
            value=float(st.session_state.get(f"{TAB_KEY}_avatar_scale", 0.20)),
            step=0.01,
            key=f"{TAB_KEY}_avatar_scale",
            disabled=not avatar_enabled,
        )

        avatar_pos = st.selectbox(
            "Avatar position",
            ["bottom-right", "bottom-left", "top-right", "top-left"],
            index=["bottom-right", "bottom-left", "top-right", "top-left"].index(
                st.session_state.get(f"{TAB_KEY}_avatar_pos", "bottom-right")
            ),
            key=f"{TAB_KEY}_avatar_pos",
            disabled=not avatar_enabled,
        )

    out["avatar_enabled"] = bool(avatar_enabled)
    out["avatar_id"] = str(avatar_pick)
    out["avatar_scale"] = float(avatar_scale)
    out["avatar_position"] = str(avatar_pos)

    return out


def _opts_to_cli_args(opts: dict[str, Any]) -> list[str]:
    args: list[str] = []

    if opts.get("handle"):
        args += ["--handle", str(opts["handle"])]

    if opts.get("no_watermark"):
        args += ["--no-watermark"]
    else:
        args += ["--watermark-opacity", str(int(opts.get("watermark_opacity", 180)))]
        if opts.get("watermark_position"):
            args += ["--watermark-position", str(opts["watermark_position"])]

    hook = str(opts.get("hook_subtitle") or "").strip()
    if hook:
        args += ["--hook-subtitle", hook]

    if opts.get("seconds"):
        args += ["--seconds", str(int(opts["seconds"]))]

    if opts.get("cinematic"):
        args += ["--cinematic"]

    # TTS args
    if bool(opts.get("tts_enabled", True)):
        tts = str(opts.get("tts") or "").strip()
        if tts:
            args += ["--tts", tts]
            if tts == "edge":
                v = str(opts.get("tts_voice") or "").strip()
                if v:
                    args += ["--edge-voice", v]
            elif tts == "elevenlabs":
                v = str(opts.get("tts_voice") or "").strip()
                if v:
                    args += ["--eleven-voice", v]
    return args


def _latest_mp4_for_topic(topic: str, ws_root: Path) -> Path | None:
    """
    fallback kalau output_video belum sempat ke-save meta.
    """
    try:
        ws_root = Path(ws_root).resolve()
        cands = [
            ws_root / "out" / topic,
            ws_root / "out",
            ws_root / "results" / topic,
            ws_root / "results",
            ws_root / "outputs" / topic,
            ws_root / "outputs",
            ws_root / "renders" / topic,
            ws_root / "renders",
        ]
        mp4s: list[Path] = []
        for d in cands:
            if d.exists():
                mp4s.extend([p for p in d.rglob("*.mp4") if p.is_file()])
        if not mp4s:
            return None
        mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return mp4s[0]
    except Exception:
        return None


# =========================
# MAIN RENDER
# =========================
def render(ctx):
    _init_state()

    legacy_root = _legacy_root()
    ws_root = _ws_root(ctx, legacy_root)
    dirs = _ws_dirs(ctx, ws_root)

    _inject_env_keys(ctx)

    prof = _ctx_profile(ctx)
    global_prof = _ctx_global_profile(ctx)
    rd = (prof.get("render_defaults") or {}) if isinstance(prof, dict) else {}
    edge_pool = _edge_voice_pool(ctx, global_prof)

    # key availability
    api_keys = _ctx_api_keys(ctx)
    pexels_key = ((api_keys.get("pexels") or "") or os.getenv("PEXELS_API_KEY", "")).strip()
    eleven_key = ((api_keys.get("elevenlabs") or "") or os.getenv("ELEVENLABS_API_KEY", "")).strip()
    has_eleven = bool(eleven_key)

    # access
    role = _role_from_ctx(ctx)
    can_generate = role in ("admin", "user", "")  # aman untuk legacy
    if role not in ("admin", "user", ""):
        st.info("Mode VIEWER: tombol Generate dinonaktifkan.")

    # job store
    js = JobStore(ws_root / "jobs")
    last_job_id_key = f"{TAB_KEY}_last_job_id"
    last_job_id = st.session_state.get(last_job_id_key)

    # ===== Header =====
    st.header("🧩 Merge Images — Manual (Background)")
    st.caption("Pilih text + pilih image manual (upload / pexels) → generate render via Jobs List. (tanpa realtime log/progress)")
    # st.caption(f"Workspace: `{ws_root}`")

    c_top1, c_top2, c_top3 = st.columns([1, 1, 2])
    with c_top1:
        if st.button("↻ Refresh", use_container_width=True, key=f"{TAB_KEY}_refresh"):
            st.rerun()
    with c_top2:
        if st.button("🧹 Reset Pilihan", use_container_width=True, key=f"{TAB_KEY}_reset"):
            _cleanup_run_files(reset_pexels_query=True)
            st.rerun()
    with c_top3:
        if last_job_id:
            j = js.get(str(last_job_id))
            if j:
                st.caption(f"Last job: `{j.id}` • status: **{j.status}**")
            else:
                st.caption("Last job: (tidak ditemukan)")

    st.divider()

    # ---- 1) pilih content ----
    st.caption("📝 **Content**")
    topics = _list_topics(dirs["contents"])
    if not topics:
        st.warning(f"Tidak ada topic folder di: {dirs['contents']}")
        return

    cT, cF = st.columns([1, 2])

    with cT:
        topic = st.selectbox("📁 Topic", topics, index=0, key=f"{TAB_KEY}_topic")

    topic_dir = dirs["contents"] / topic
    txt_files = _list_txt_files(topic_dir)
    if not txt_files:
        st.warning(f"Tidak ada file .txt di: {topic_dir}")
        return

    txt_names = [p.name for p in txt_files]

    # jaga-jaga kalau topic berubah dan file sebelumnya tidak ada di topic baru
    prev = st.session_state.get(f"{TAB_KEY}_txt_name")
    if prev not in txt_names:
        st.session_state[f"{TAB_KEY}_txt_name"] = txt_names[0]

    with cF:
        chosen = st.selectbox("📄 File .txt", txt_names, key=f"{TAB_KEY}_txt_name")

    content_path = (topic_dir / chosen).resolve()

    preview = _preview_txt(content_path, max_lines=12)
    with st.expander("👀 Preview Content Text", expanded=False):
        st.code(preview, language="text")
        content_text_full = content_path.read_text(encoding="utf-8", errors="replace")

    # ---- 2) images ----
    st.caption("🖼️ **Images (max 5)**")
    upload_run_dir = dirs["uploads"] / "manual_images"
    upload_run_dir.mkdir(parents=True, exist_ok=True)

    cX, cY, cZ = st.columns([1, 1, 2])
    with cX:
        st.button("🧼 Clear Selected", use_container_width=True, on_click=_clear_selected_images_only, key=f"{TAB_KEY}_btn_clear_sel")
    with cY:
        st.button("🧹 Clear Search", use_container_width=True, on_click=_clear_pexels_search, key=f"{TAB_KEY}_btn_clear_search")
    with cZ:
        st.caption("Tip: Clear Search untuk reset hasil Pexels + query UI.")

    _render_local_uploader(upload_run_dir)
    with st.expander("🔍 Pexels", expanded=False):
        _render_pexels_picker(content_text_full, api_key=pexels_key, upload_dir=upload_run_dir)

    _render_selected_images()

    # ---- 3) options ----
    st.caption("⚙️ **Render Options**")
    avatar_ids = _list_avatar_ids(legacy_root)
    opts = _render_options_form(rd=rd, edge_pool=edge_pool, has_eleven=has_eleven, avatar_ids=avatar_ids)

    # ---- 4) manifest ----
    st.caption("🧾 **Manifest**")
    mb = ManifestBuilder(dirs["manifests"] / "manual")
    images = _images_as_paths()

    can_manifest = content_path.exists() and len(images) > 0
    if not can_manifest:
        st.info("Syarat manifest: pilih content + minimal 1 image (maks 5).")

    if st.button("🧾 Generate Manifest", key=f"{TAB_KEY}_gen_manifest", disabled=(not can_manifest)):
        ts = time.strftime("%Y%m%d_%H%M%S")
        slug = content_path.stem
        mf_path = mb.build(
            topic=topic,
            source_txt_path=content_path,
            images=images,
            render_options=opts,
            created_at=ts,
            slug=slug,
        )
        st.session_state[f"{TAB_KEY}_manifest_path"] = str(mf_path)
        st.toast("✅ Manifest dibuat", icon="✅")
        st.rerun()

    mf_path_str = st.session_state.get(f"{TAB_KEY}_manifest_path")

    # ---- 5) enqueue render (background) ----
    st.caption("🚀 **Render (Background)**")

    # stop last job
    cA, cB, cC = st.columns([2, 1, 2])
    with cA:
        start_disabled = (not mf_path_str) or (not can_generate)
        if st.button("🚀 GENERATE / RENDER (Background)", key=f"{TAB_KEY}_start_bg", disabled=start_disabled):
            main_py = (legacy_root / "main.py").resolve()

            cmd = [
                sys.executable, str(main_py),
                "--manual",
                "--manual-manifest", str(Path(mf_path_str).resolve()),
                "--topic", topic,
                "--file", str(content_path.resolve()),
            ]
            cmd += _opts_to_cli_args(opts)

            env = os.environ.copy()
            # assets dirs (dipakai postprocess)
            env["YTA_BGM_DIR"] = str((legacy_root / "assets" / "bgm").resolve())
            env["YTA_AVATARS_DIR"] = str((legacy_root / "assets" / "avatars").resolve())

            # api keys
            keys = _ctx_api_keys(ctx) if isinstance(ctx, dict) else {}
            if keys.get("elevenlabs"):
                env["ELEVENLABS_API_KEY"] = str(keys["elevenlabs"])
            if keys.get("gemini"):
                env["GEMINI_API_KEY"] = str(keys["gemini"])
                env["GOOGLE_API_KEY"] = str(keys["gemini"])
            if keys.get("pexels"):
                env["PEXELS_API_KEY"] = str(keys["pexels"])
            if keys.get("pixabay"):
                env["PIXABAY_API_KEY"] = str(keys["pixabay"])

            # postprocess options (supaya avatar sama seperti control panel)
            post = {
                "topic": topic,
                "tts_on": bool(opts.get("tts_enabled", True)),
                "bgm_on": False,
                "bgm_vol": 0.2,
                "bgm_file": "(auto/latest)",
                "avatar_on": bool(opts.get("avatar_enabled", False)),
                "avatar_id": str(opts.get("avatar_id") or "cat_v1"),
                "avatar_scale": float(opts.get("avatar_scale") or 0.20),
                "avatar_position": str(opts.get("avatar_position") or "bottom-right"),
            }

            meta = {
                "topic": topic,
                "mode": "Merge Images (Manual)",
                "file": str(content_path.resolve()),
                "manifest": str(Path(mf_path_str).resolve()),
                "post": post,
            }

            user = _ctx_get_username(ctx) or "unknown"
            job_id = js.enqueue(user=user, cmd=cmd, cwd=str(ws_root), env=env, meta=meta)

            st.session_state[last_job_id_key] = job_id
            st.session_state[f"{TAB_KEY}_cleaned_for_job"] = None  # reset cleanup marker

            st.success(f"✅ Proses berjalan di background. Job ID: `{job_id}`")
            st.caption("Buka tab **Jobs List** untuk cek status & log (admin).")
            st.rerun()

    with cB:
        stop_disabled = (not can_generate) or (not last_job_id)
        if st.button("⏹ Stop", key=f"{TAB_KEY}_stop_bg", disabled=stop_disabled):
            ok = js.stop(str(last_job_id))
            st.toast("🛑 Stop dikirim." if ok else "⚠️ Job tidak ditemukan / sudah selesai.", icon="🛑")
            st.rerun()

    with cC:
        if last_job_id:
            j = js.get(str(last_job_id))
            if j:
                st.markdown("### 📌 Status")
                st.write(f"**Job ID:** `{j.id}`")
                st.write(f"**Status:** **{j.status}**")
            else:
                st.caption("Job terakhir tidak ditemukan.")

    # ---- resolve output for preview ----
    out_mp4 = None
    if last_job_id:
        j = js.get(str(last_job_id))
        if j and isinstance(j.meta, dict):
            out_mp4 = str((j.meta or {}).get("output_video") or "").strip() or None

            # auto-clean selected temp images AFTER job done once
            if j.status in ("done", "error", "stopped"):
                cleaned = st.session_state.get(f"{TAB_KEY}_cleaned_for_job")
                if cleaned != j.id:
                    # cleanup temp downloads/uploads (aman, tidak menyentuh out)
                    _cleanup_run_files(reset_pexels_query=False)
                    st.session_state[f"{TAB_KEY}_cleaned_for_job"] = j.id

    if not out_mp4:
        cand = _latest_mp4_for_topic(topic, ws_root)
        if cand:
            out_mp4 = str(cand.resolve())

    st.divider()
    st.caption("📺 **Preview**")

    if out_mp4 and Path(out_mp4).exists():
        left, right = st.columns([1, 1])
        with left:
            st.video(out_mp4)  # ✅ setengah lebar (kolom kiri)
        with right:
            fname = Path(out_mp4).name
            st.caption("🎞️ Output")
            st.code(fname)

            try:
                with open(out_mp4, "rb") as f:
                    st.download_button(
                        "⬇️ Download MP4",
                        data=f,
                        file_name=fname,
                        mime="video/mp4",
                        use_container_width=True,
                    )
            except Exception as e:
                st.caption(f"(Download gagal: {e})")
    else:
        st.caption("Belum ada hasil MP4 untuk dipreview.")
