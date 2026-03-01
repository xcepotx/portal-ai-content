from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime, timedelta
import streamlit as st

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

def _count_images(out_dir: Path) -> int:
    # exclude noisy dirs
    exclude = {"frames", "_cuts", "exports"}
    n = 0
    if not out_dir.exists():
        return 0
    for p in out_dir.rglob("*"):
        if p.is_dir():
            continue
        if any(part in exclude for part in p.parts):
            continue
        if p.suffix.lower() in IMG_EXT:
            n += 1
    return n

def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _sum_gemini_usage(job_dir: Path) -> tuple[int, int, int, int]:
    """
    returns (calls, prompt_tokens, output_tokens, total_tokens)
    """
    p = job_dir / "gemini_usage.jsonl"
    if not p.exists():
        return (0, 0, 0, 0)

    calls = 0
    pt = 0
    ot = 0
    tt = 0
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)
            calls += 1
            pt += int(j.get("prompt_token_count") or 0)
            ot += int(j.get("candidates_token_count") or 0)
            tt += int(j.get("total_token_count") or 0)
    except Exception:
        pass
    return (calls, pt, ot, tt)

def _job_time(job_dir: Path) -> float:
    # best effort from folder name "job_YYYYMMDD_HHMMSS"
    name = job_dir.name
    if name.startswith("job_"):
        s = name[4:]
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(s, fmt).timestamp()
            except Exception:
                pass
    try:
        return job_dir.stat().st_mtime
    except Exception:
        return 0.0

def render(ctx: dict, services) -> None:
    st.header("📊 Admin Analytics")

    if ctx.get("auth_role") != "admin":
        st.error("Hanya admin yang boleh akses halaman ini.")
        return

    root = Path(services.workspace.root_dir).resolve()

    # filters
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        days = st.slider("Scan last N days", 1, 90, 14, 1)
    with c2:
        include_inactive = st.checkbox("Include inactive users", value=False)
    with c3:
        st.caption("Catatan: Token usage mulai tercatat setelah patch gemini_client + spawn_job aktif.")

    since_ts = (datetime.now() - timedelta(days=int(days))).timestamp()

    from collections import defaultdict
    import pandas as pd

    daily = defaultdict(lambda: {
        "jobs": 0,
        "done": 0,
        "error": 0,
        "images": 0,
        "gemini_calls": 0,
        "total_tokens": 0,
    })

    users = services.user_store.list_users()
    if not include_inactive:
        users = [u for u in users if bool(u.get("active", True))]

    rows = []
    totals = {
        "jobs": 0,
        "done": 0,
        "error": 0,
        "images": 0,
        "calls": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }

    for u in users:
        username = u.get("username")
        if not username:
            continue
        user_root = (root / username).resolve()
        out_root = user_root / "out"
        if not out_root.exists():
            continue

        jobs = 0
        done = 0
        err = 0
        images = 0

        calls = 0
        pt = 0
        ot = 0
        tt = 0

        # scan job dirs: out/<job_type>/job_*
        for job_type_dir in sorted([p for p in out_root.iterdir() if p.is_dir()]):
            for jd in sorted(job_type_dir.glob("job_*")):
                if not jd.is_dir():
                    continue
                if _job_time(jd) < since_ts:
                    continue

                jobs += 1
                prog = _read_json(jd / "progress.json")
                stt = str(prog.get("status") or "").lower().strip()
                if stt == "done":
                    done += 1
                elif stt in ("error", "stopped", "cancelled", "canceled"):
                    err += 1

                images += _count_images(jd / "outputs")

                c, ptt, ott, ttt = _sum_gemini_usage(jd)
                calls += c
                pt += ptt
                ot += ott
                tt += ttt

                day_key = datetime.fromtimestamp(_job_time(jd)).strftime("%Y-%m-%d")

                daily[day_key]["jobs"] += 1
                if stt == "done":
                    daily[day_key]["done"] += 1
                elif stt in ("error", "stopped", "cancelled", "canceled"):
                    daily[day_key]["error"] += 1

                img_count = _count_images(jd / "outputs")
                daily[day_key]["images"] += img_count

                c, ptt, ott, ttt = _sum_gemini_usage(jd)
                daily[day_key]["gemini_calls"] += c
                daily[day_key]["total_tokens"] += ttt

        if jobs == 0 and not include_inactive:
            continue

        success_rate = (done / jobs * 100.0) if jobs > 0 else 0.0

        rows.append({
            "user": username,
            "role": u.get("role", ""),
            "active": bool(u.get("active", True)),
            "jobs": jobs,
            "done": done,
            "error": err,
            "success_rate_%": round(success_rate, 1),
            "images_generated": images,
            "gemini_calls": calls,
            "prompt_tokens": pt,
            "output_tokens": ot,
            "total_tokens": tt,
        })

        totals["jobs"] += jobs
        totals["done"] += done
        totals["error"] += err
        totals["images"] += images
        totals["calls"] += calls
        totals["prompt_tokens"] += pt
        totals["output_tokens"] += ot
        totals["total_tokens"] += tt

    # summary
    st.subheader("Summary (filtered)")

    st.divider()
    st.subheader("Tren harian")

    if daily:
        df_daily = pd.DataFrame(
            [{"day": k, **v} for k, v in sorted(daily.items(), key=lambda x: x[0])]
        ).set_index("day")

        c1, c2 = st.columns(2)
        with c1:
            st.caption("Jobs / Done / Error per hari")
            st.line_chart(df_daily[["jobs", "done", "error"]])

        with c2:
            st.caption("Images generated per hari")
            st.bar_chart(df_daily[["images"]])

        c3, c4 = st.columns(2)
        with c3:
            st.caption("Gemini calls per hari")
            st.line_chart(df_daily[["gemini_calls"]])

        with c4:
            st.caption("Total tokens per hari")
            st.line_chart(df_daily[["total_tokens"]])

    else:
        st.info("Belum ada data untuk range hari yang dipilih.")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Total jobs", totals["jobs"])
    s2.metric("Done", totals["done"])
    s3.metric("Images generated", totals["images"])
    s4.metric("Gemini calls", totals["calls"])

    t1, t2, t3 = st.columns(3)
    t1.metric("Prompt tokens", totals["prompt_tokens"])
    t2.metric("Output tokens", totals["output_tokens"])
    t3.metric("Total tokens", totals["total_tokens"])

    st.divider()
    st.subheader("Per-user")
    rows = sorted(rows, key=lambda r: (r["total_tokens"], r["jobs"]), reverse=True)
    st.dataframe(rows, use_container_width=True, hide_index=True)
