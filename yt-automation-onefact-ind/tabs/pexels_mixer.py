from __future__ import annotations

import os
import time
import sys
import json
import hashlib
import subprocess
from pathlib import Path
from typing import Any

import requests
import streamlit as st

from core.job_store import JobStore

PEXELS_API = "https://api.pexels.com/videos/search"
TAB_KEY = "pexels_mixer"


# -------------------------
# ctx / workspace helpers (aman: Path / str)
# -------------------------
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
    return Path(__file__).resolve().parents[1]


def _ws_root(ctx) -> Path:
    if isinstance(ctx, dict):
        paths = ctx.get("paths") or {}
        p = _coerce_path(paths.get("user_root"))
        if p:
            p.mkdir(parents=True, exist_ok=True)
            (p / "out").mkdir(parents=True, exist_ok=True)
            (p / "downloads").mkdir(parents=True, exist_ok=True)
            (p / "manifests").mkdir(parents=True, exist_ok=True)
            (p / "jobs").mkdir(parents=True, exist_ok=True)
            return p
    return _legacy_root()


def _ctx_api_keys(ctx) -> dict:
    return (ctx.get("api_keys") or {}) if isinstance(ctx, dict) else {}


def _get_role(ctx) -> str:
    if isinstance(ctx, dict) and ctx.get("auth_role"):
        return str(ctx.get("auth_role") or "").strip().lower()
    return str(st.session_state.get("auth_role", "") or "").strip().lower()


def _get_user(ctx) -> str:
    if isinstance(ctx, dict):
        u = (ctx.get("auth_user") or ctx.get("user") or ctx.get("username") or "").strip()
        if u:
            return u
    return "unknown"


