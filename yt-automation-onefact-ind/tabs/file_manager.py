from __future__ import annotations

import os
import html
import re
import time
import json
import subprocess
import zipfile
from pathlib import Path
import streamlit as st

from core.job_store import JobStore

# =========================
# Helpers
# =========================


def _read_jobs_index(jobs_dir: Path) -> dict[str, dict]:
    """
    Baca jobs.json -> map job_id -> job dict
    """
    try:
        idx = (Path(jobs_dir) / "jobs.json").resolve()
        if not idx.exists():
            return {}
        data = json.loads(idx.read_text(encoding="utf-8", errors="ignore") or "{}")
        out: dict[str, dict] = {}
        for j in (data.get("jobs") or []):
            jid = str(j.get("id") or "").strip()
            if jid:
                out[jid] = j
        return out
    except Exception:
        return {}

def _fmt_dt_from_iso_or_mtime(iso: str | None, path: Path | None = None) -> str:
    """
    iso contoh: 2026-02-27T22:40:58 -> 2026-02-27 22:40
    fallback: mtime file
    """
    s = str(iso or "").strip()
    if s:
        return s.replace("T", " ")[:16]
    if path and path.exists():
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        except Exception:
            pass
    return "-"

def _job_display_name(meta: dict) -> str:
    """
    Nama job: prefer file basename -> manifest basename -> topic
    """
    try:
        f = str(meta.get("file") or "").strip()
        if f:
            return Path(f).name
        mf = str(meta.get("manifest") or "").strip()
        if mf:
            return Path(mf).name
        t = str(meta.get("topic") or "").strip()
        return t or "-"
    except Exception:
        return "-"

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

def _is_admin(role: str) -> bool:
    return (role or "").strip().lower() == "admin"

def _mode_badge(mode: str, source: str = "") -> str:
    m = (mode or "").lower()
    s = (source or "").lower()

    # paling tegas: source
    if s == "control_panel":
        return "🧩 Control Panel"
    if s == "autostock":
        return "🎬 AutoStock"
    if s == "merge_images":
        return "🧱 Merge Images"

    # fallback: dari mode text
    if "autostock" in m:
        return "🎬 AutoStock"
    if "merge images" in m or "manual" in m:
        return "🧱 Merge Images"
    if "single video" in m or "batch" in m:
        return "🧩 Control Panel"

    return f"⚙️ {mode or 'Unknown'}"

def _list_job_outputs(ws_root: Path) -> list[dict]:
    """
    Return list of dict:
      { "job_id", "status", "topic", "mode", "ended_at", "path" }
    """
    jobs_dir = (ws_root / "jobs").resolve()
    if not jobs_dir.exists():
        return []

    js = JobStore(jobs_dir)
    js.refresh_status()

    out = []
    for j in js.list_jobs():
        mm = j.meta or {}
        p = str(mm.get("output_video") or mm.get("raw_output_video") or "").strip()
        if not p:
            continue
        vp = Path(p)
        if not vp.exists() or not vp.is_file():
            continue

        out.append({
            "job_id": j.id,
            "status": j.status,
            "topic": str(mm.get("topic") or ""),
            "mode": str(mm.get("mode") or ""),
            "source": str(mm.get("source") or ""),
            "ended_at": str(j.ended_at or ""),
            "path": str(vp),
        })

    return out

def _stem_base(name: str) -> str:
    """
    Normalize stem untuk gabung varian:
      mobil_xxx_bgm_avatar_192712_web_fast -> mobil_xxx
    """
    stem = Path(name).stem
    stem = re.sub(r"(_bgm_avatar|_bgm|_avatar)(?:_\d{6})?", "", stem)
    stem = re.sub(r"(_web_fast|_web|_fast)$", "", stem)
    stem = re.sub(r"_\d{6}$", "", stem)
    return stem

def _list_final_videos_from_jobs(ws_root: Path) -> list[dict]:
    """
    Return list of dict:
      {job_id, status, topic, mode, ended_at, path}
    Hanya file mp4 yang benar2 ada.
    """
    jobs_dir = (ws_root / "jobs").resolve()
    if not jobs_dir.exists():
        return []

    js = JobStore(jobs_dir)
    js.refresh_status()

    rows: list[dict] = []
    for j in js.list_jobs():
        mm = j.meta or {}
        p = str(mm.get("output_video") or mm.get("raw_output_video") or "").strip()
        if not p:
            continue
        vp = Path(p)
        if (not vp.exists()) or (not vp.is_file()) or (vp.suffix.lower() != ".mp4"):
            continue

        rows.append({
            "job_id": j.id,
            "status": j.status,
            "topic": str(mm.get("topic") or ""),
            "mode": str(mm.get("mode") or ""),
            "source": str(mm.get("source") or ""),
            "ended_at": str(j.ended_at or ""),
            "path": str(vp),
        })

    # dedupe: kalau ada beberapa varian (jarang), ambil newest per base
    by_base: dict[str, dict] = {}
    for r in rows:
        vp = Path(r["path"])
        base = _stem_base(vp.name)
        mt = vp.stat().st_mtime if vp.exists() else 0
        old = by_base.get(base)
        if (old is None) or (mt > (Path(old["path"]).stat().st_mtime if Path(old["path"]).exists() else 0)):
            by_base[base] = r

    out = list(by_base.values())
    out.sort(key=lambda r: Path(r["path"]).stat().st_mtime if Path(r["path"]).exists() else 0, reverse=True)
    return out

def _is_temp_video_name(name: str) -> bool:
    n = (name or "")
    bad = ["TEMP_MPY", "_TEMP_MPY", ".tmp_", "_mouth_overlay", "_clip"]
    if any(x in n for x in bad):
        return True
    if n.startswith(".tmp_"):
        return True
    return False


def _pick_best_variant(paths: list[Path]) -> Path:
    def score(p: Path) -> tuple[int, float, int]:
        n = p.name
        s = 0
        if "_web_fast" in n: s = 60
        elif "_web" in n: s = 55
        elif "_bgm_avatar" in n: s = 50
        elif "_avatar" in n: s = 45
        elif "_bgm" in n: s = 35
        else: s = 20
        mt = p.stat().st_mtime if p.exists() else 0.0
        sz = p.stat().st_size if p.exists() else 0
        return (s, mt, sz)

    paths = [p for p in paths if p.exists()]
    paths.sort(key=score, reverse=True)
    return paths[0]


def _final_videos_for_topic(topic_dirs: list[Path]) -> list[Path]:
    """
    topic_dirs: bisa dari base_root/out/<topic> dan base_root/results/<topic>
    Return: hanya final best 1 file per base stem, newest-first
    """
    all_mp4: list[Path] = []
    for d in topic_dirs:
        if d.exists():
            all_mp4 += [p for p in d.glob("*.mp4") if p.is_file()]

    # filter temp
    all_mp4 = [p for p in all_mp4 if (p.stat().st_size > 0 and not _is_temp_video_name(p.name))]

    # group by base stem
    buckets: dict[str, list[Path]] = {}
    for p in all_mp4:
        b = _stem_base(p.name)
        buckets.setdefault(b, []).append(p)

    finals: list[Path] = []
    for _, group in buckets.items():
        finals.append(_pick_best_variant(group))

    finals.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return finals

def _legacy_root() -> Path:
    # yt-automation-onefact-ind/
    return Path(__file__).resolve().parents[1]


def _ws_root_from_ctx(ctx) -> Path | None:
    if not isinstance(ctx, dict):
        return None
    paths = ctx.get("paths") or {}
    return _coerce_path(paths.get("user_root"))


def _role_from_ctx(ctx) -> str:
    if isinstance(ctx, dict):
        r = (ctx.get("auth_role") or "").strip()
        if r:
            return r
    return (st.session_state.get("auth_role") or "").strip()


def _can_write(role: str) -> bool:
    # sesuaikan policy kamu di portal
    return role in ("admin", "user")


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_in_root(file_path: Path, root: Path) -> bool:
    try:
        rp = file_path.resolve()
        rr = root.resolve()
        return str(rp).startswith(str(rr) + os.sep) or rp == rr
    except Exception:
        return False


def _list_topics_from_out(out_root: Path) -> list[str]:
    if not out_root.exists():
        return []
    topics = [p.name for p in out_root.iterdir() if p.is_dir()]
    topics.sort(key=lambda x: x.lower())
    return topics


def _list_topics_from_contents(contents_root: Path) -> list[str]:
    if not contents_root.exists():
        return []
    topics = []
    for p in contents_root.iterdir():
        if p.is_dir():
            if p.name.lower() == "generated":
                continue
            topics.append(p.name)
    topics.sort(key=lambda x: x.lower())
    return topics