# -------------------------
# Helpers
# -------------------------
def _ts_compact() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _safe_filename(s: str) -> str:
    keep = []
    for ch in (s or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        elif ch.isspace():
            keep.append("_")
    out = "".join(keep).strip("_")
    out = out[:80] or "pexels_mix"
    if not out.lower().endswith(".mp4"):
        out += ".mp4"
    return out


def _suggest_outname(keyword: str) -> str:
    base = _safe_filename(keyword or "pexels_mix")
    if base.lower().endswith(".mp4"):
        base = base[:-4]
    return f"{base}_{_ts_compact()}.mp4"


def _ratio_close(a: float, b: float, tol: float = 0.08) -> bool:
    if b == 0:
        return False
    return abs(a - b) / b <= tol


def _match_aspect(w: int, h: int, mode: str) -> bool:
    if not w or not h:
        return True if mode == "All" else False

    r = w / h
    if mode == "All":
        return True
    if mode == "9:16 (Portrait)":
        return _ratio_close(r, 9 / 16, tol=0.10)
    if mode == "16:9 (Landscape)":
        return _ratio_close(r, 16 / 9, tol=0.10)
    return True


def _pick_best_file(mp4s: list[dict], aspect_filter: str):
    scored = []
    for f in mp4s:
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        if w <= 0 or h <= 0:
            continue
        ok = _match_aspect(w, h, aspect_filter)
        area = w * h
        scored.append((1 if ok else 0, area, f))

    if not scored:
        return None

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    if aspect_filter != "All":
        for ok, area, f in scored:
            if ok == 1:
                return f
        return scored[0][2]

    return scored[0][2]


def _latest_out_mp4(ws_root: Path) -> Path | None:
    out_dir = (ws_root / "out" / "pexels_mix").resolve()
    if not out_dir.exists():
        return None
    mp4s = sorted([p for p in out_dir.glob("*.mp4") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[0] if mp4s else None


# -------------------------
# Background runner builder
# -------------------------
def _write_job_payload(ws_root: Path, payload: dict[str, Any]) -> Path:
    mf_dir = (ws_root / "manifests" / "pexels_mixer").resolve()
    mf_dir.mkdir(parents=True, exist_ok=True)
    path = (mf_dir / f"pexels_mix_{_ts_compact()}.json").resolve()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_runner(ws_root: Path, cfg_path: Path) -> Path:
    run_dir = (ws_root / "manifests" / "pexels_mixer").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    runner_path = (run_dir / f"_runner_pexels_mix_{_ts_compact()}.py").resolve()

    # Runner: download -> trim -> concat -> print OUTPUT_MP4 -> cleanup tmp
    runner = f"""\
import os, sys, json, shutil, subprocess, time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

CFG = {str(cfg_path)!r}

def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode, (p.stderr or p.stdout or "")[-2000:]

def _download(url: str, out_path: Path) -> bool:
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # reuse if exists
        if out_path.exists() and out_path.stat().st_size > 0:
            return True
        import requests
        headers = {{
            "User-Agent": "Mozilla/5.0 (compatible; PexelsMixer/1.0)",
        }}
        r = requests.get(url, headers=headers, stream=True, timeout=90, allow_redirects=True)
        r.raise_for_status()
        with out_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024*256):
                if chunk:
                    f.write(chunk)
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        print("[DL][ERR]", type(e).__name__, str(e), flush=True)
        return False

def main():
    cfg = json.loads(Path(CFG).read_text(encoding="utf-8"))
    items = cfg.get("items") or []
    out_w = int(cfg.get("out_w") or 720)
    out_h = int(cfg.get("out_h") or 1280)
    fps   = int(cfg.get("fps") or 30)
    crf   = int(cfg.get("crf") or 20)

    ws_root = Path(cfg.get("ws_root")).resolve()
    dl_dir  = Path(cfg.get("dl_dir")).resolve()
    out_dir = Path(cfg.get("out_dir")).resolve()
    tmp_dir = Path(cfg.get("tmp_dir")).resolve()

    out_dir.mkdir(parents=True, exist_ok=True)
    dl_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if not items:
        print("[ERR] no items", flush=True)
        return 2

    # download all
    local_paths = []
    for it in items:
        vid = str(it.get("id") or "vid")
        url = str(it.get("file_url") or "").strip()
        if not url:
            print("[WARN] missing url for", vid, flush=True)
            continue
        outp = (dl_dir / f"pexels_{{vid}}.mp4").resolve()
        ok = _download(url, outp)
        if not ok:
            print("[DL][ERR] failed:", vid, flush=True)
            return 3
        local_paths.append((it, outp))

    if not local_paths:
        print("[ERR] all downloads failed", flush=True)
        return 4

    vf = (
        f"scale={{out_w}}:{{out_h}}:force_original_aspect_ratio=decrease,"
        f"pad={{out_w}}:{{out_h}}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={{fps}}"
    )

    trimmed = []
    for i, (it, src) in enumerate(local_paths):
        start = float(it.get("start") or 0.0)
        dur   = float(it.get("dur") or 5.0)
        out_trim = (tmp_dir / f"trim_{{i:03d}}.mp4").resolve()

        cmd = [
            "ffmpeg","-y",
            "-ss", str(max(start, 0.0)),
            "-i", str(src),
            "-t", str(max(dur, 0.1)),
            "-vf", vf,
            "-c:v","libx264","-pix_fmt","yuv420p",
            "-preset","veryfast","-crf", str(int(crf)),
            "-an",
            str(out_trim),
        ]
        rc, tail = _run(cmd)
        if rc != 0:
            print("[FFMPEG][TRIM][ERR]", tail, flush=True)
            return 5
        trimmed.append(str(out_trim))

    list_file = (tmp_dir / "concat_list.txt").resolve()
    with list_file.open("w", encoding="utf-8") as f:
        for p in trimmed:
            f.write(f"file '{{p}}'\\n")

    out_name = str(cfg.get("out_name") or "pexels_mix.mp4")
    out_path = (out_dir / out_name).resolve()

    cmd_concat = [
        "ffmpeg","-y",
        "-f","concat","-safe","0",
        "-i", str(list_file),
        "-c:v","libx264","-pix_fmt","yuv420p",
        "-preset","veryfast","-crf", str(int(crf)),
        "-movflags","+faststart",
        str(out_path),
    ]
    rc, tail = _run(cmd_concat)
    if rc != 0:
        print("[FFMPEG][CONCAT][ERR]", tail, flush=True)
        return 6

    print("OUTPUT_MP4:", str(out_path), flush=True)

    # cleanup tmp trims (keep downloads)
    try:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
    except Exception:
        pass

    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
"""
    runner_path.write_text(runner, encoding="utf-8")
    return runner_path


# -------------------------
# Main UI
# -------------------------
def render(ctx):
    st.subheader("🎞️ Pexels Mixer (Background)")
    st.caption("Cari video di Pexels → pilih manual + trim → jalankan merge di background (Jobs List).")

    ws_root = _ws_root(ctx)
    legacy_root = _legacy_root()

    api = _ctx_api_keys(ctx)
    role = _get_role(ctx)
    can_generate = (role == "admin")

    # workspace folders
    dl_dir = (ws_root / "downloads" / "pexels_videos").resolve()
    out_dir = (ws_root / "out" / "pexels_mix").resolve()
    tmp_root = (ws_root / "out" / "_tmp" / "pexels_mix").resolve()
    for p in [dl_dir, out_dir, tmp_root]:
        p.mkdir(parents=True, exist_ok=True)

    js = JobStore(ws_root / "jobs")
    st.session_state.setdefault("pm_results", [])
    st.session_state.setdefault("pm_selected", [])
    st.session_state.setdefault(f"{TAB_KEY}_last_job_id", None)

    last_job_id = st.session_state.get(f"{TAB_KEY}_last_job_id")

    c0, c1, c2 = st.columns([1, 1, 2])
    with c0:
        if st.button("↻ Refresh", use_container_width=True, key="pm_refresh"):
            st.rerun()
    with c1:
        if last_job_id and st.button("⏹ Stop Job", use_container_width=True, key="pm_stop", disabled=not can_generate):
            ok = js.stop(str(last_job_id))
            st.toast("🛑 Stop dikirim." if ok else "⚠️ Job tidak ditemukan / sudah selesai.", icon="🛑")
            st.rerun()
    with c2:
        if last_job_id:
            j = js.get(str(last_job_id))
            if j:
                st.caption(f"Job: `{j.id}` • status: **{j.status}**")
            else:
                st.caption(f"Job: `{last_job_id}` • (tidak ditemukan)")

    st.divider()
    st.caption(f"Workspace: `{ws_root}`")

    # ===== API Key =====
    pexels_key = str(api.get("pexels") or os.getenv("PEXELS_API_KEY", "")).strip()
    if not pexels_key:
        st.warning("PEXELS API key belum ada. Set global key di My Profile (admin) atau env PEXELS_API_KEY.")
        st.code("PEXELS_API_KEY=YOUR_KEY_HERE", language="bash")

    headers = {
        "Authorization": pexels_key,
        "User-Agent": "Mozilla/5.0 (compatible; AutoFactBot/1.0)",
        "Accept": "application/json",
    } if pexels_key else {}

    if not can_generate:
        st.info("Akun ini mode VIEWER: tombol Select/Render dinonaktifkan.")

    # ===== Search UI =====
    c1, c2, c3, c4 = st.columns([3, 1, 1.6, 1])
    with c1:
        q = st.text_input("Search keyword", value="nature cinematic", key="pm_q")
    with c2:
        per_page = st.number_input("Results", 5, 40, 12, key="pm_perpage")
    with c3:
        aspect_filter = st.radio(
            "Aspect filter",
            ["All", "9:16 (Portrait)", "16:9 (Landscape)"],
            horizontal=True,
            key="pm_aspect",
        )
    with c4:
        debug_on = st.checkbox("Debug", value=False, key="pm_debug")

    run_search = st.button("🔎 Search", use_container_width=True, disabled=not bool(pexels_key))
    if run_search and pexels_key:
        params = {"query": q, "per_page": int(per_page)}
        try:
            r = requests.get(PEXELS_API, headers=headers, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            vids = data.get("videos", []) or []

            results = []
            for v in vids:
                vid_id = v.get("id")
                user = (v.get("user") or {}).get("name", "")
                url_page = v.get("url", "")
                duration = float(v.get("duration") or 0)
                image = v.get("image", "")

                files = v.get("video_files") or []
                mp4s = [f for f in files if f.get("file_type") == "video/mp4"]
                if not mp4s:
                    continue

                chosen = _pick_best_file(mp4s, aspect_filter)
                if not chosen:
                    continue

                file_url = chosen.get("link")
                w = int(chosen.get("width") or 0)
                h = int(chosen.get("height") or 0)

                if not _match_aspect(w, h, aspect_filter):
                    if debug_on:
                        st.write(f"DEBUG SKIP vid={vid_id}: chosen={w}x{h} not match {aspect_filter}")
                    continue

                if not file_url:
                    continue

                results.append({
                    "id": vid_id,
                    "author": user,
                    "page": url_page,
                    "thumb": image,
                    "file_url": file_url,
                    "w": w, "h": h,
                    "duration": duration,
                })

            st.session_state.pm_results = results
            st.toast(f"Found {len(results)} videos", icon="✅")
        except Exception as e:
            st.error(f"Search error: {e}")

    # ===== Results =====
    results = st.session_state.pm_results
    if results:
        st.divider()
        st.markdown("### 1) Pilih Video")
        st.caption("Klik Select untuk memasukkan ke list Selected di bawah.")

        for v in results:
            with st.container(border=True):
                a, b, c = st.columns([1.2, 2.5, 1.3])

                with a:
                    if v.get("thumb"):
                        st.image(v["thumb"], use_container_width=True)

                with b:
                    st.markdown(
                        f"**ID:** `{v['id']}`  \n"
                        f"**Author:** {v['author']}  \n"
                        f"**Size:** {v['w']}x{v['h']}  \n"
                        f"**Dur:** {v['duration']}s"
                    )
                    st.caption(v.get("page", ""))

                with c:
                    picked = st.button("➕ Select", key=f"pm_add_{v['id']}", use_container_width=True, disabled=not can_generate)
                    if picked:
                        exists = any(x["id"] == v["id"] for x in st.session_state.pm_selected)
                        if not exists:
                            st.session_state.pm_selected.append({
                                "id": v["id"],
                                "file_url": v["file_url"],
                                "start": 0.0,
                                "dur": min(6.0, float(v["duration"] or 6.0)),
                                "meta": v,
                            })
                            st.toast(f"Added {v['id']}", icon="➕")
                        else:
                            st.toast("Already selected", icon="ℹ️")
    else:
        st.info("Search dulu untuk melihat hasil video Pexels.")

    # ===== Selected list =====
    st.divider()
    st.markdown("### 2) Selected (Urutan + Trim)")
    selected = st.session_state.pm_selected
    if not selected:
        st.info("Belum ada video yang dipilih.")
        return

    cc1, cc2, cc3 = st.columns([1, 1.6, 1.4])
    with cc1:
        if st.button("🧹 Clear Selected", use_container_width=True, disabled=not can_generate):
            st.session_state.pm_selected = []
            st.rerun()

    with cc2:
        suggested = _suggest_outname(q)

        # init state aman
        if "pm_outname_value" not in st.session_state:
            st.session_state["pm_outname_value"] = suggested
            st.session_state["pm_outname_last_q"] = q
            st.session_state["pm_outname_last_suggested"] = suggested

        # update default hanya jika user belum edit manual
        if st.session_state.get("pm_outname_last_q") != q:
            cur_val = (st.session_state.get("pm_outname_value") or "").strip()
            last_sug = (st.session_state.get("pm_outname_last_suggested") or "").strip()
            if (not cur_val) or (cur_val == last_sug):
                st.session_state["pm_outname_value"] = suggested
            st.session_state["pm_outname_last_q"] = q
            st.session_state["pm_outname_last_suggested"] = suggested

        out_name = st.text_input(
            "Output filename",
            value=st.session_state.get("pm_outname_value", suggested),
            key="pm_outname_input",
            help="Default otomatis: <keyword>_<timestamp>.mp4 (boleh diedit)",
        )
        st.session_state["pm_outname_value"] = out_name

    with cc3:
        st.caption("Tip: durasi 3–6 detik biasanya enak buat konten pendek.")

    for i, it in enumerate(selected):
        meta = it.get("meta", {})
        with st.container(border=True):
            cA, cB, cC, cD = st.columns([1.2, 2.2, 1.3, 1.3])

            with cA:
                thumb = meta.get("thumb") or meta.get("image")
                if thumb:
                    st.image(thumb, use_container_width=True)

            with cB:
                st.markdown(f"**#{i+1}**  |  ID: `{it['id']}`")
                st.caption(meta.get("page", ""))

            with cC:
                it["start"] = float(st.number_input(
                    "Start (sec)", 0.0, 9999.0, float(it.get("start", 0.0)),
                    step=0.5, key=f"pm_start_{it['id']}", disabled=not can_generate
                ))
                it["dur"] = float(st.number_input(
                    "Dur (sec)", 0.5, 120.0, float(it.get("dur", 5.0)),
                    step=0.5, key=f"pm_dur_{it['id']}", disabled=not can_generate
                ))

            with cD:
                up = st.button("⬆️", key=f"pm_up_{it['id']}", use_container_width=True, disabled=(i == 0) or (not can_generate))
                dn = st.button("⬇️", key=f"pm_dn_{it['id']}", use_container_width=True, disabled=(i == len(selected)-1) or (not can_generate))
                rm = st.button("🗑️ Remove", key=f"pm_rm_{it['id']}", use_container_width=True, disabled=not can_generate)

                if up:
                    st.session_state.pm_selected[i-1], st.session_state.pm_selected[i] = st.session_state.pm_selected[i], st.session_state.pm_selected[i-1]
                    st.rerun()
                if dn:
                    st.session_state.pm_selected[i+1], st.session_state.pm_selected[i] = st.session_state.pm_selected[i], st.session_state.pm_selected[i+1]
                    st.rerun()
                if rm:
                    st.session_state.pm_selected.pop(i)
                    st.rerun()

    # ===== Output Size Presets =====
    st.divider()
    st.markdown("### 3) Render (Background)")

    SIZE_PRESETS = {
        "720x1280 (Shorts 9:16)": (720, 1280),
        "1080x1920 (FullHD 9:16)": (1080, 1920),
        "1280x720 (HD 16:9)": (1280, 720),
        "1920x1080 (FullHD 16:9)": (1920, 1080),
        "1080x1080 (Square)": (1080, 1080),
        "Custom...": None,
    }

    s1, s2, s3 = st.columns([1.6, 1, 1])
    with s1:
        size_label = st.selectbox("Size preset", list(SIZE_PRESETS.keys()), index=0, key="pm_out_size")
    with s2:
        fps = st.number_input("FPS", min_value=24, max_value=60, value=30, step=1, key="pm_out_fps")
    with s3:
        crf = st.number_input("CRF", min_value=14, max_value=30, value=20, step=1, key="pm_out_crf")

    if size_label == "Custom...":
        cW, cH = st.columns(2)
        with cW:
            out_w = st.number_input("Custom width", min_value=240, max_value=4096, value=1280, step=2, key="pm_custom_w")
        with cH:
            out_h = st.number_input("Custom height", min_value=240, max_value=4096, value=720, step=2, key="pm_custom_h")
    else:
        out_w, out_h = SIZE_PRESETS[size_label]

    start_bg = st.button("🚀 Start Merge (Background)", type="primary", use_container_width=True, disabled=not can_generate)
    if start_bg:
        # build payload for runner
        out_name_final = (st.session_state.get("pm_outname_value") or "").strip()
        if not out_name_final:
            out_name_final = _suggest_outname(q)
        out_name_final = _safe_filename(out_name_final)

        items = []
        for it in st.session_state.pm_selected:
            items.append({
                "id": it.get("id"),
                "file_url": it.get("file_url"),
                "start": float(it.get("start") or 0.0),
                "dur": float(it.get("dur") or 5.0),
            })

        if not items:
            st.error("Selected kosong.")
            st.stop()

        job_tag = _ts_compact()
        tmp_dir = (tmp_root / f"job_{job_tag}").resolve()

        payload = {
            "ws_root": str(ws_root.resolve()),
            "dl_dir": str(dl_dir),
            "out_dir": str(out_dir),
            "tmp_dir": str(tmp_dir),
            "out_name": out_name_final,
            "out_w": int(out_w),
            "out_h": int(out_h),
            "fps": int(fps),
            "crf": int(crf),
            "items": items,
            "query": str(q),
        }

        cfg_path = _write_job_payload(ws_root, payload)
        runner_path = _write_runner(ws_root, cfg_path)

        # env
        env = os.environ.copy()
        if pexels_key:
            env["PEXELS_API_KEY"] = pexels_key

        # postprocess only for web-playable + cleanup naming (no bgm/avatar)
        post = {
            "topic": "pexels_mix",
            "tts_on": True,
            "bgm_on": False,
            "bgm_vol": 0.2,
            "bgm_file": "(auto/latest)",
            "avatar_on": False,
            "avatar_id": "",
            "avatar_scale": 0.2,
            "avatar_position": "bottom-right",
        }

        meta = {
            "topic": "pexels_mix",
            "mode": "Pexels Mixer",
            "query": str(q),
            "post": post,
        }

        user = _get_user(ctx)
        job_id = js.enqueue(
            user=user,
            cmd=[sys.executable, "-u", str(runner_path)],
            cwd=str(ws_root),
            env=env,
            meta=meta,
        )

        st.session_state[f"{TAB_KEY}_last_job_id"] = job_id
        st.success(f"✅ Proses berjalan di background. Job ID: `{job_id}`")
        st.caption("Buka tab **Jobs List** untuk melihat status & log (admin).")
        st.rerun()

    # ===== Preview last output (simple) =====
    st.markdown("---")
    st.subheader("📺 Preview Hasil Terakhir")

    out_mp4 = None
    if last_job_id:
        j = js.get(str(last_job_id))
        if j and isinstance(j.meta, dict):
            out_mp4 = str((j.meta or {}).get("output_video") or "").strip() or None

    if not out_mp4:
        cand = _latest_out_mp4(ws_root)
        if cand:
            out_mp4 = str(cand)

    if out_mp4 and Path(out_mp4).exists():
        st.video(out_mp4)
        st.caption(f"🎞️ {Path(out_mp4).name}")
    else:
        st.caption("Belum ada output untuk dipreview.")