def _clean_temp_mp4(root: Path) -> int:
    """
    Hapus file sampah TEMP_MPY / .tmp_ di root yang dipilih (dan subfolder out/*).
    Aman: hanya di dalam root.
    """
    patterns = ["TEMP_MPY", ".tmp_", "_TEMP_MPY"]
    count = 0

    candidates: list[Path] = []
    candidates += list(root.glob("*.mp4"))
    out_root = root / "out"
    if out_root.exists():
        candidates += list(out_root.rglob("*.mp4"))

    for f in candidates:
        name = f.name
        if not any(pat in name for pat in patterns):
            continue
        if not _safe_in_root(f, root):
            continue
        try:
            f.unlink(missing_ok=True)
            count += 1
        except Exception:
            pass
    return count


def _ffmpeg_fix_playback(mp4_path: Path) -> tuple[bool, str]:
    """
    Re-encode video to H.264 + yuv420p for browser compatibility.
    Audio copied.
    """
    tmp = mp4_path.with_name(mp4_path.stem + "_fixed.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp4_path),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-c:a", "copy",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(mp4_path)
            return True, "✅ Video berhasil diperbaiki (browser-friendly)."
        return False, "❌ Gagal convert video (tmp tidak terbentuk)."
    except Exception as e:
        return False, f"❌ Error ffmpeg: {e}"


# =========================
# ZIP helpers (MULTI DOWNLOAD)
# =========================
def _zip_tmp_dir(base_root: Path) -> Path:
    """
    Temp zip folder di dalam base_root (workspace/legacy).
    """
    out_tmp = base_root / "out" / "_tmp" / "file_manager_zips"
    try:
        out_tmp.mkdir(parents=True, exist_ok=True)
        return out_tmp.resolve()
    except Exception:
        p = base_root / "_tmp_zips"
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()


def _purge_old_zips(tmp_dir: Path, older_seconds: int = 24 * 3600) -> int:
    """
    Bersihkan zip lama biar folder _tmp nggak numpuk.
    """
    if not tmp_dir.exists():
        return 0
    now = time.time()
    n = 0
    for z in tmp_dir.glob("*.zip"):
        try:
            if now - z.stat().st_mtime > older_seconds:
                z.unlink(missing_ok=True)
                n += 1
        except Exception:
            pass
    return n


def _safe_arcname(fp: Path, arc_base: Path) -> str:
    """
    Nama file di dalam ZIP = relatif terhadap arc_base.
    """
    try:
        rel = fp.resolve().relative_to(arc_base.resolve())
        return str(rel).replace("\\", "/")
    except Exception:
        return fp.name


def _build_zip_file(
    files: list[Path],
    *,
    zip_path: Path,
    arc_base: Path,
    root_guard: Path,
) -> tuple[bool, str]:
    """
    Build zip di disk, aman (hanya file di bawah root_guard).
    """
    try:
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        safe_files: list[Path] = []
        seen = set()
        for f in files:
            if not f.exists() or not f.is_file():
                continue
            if not _safe_in_root(f, root_guard):
                continue
            rp = str(f.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            safe_files.append(f)

        if not safe_files:
            return False, "Tidak ada file valid untuk di-zip."

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in safe_files:
                zf.write(str(f), arcname=_safe_arcname(f, arc_base))

        if zip_path.exists() and zip_path.stat().st_size > 0:
            return True, str(zip_path)
        return False, "ZIP gagal dibuat (file kosong)."
    except Exception as e:
        return False, f"ZIP error: {e}"


def _zip_ui_multiselect(
    *,
    title: str,
    key_prefix: str,
    options: list[tuple[str, Path]],   # (label, path)
    arc_base: Path,
    base_root: Path,
    disabled: bool,
) -> None:
    """
    UI multiselect + build zip + download zip (untuk area non-video).
    """
    if not options:
        return

    with st.expander(f"📦 {title} (Multi Download ZIP)", expanded=False):
        labels = [lab for lab, _ in options]
        map_label_to_path = {lab: p for lab, p in options}

        sel_key = f"{key_prefix}_sel"
        zip_path_key = f"{key_prefix}_zip_path"

        st.session_state.setdefault(sel_key, [])

        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("✅ Select All", use_container_width=True, disabled=disabled, key=f"{key_prefix}_selall"):
                st.session_state[sel_key] = labels
                st.rerun()
        with c2:
            if st.button("🧹 Clear", use_container_width=True, disabled=disabled, key=f"{key_prefix}_clear"):
                st.session_state[sel_key] = []
                st.session_state.pop(zip_path_key, None)
                st.rerun()
        with c3:
            tmp_dir = _zip_tmp_dir(base_root)
            purged = _purge_old_zips(tmp_dir)
            if purged:
                st.caption(f"🧼 Auto-clean: removed {purged} old ZIP(s)")

        selected = st.multiselect(
            "Pilih file untuk di-zip",
            options=labels,
            default=st.session_state.get(sel_key, []),
            key=sel_key,
            disabled=disabled,
        )

        st.caption(f"Selected: {len(selected)} file")

        c_build, c_hint = st.columns([1, 2])
        with c_build:
            build_clicked = st.button(
                "🧷 Buat ZIP",
                use_container_width=True,
                disabled=disabled or (not selected),
                key=f"{key_prefix}_build",
            )
        with c_hint:
            st.caption("ZIP dibuat di disk (lebih aman untuk file besar).")

        if build_clicked:
            tmp_dir = _zip_tmp_dir(base_root)
            ts = time.strftime("%Y%m%d_%H%M%S")
            zip_name = f"{key_prefix}_{ts}.zip"
            zip_path = (tmp_dir / zip_name).resolve()

            files = [map_label_to_path[x] for x in selected if x in map_label_to_path]
            ok, msg = _build_zip_file(files, zip_path=zip_path, arc_base=arc_base, root_guard=base_root)
            if ok:
                st.session_state[zip_path_key] = msg
                st.success(f"ZIP ready: {Path(msg).name}")
            else:
                st.error(msg)

        z = st.session_state.get(zip_path_key)
        if z and Path(z).exists():
            try:
                with open(z, "rb") as f:
                    st.download_button(
                        "⬇️ Download ZIP",
                        data=f,
                        file_name=Path(z).name,
                        mime="application/zip",
                        use_container_width=True,
                        key=f"{key_prefix}_dlzip",
                    )
            except Exception as e:
                st.error(f"Gagal download ZIP: {e}")


# =========================
# Inline checkbox ZIP (untuk Out Videos)
# =========================
def _init_selset(key: str) -> None:
    if key not in st.session_state or not isinstance(st.session_state.get(key), set):
        st.session_state[key] = set()


def _toggle_select_all(sel_key: str, ids: list[str]) -> None:
    _init_selset(sel_key)
    st.session_state[sel_key] = set(ids)


def _toggle_clear(sel_key: str, zip_key: str | None = None) -> None:
    _init_selset(sel_key)
    st.session_state[sel_key] = set()
    if zip_key:
        st.session_state.pop(zip_key, None)


def _selected_paths_from_ids(id_to_path: dict[str, Path], selected_ids: set[str]) -> list[Path]:
    out: list[Path] = []
    for _id in selected_ids:
        p = id_to_path.get(_id)
        if p:
            out.append(p)
    return out


def _inline_zip_bar(
    *,
    title: str,
    key_prefix: str,
    ids: list[str],
    id_to_path: dict[str, Path],
    arc_base: Path,
    base_root: Path,
    disabled: bool,
) -> None:
    """
    Bar di atas list video: select all/clear + buat zip + download zip.
    """
    sel_key = f"{key_prefix}_selset"
    zip_key = f"{key_prefix}_zip_path"
    _init_selset(sel_key)

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.2, 2.6])

        with c1:
            if st.button("✅ Select All", use_container_width=True, disabled=disabled, key=f"{key_prefix}_all"):
                _toggle_select_all(sel_key, ids)
                st.session_state.pop(zip_key, None)
                st.rerun()

        with c2:
            if st.button("🧹 Clear", use_container_width=True, disabled=disabled, key=f"{key_prefix}_clear"):
                _toggle_clear(sel_key, zip_key)
                st.rerun()

        with c3:
            selected_count = len(st.session_state.get(sel_key, set()))
            build_clicked = st.button(
                f"🧷 Buat ZIP ({selected_count})",
                use_container_width=True,
                disabled=disabled or selected_count == 0,
                key=f"{key_prefix}_build",
                help="Buat file ZIP dari video yang dicentang",
            )

        with c4:
            tmp_dir = _zip_tmp_dir(base_root)
            purged = _purge_old_zips(tmp_dir)
            hint = f"📦 {title} — Centang video → Buat ZIP → Download"
            if purged:
                hint += f" | 🧼 cleaned {purged} old ZIP(s)"
            st.caption(hint)

        if build_clicked:
            tmp_dir = _zip_tmp_dir(base_root)
            ts = time.strftime("%Y%m%d_%H%M%S")
            zip_name = f"{key_prefix}_{ts}.zip"
            zip_path = (tmp_dir / zip_name).resolve()

            selected_ids: set[str] = st.session_state.get(sel_key, set())
            files = _selected_paths_from_ids(id_to_path, selected_ids)

            ok, msg = _build_zip_file(files, zip_path=zip_path, arc_base=arc_base, root_guard=base_root)
            if ok:
                st.session_state[zip_key] = msg
                st.success(f"ZIP ready: {Path(msg).name}")
            else:
                st.error(msg)

        z = st.session_state.get(zip_key)
        if z and Path(z).exists():
            try:
                with open(z, "rb") as f:
                    st.download_button(
                        "⬇️ Download ZIP",
                        data=f,
                        file_name=Path(z).name,
                        mime="application/zip",
                        use_container_width=True,
                        key=f"{key_prefix}_download_zip",
                    )
            except Exception as e:
                st.error(f"Gagal download ZIP: {e}")


# =========================
# UI render
# =========================
def render(ctx):
    role = _role_from_ctx(ctx)
    is_admin = _is_admin(role)
    writable = _can_write(role)

    ws = _ws_root_from_ctx(ctx)
    if ws is None:
        st.error("Workspace user tidak ditemukan (ctx['paths']['user_root']).")
        return
    base_root = ws.resolve()
    _ensure_dir(base_root)

    st.subheader("📁 File Manager")
    #st.caption(f"Workspace aktif: `{base_root}`")

    # actions (admin/user saja)
    c_btn, c_clean, c_sp = st.columns([2, 2, 6])
    with c_btn:
        if st.button("🔄 Refresh", key="fm_refresh", use_container_width=True):
            st.rerun()
    with c_clean:
        if st.button("🧹 Clean temp", key="fm_clean", disabled=not writable, use_container_width=True):
            n = _clean_temp_mp4(base_root)
            st.toast(f"Cleaned {n} temp mp4", icon="🗑️" if n else "✨")
            time.sleep(0.4)
            st.rerun()

    # area: Logs hanya admin
    areas = ["Videos"]
    if is_admin:
        areas.append("Logs")
    area = "Videos" if not is_admin else st.selectbox("Area", areas, index=0, key="fm_area")

    # =========================
    # VIDEOS (HANYA OUTPUT FINAL DARI JOBSTORE)
    # =========================
    if area == "Videos":
        rows = _list_final_videos_from_jobs(base_root)
        if not rows:
            st.info("Belum ada video final dari Jobs.")
            st.caption("Jalankan render dari Control Panel / Auto Stock → lalu cek lagi di sini.")
            return

        topics = sorted(list({r["topic"] for r in rows if r["topic"]}), key=lambda x: x.lower())
        modes  = sorted(list({r["mode"] for r in rows if r["mode"]}), key=lambda x: x.lower())

        f1, f2, f3 = st.columns([1.2, 1.2, 2.6])
        with f1:
            pick_topic = st.selectbox("Topic", ["(all)"] + topics, index=0, key="fm_vid_topic")
        with f2:
            pick_mode = st.selectbox("Mode", ["(all)"] + modes, index=0, key="fm_vid_mode")
        with f3:
            st.caption("✅ Hanya video final dari JobStore (tidak scan folder out/).")

        def _ok(r):
            if pick_topic != "(all)" and r["topic"] != pick_topic:
                return False
            if pick_mode != "(all)" and r["mode"] != pick_mode:
                return False
            return True

        rows = [r for r in rows if _ok(r)]
        st.caption(f"🎞️ {len(rows)} video")

        # tampil ringkas: expander per video
        for r in rows[:60]:
            vp = Path(r["path"])
            badge = _mode_badge(r.get("mode", ""), r.get("source", ""))
            title = f"{badge} • 🎞️ {vp.name}"  # ✅ penanda di judul

            with st.expander(title, expanded=False):
                meta_line = f"job={r['job_id']} • status={r['status']} • topic={r['topic']} • mode={r['mode']}"
                st.caption(meta_line)

                cL, cR = st.columns([1, 1])  # ✅ video jadi ~½ lebar
                with cL:
                    st.video(str(vp))

                with cR:
                    st.caption("📌 Output")
                    st.code(vp.name)  # hanya filename

                    try:
                        with open(vp, "rb") as f:
                            st.download_button(
                                "⬇️ Download",
                                data=f,
                                file_name=vp.name,
                                mime="video/mp4",
                                key=f"fm_dl_{r['job_id']}",
                                use_container_width=True,
                            )
                    except Exception as e:
                        st.error(f"Gagal download: {e}")
        return

    # =========================
    # LOGS (ADMIN ONLY)
    # =========================
    elif area == "Logs":
        if not is_admin:
            st.info("Logs hanya untuk admin.")
            return

        # gabung 2 sumber log: workspace/logs dan workspace/jobs/logs
        sources: list[tuple[str, Path]] = []
        label_info: dict[str, dict] = {}

        # (A) legacy logs: base_root/logs/*.log
        l1 = (base_root / "logs").resolve()
        if l1.exists():
            for p in l1.glob("*.log"):
                dt = _fmt_dt_from_iso_or_mtime(None, p)
                label = f"[logs] {dt} • {p.name}"
                sources.append((label, p))
                label_info[label] = {
                    "kind": "logs",
                    "job_id": "",
                    "topic": "",
                    "mode": "",
                    "status": "",
                    "name": p.name,
                    "started": dt,
                }

        # (B) job logs: base_root/jobs/logs/<jobid>.log
        jobs_dir = (base_root / "jobs").resolve()
        jobs_logs = (jobs_dir / "logs").resolve()
        jobs_index = _read_jobs_index(jobs_dir)

        if jobs_logs.exists():
            for p in jobs_logs.glob("*.log"):
                job_id = p.stem  # <jobid>.log
                j = jobs_index.get(job_id) or {}
                meta = (j.get("meta") or {}) if isinstance(j.get("meta"), dict) else {}

                topic = str(meta.get("topic") or "").strip()
                mode = str(meta.get("mode") or "").strip()
                status = str(j.get("status") or "").strip()

                name = _job_display_name(meta)
                started = _fmt_dt_from_iso_or_mtime(str(j.get("started_at") or ""), p)

                # label ringkas tapi informatif
                # contoh: [jobs] 2026-02-27 22:40 • kebanyakan_oli...txt • AutoStock • automotif • running
                parts = [f"[jobs] {started}", name]
                if mode:
                    parts.append(mode)
                if topic:
                    parts.append(topic)
                if status:
                    parts.append(status)
                label = " • ".join(parts)

                sources.append((label, p))
                label_info[label] = {
                    "kind": "jobs",
                    "job_id": job_id,
                    "topic": topic,
                    "mode": mode,
                    "status": status,
                    "name": name,
                    "started": started,
                }

        # sort by file mtime newest first
        sources.sort(key=lambda x: x[1].stat().st_mtime if x[1].exists() else 0, reverse=True)

        if not sources:
            st.info("📂 Tidak ada log.")
            return

        labels = [lab for lab, _ in sources]
        lab_pick = st.selectbox("Log file", labels, key="fm_log_pick")
        f = dict(sources)[lab_pick]
        info = label_info.get(lab_pick, {})

        # header info (biar jelas ini log job apa)
        if info.get("kind") == "jobs":
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Job ID", info.get("job_id") or "-")
            c2.metric("Status", info.get("status") or "-")
            c3.metric("Topic", info.get("topic") or "-")
            c4.metric("Mode", info.get("mode") or "-")
            st.caption(f"🧾 Name: {info.get('name') or '-'} • Start: {info.get('started') or '-'}")
        else:
            st.caption(f"🧾 Log: {f.name} • Time: {info.get('started') or _fmt_dt_from_iso_or_mtime(None, f)}")

        cdl, _ = st.columns([1, 3])
        with cdl:
            try:
                with open(f, "rb") as dl:
                    st.download_button(
                        "⬇️ Download",
                        dl,
                        file_name=f.name,
                        key="fm_log_dl",
                        use_container_width=True,
                    )
            except Exception as e:
                st.error(f"Gagal download log: {e}")

        st.caption("Preview tail log (300 lines terakhir)")
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            tail = "\n".join(txt.splitlines()[-300:])
            st.code(tail, language="text")
        except Exception as e:
            st.error(f"Gagal baca log: {e}")
