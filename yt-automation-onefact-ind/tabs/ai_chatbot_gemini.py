from __future__ import annotations

import os
import copy
import queue
import time
import re
import threading
import json
import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from modules.gemini_client import GeminiClient
from modules import prompt_templates
from modules.chat_manager import (
    init_chat_state,
    add_user_message,
    add_assistant_message,
    clear_chat,
    get_messages_for_api,
)

load_dotenv()

LONG_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "cta": {"type": "string"},
        "video_project": {
            "type": "object",
            "properties": {
                "judul": {"type": "string"},
                "target_durasi": {"type": "string"},  # contoh "03:30" atau "00:45"
                "kategori": {"type": "string"},
                "tone": {"type": "string"},
            },
            "required": ["judul", "target_durasi", "kategori", "tone"],
        },
        "content_flow": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segmen": {"type": "string"},
                    "narasi": {"type": "string"},
                    "image_keyword": {"type": "string"},
                },
                "required": ["segmen", "narasi"],
            },
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "hook", "cta", "video_project", "content_flow", "keywords"],
}

FACT_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "seconds": {"type": "integer"},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "fact": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "string"}},
                    "cta": {"type": "string"},
                    "bg": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "variants": {"type": "array", "items": {"type": "string"}},
                            "avoid": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["query", "variants", "avoid"],
                    },
                },
                "required": ["title", "hook", "fact", "lines", "cta", "bg"],
            },
        },
    },
    "required": ["title", "seconds", "facts"],
}


def _get_user_paths(ctx: dict | None) -> dict:
    """workspace paths: portal ctx['paths'] kalau ada, fallback local."""
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict):
        p = ctx["paths"]
        return {k: Path(v) for k, v in p.items()}
    root = Path(".").resolve()
    return {"user_root": root, "contents": root / "contents", "logs": root / "logs"}


def _get_role(ctx: dict | None) -> str:
    return str((ctx or {}).get("auth_role") or "")


def _get_profile_api_key(ctx: dict | None) -> str:
    prof = (ctx or {}).get("profile") or {}
    api = (prof.get("api_keys") or {}) if isinstance(prof, dict) else {}
    key = str(api.get("gemini") or "").strip()
    if key:
        return key
    return str(os.getenv("GEMINI_API_KEY", "")).strip()


def _context_prefix(active_text: str, context_mode: str, custom_instruction: str) -> str:
    parts = []
    if custom_instruction.strip():
        parts.append(f"Instruksi tambahan:\n{custom_instruction.strip()}")

    if active_text.strip():
        if context_mode == "Full Content":
            parts.append("Konten referensi:\n" + active_text.strip())
        elif context_mode == "500 Karakter Pertama":
            parts.append("Konten referensi (500 char pertama):\n" + active_text.strip()[:500])

    return "\n\n---\n\n".join(parts).strip() if parts else ""


def _init_async_state() -> None:
    if "gemini_queue" not in st.session_state:
        st.session_state["gemini_queue"] = queue.Queue()
    if "gemini_pending" not in st.session_state:
        st.session_state["gemini_pending"] = False
    if "gemini_last_error" not in st.session_state:
        st.session_state["gemini_last_error"] = ""
    if "tpl_done_job_id" not in st.session_state:
        st.session_state["tpl_done_job_id"] = ""
    if "tpl_text" not in st.session_state:
        st.session_state["tpl_text"] = ""
    if "tpl_file" not in st.session_state:
        st.session_state["tpl_file"] = ""
    if "tpl_error" not in st.session_state:
        st.session_state["tpl_error"] = ""
    if "tpl_pending" not in st.session_state:
        st.session_state["tpl_pending"] = False
    if "tpl_auto_refresh" not in st.session_state:
        st.session_state["tpl_auto_refresh"] = True
    if "tpl_job_id" not in st.session_state:
        st.session_state["tpl_job_id"] = ""
    if "tpl_started_at" not in st.session_state:
        st.session_state["tpl_started_at"] = 0.0


def _start_gemini_job(api_key: str, temperature: float, max_tokens: int, prefix: str = "") -> None:
    """Thread job: result masuk queue. Jangan st.rerun dari thread."""
    q: queue.Queue = st.session_state["gemini_queue"]
    st.session_state["gemini_pending"] = True
    st.session_state["gemini_last_error"] = ""

    messages = get_messages_for_api()
    if prefix:
        messages = [{"role": "user", "content": prefix}] + messages

    def _task():
        try:
            client = GeminiClient(api_key)
            result = client.generate(
                messages,
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            q.put(("ok", str(result.get("text", "")).strip()))
        except Exception as e:
            q.put(("err", f"{e}"))

    threading.Thread(target=_task, daemon=True).start()


def _count_words(s: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9']+", (s or "").strip(), flags=re.UNICODE))


def _fact_words(f: dict) -> int:
    lines = f.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    text = " ".join(
        [
            str(f.get("hook") or ""),
            str(f.get("fact") or ""),
            " ".join([str(x or "") for x in lines]),
            str(f.get("cta") or ""),
        ]
    )
    return _count_words(text)


def _build_short_prompt(topic: str, title: str, seconds: int, n_facts: int, target_seconds: int | None) -> str:
    # Kecepatan TTS (kata/detik) -> sesuaikan dengan engine kamu
    wps = float(os.getenv("TTS_WPS", "1.45"))

    if target_seconds:
        target_words = int(round(float(target_seconds) * wps))
        # Range 25–30 detik: min = 0.83*target, max = target
        min_words = int(round(target_words * 0.83))
        max_words = int(target_words)

        dur_rules = f"""
ATURAN DURASI (WAJIB):
- Kecepatan asumsi voiceover ≈ {wps:.2f} kata/detik.
- Target durasi per FACT: 25–{int(target_seconds)} detik.
- Total kata per FACT WAJIB {min_words}–{max_words} kata (jangan lebih dari {max_words}).
- `lines`: 3 kalimat, masing-masing 7–9 kata.
- `hook` 6–8 kata, `fact` 10–14 kata, `cta` 6–8 kata.
""".strip()
    else:
        dur_rules = ""

    return f"""
Kamu adalah generator template konten video pendek.
Output HARUS JSON VALID saja (tanpa markdown, tanpa penjelasan).

ATURAN JSON (WAJIB):
- Output JSON valid saja, tanpa ``` dan tanpa teks lain.
- Jangan menulis karakter kutip ganda (") di dalam isi kalimat (konten string).
  Jika perlu kutip, pakai kutip tunggal (') atau parafrase.
- Jangan buat newline literal di dalam string JSON.

{dur_rules}

Buat JSON dengan format:
- title: "{title}"
- seconds: {int(seconds)}
- facts: array berisi TEPAT {int(n_facts)} item

Aturan facts[i]:
- title, hook, fact, cta: bahasa Indonesia, singkat, catchy
- lines: array TEPAT 3 kalimat (7–9 kata per kalimat)
- bg.query: keyword English untuk stock image/video (realistic photo)
- bg.variants: 2 string
- bg.avoid: selalu ["portrait","person","selfie"]

Topik utama: {topic}
""".strip()

def _tighten_fact_schema(base_schema: dict, exact_facts: int) -> dict:
    """
    Bikin schema khusus per-batch agar:
    - facts harus tepat sejumlah `exact_facts`
    - lines harus tepat 3
    - bg.variants harus tepat 2
    """
    s = copy.deepcopy(base_schema)
    facts = s.get("properties", {}).get("facts", {})
    if isinstance(facts, dict):
        facts["minItems"] = int(exact_facts)
        facts["maxItems"] = int(exact_facts)

        item = facts.get("items", {})
        if isinstance(item, dict):
            props = item.get("properties", {})

            lines = props.get("lines")
            if isinstance(lines, dict):
                lines["minItems"] = 3
                lines["maxItems"] = 3

            bg = props.get("bg")
            if isinstance(bg, dict):
                bgp = bg.get("properties", {})
                variants = bgp.get("variants")
                if isinstance(variants, dict):
                    variants["minItems"] = 2
                    variants["maxItems"] = 2

    return s


def _start_template_job(
    api_key: str,
    prompt: str,
    max_tokens: int,
    schema: dict,
    out_dir: Path,
    topic: str,
    kind: str = "short",
    target_seconds: int | None = None,
) -> None:
    q: queue.Queue = st.session_state["gemini_queue"]

    job_id = str(time.time_ns())
    st.session_state["tpl_job_id"] = job_id
    st.session_state["tpl_done_job_id"] = ""
    st.session_state["tpl_started_at"] = time.time()

    st.session_state["tpl_pending"] = True
    st.session_state["tpl_error"] = ""
    st.session_state["tpl_text"] = ""
    st.session_state["tpl_file"] = ""

    def _task():
        try:
            client = GeminiClient(api_key, timeout=120)

            prompt_eff = prompt

            if kind == "short" and target_seconds:
                target_words = int(round(float(target_seconds) * 3.2))
                min_words = int(round(target_words * 0.92))

                prompt_eff = prompt.strip() + f"""

ATURAN DURASI (WAJIB):
- Target durasi voiceover per FACT ≈ {int(target_seconds)} detik.
- Total kata per FACT (gabungan hook + fact + semua lines + cta) IDEAL {target_words} kata (±10%).
- MINIMAL {min_words} kata per FACT (jangan kurang).
- Jangan pakai kalimat super pendek. Setiap item `lines` buat 14–18 kata per kalimat.
- `hook` 10–14 kata, `fact` 18–28 kata, `cta` 10–14 kata.
""".strip()

            last_text = ""
            last_data = None

            for attempt in range(2):
                result = client.generate(
                    prompt_eff,
                    temperature=0.2,
                    max_tokens=max_tokens,
                    force_json=True,
                    json_schema=schema,
                )
                last_text = str(result.get("text", "")).strip()
                data = json.loads(last_text)
                last_data = data

                if kind == "short" and target_seconds and isinstance(data, dict):
                    facts = data.get("facts") or []
                    if isinstance(facts, list) and facts:
                        ws = [_fact_words(f) for f in facts if isinstance(f, dict)]
                        avg_words = (sum(ws) / max(1, len(ws))) if ws else 0

                        target_words = int(round(float(target_seconds) * 3.2))
                        min_words = int(round(target_words * 0.92))

                        if avg_words < min_words:
                            prompt_eff = prompt.strip() + f"""

PERBAIKI: output sebelumnya terlalu pendek.
WAJIB: rata-rata kata per FACT minimal {min_words} dan ideal {target_words}±10%.
Tetap patuhi schema JSON yang sama. Output JSON valid saja.
""".strip()
                            continue

                break

            q.put(
                (
                    "tpl_ok",
                    {
                        "job_id": job_id,
                        "text": last_text,
                        "out_dir": str(out_dir),
                        "topic": topic,
                        "kind": kind,
                    },
                )
            )

        except Exception:
            import traceback

            q.put(("tpl_err", {"job_id": job_id, "err": traceback.format_exc()}))

    threading.Thread(target=_task, daemon=True).start()


def _start_short_template_job(
    api_key: str,
    topic: str,
    title: str,
    seconds: int,
    n_facts: int,
    schema: dict,
    out_dir: Path,
    target_seconds: int | None = None,
) -> None:
    q: queue.Queue = st.session_state["gemini_queue"]

    job_id = str(time.time_ns())
    st.session_state["tpl_job_id"] = job_id
    st.session_state["tpl_done_job_id"] = ""
    st.session_state["tpl_started_at"] = time.time()
    st.session_state["tpl_pending"] = True
    st.session_state["tpl_error"] = ""
    st.session_state["tpl_text"] = ""
    st.session_state["tpl_file"] = ""

    def _task():
        try:
            client = GeminiClient(api_key, timeout=180)

            # batch size: kalau seconds besar, kecilkan batch biar gak kepotong
            batch = 2 if int(seconds) >= 25 else 4

            all_facts: list[dict] = []
            remaining = int(n_facts)

            while remaining > 0:
                take = min(batch, remaining)
                prompt_batch = _build_short_prompt(topic, title, seconds, take, target_seconds)
                schema_batch = _tighten_fact_schema(schema, take)

                # naikin max_tokens per batch (lebih aman)
                max_tokens_batch = 2500 if int(seconds) >= 25 else 1800

                # ====== REPLACE BLOK RETRY LAMA DENGAN INI ======

                wps = float(os.getenv("TTS_WPS", "1.45"))
                target_words = int(round(float(target_seconds or seconds) * wps))
                min_words = int(round(target_words * 0.90))  # ~25 detik
                max_words = int(round(target_words * 1.05))    # ~30 detik (jangan lewat)

                prompt_eff = prompt_batch
                ok_data = None
                last_ex = None

                for tr in range(3):
                    try:
                        result = client.generate(
                            prompt_eff,
                            temperature=0.0,
                            max_tokens=max_tokens_batch,
                            force_json=True,
                            json_schema=schema_batch,
                        )
                        text = str(result.get("text", "")).strip()
                        data = json.loads(text)

                        facts = data.get("facts") or []
                        ws = [_fact_words(f) for f in facts if isinstance(f, dict)]
                        max_fact_words = max(ws) if ws else 0
                        min_fact_words = min(ws) if ws else 0

                        if max_fact_words > max_words:
                            prompt_eff = prompt_batch + f"""

            PERBAIKI: output sebelumnya TERLALU PANJANG.
            WAJIB: total kata per FACT antara {min_words}–{max_words}.
            Ringkas hook/fact/lines/cta tanpa mengubah format schema. Output JSON saja.
            """.strip()
                            continue

                        if min_fact_words < min_words:
                            prompt_eff = prompt_batch + f"""

            PERBAIKI: output sebelumnya TERLALU PENDEK.
            WAJIB: total kata per FACT antara {min_words}–{max_words}.
            Tambah sedikit detail, tetap singkat. Output JSON saja.
            """.strip()
                            continue

                        ok_data = data
                        break

                    except Exception as ex:
                        last_ex = ex
                        time.sleep(0.6)
                # ====== END REPLACE ======

                for f in facts:
                    if isinstance(f, dict):
                        all_facts.append(f)

                remaining -= take

            final = {"title": title, "seconds": int(seconds), "facts": all_facts[: int(n_facts)]}
            q.put(
                (
                    "tpl_ok",
                    {
                        "job_id": job_id,
                        "text": json.dumps(final, ensure_ascii=False, indent=2),
                        "out_dir": str(out_dir),
                        "topic": topic,
                        "kind": "short",
                    },
                )
            )
        except Exception:
            import traceback

            q.put(("tpl_err", {"job_id": job_id, "err": traceback.format_exc()}))

    threading.Thread(target=_task, daemon=True).start()


def _drain_queue() -> None:
    q: queue.Queue = st.session_state["gemini_queue"]
    changed = False

    while True:
        try:
            status, payload = q.get_nowait()
        except Exception:
            break

        if status == "ok":
            if payload:
                add_assistant_message(payload)
            st.session_state["gemini_pending"] = False
            changed = True
            continue

        if status == "err":
            st.session_state["gemini_last_error"] = str(payload)
            st.session_state["gemini_pending"] = False
            changed = True
            continue

        if status == "tpl_ok":
            job_id = (payload or {}).get("job_id", "")
            if not job_id or job_id != st.session_state.get("tpl_job_id"):
                continue

            if job_id == st.session_state.get("tpl_done_job_id"):
                st.session_state["tpl_pending"] = False
                changed = True
                continue

            st.session_state["tpl_done_job_id"] = job_id

            text = (payload or {}).get("text", "").strip()
            kind = ((payload or {}).get("kind") or "short").strip().lower()
            topic = ((payload or {}).get("topic") or "template").strip() or "template"
            out_dir = Path((payload or {}).get("out_dir") or ".")
            out_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"long_{ts}.json" if kind == "long" else f"{topic}_{ts}.json"
            out_path = out_dir / filename

            try:
                data = json.loads(text)
                out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                st.session_state["tpl_text"] = json.dumps(data, ensure_ascii=False, indent=2)
                st.session_state["tpl_file"] = str(out_path)
                st.session_state["tpl_error"] = ""
            except Exception as e:
                st.session_state["tpl_text"] = text
                st.session_state["tpl_file"] = ""
                st.session_state["tpl_error"] = f"JSON invalid / save failed: {e}"

            st.session_state["tpl_pending"] = False
            changed = True
            continue

        if status == "tpl_err":
            job_id = (payload or {}).get("job_id", "")
            if job_id and job_id != st.session_state.get("tpl_job_id"):
                continue
            st.session_state["tpl_error"] = str((payload or {}).get("err") or payload)
            st.session_state["tpl_pending"] = False
            changed = True
            continue

    if changed:
        st.rerun()


def render(ctx: dict | None = None) -> None:
    paths = _get_user_paths(ctx)
    generated_dir = paths["contents"] / "generated"
    templates_dir_user = paths["user_root"] / "templates"
    generated_dir.mkdir(parents=True, exist_ok=True)
    templates_dir_user.mkdir(parents=True, exist_ok=True)

    st.header("🤖 AI Chatbot (Gemini Assistant)")

    role = _get_role(ctx)
    if role == "demo":
        st.warning("Akun DEMO: fitur AI Chatbot tidak tersedia. Upgrade ke role `user` untuk akses.")
        return

    init_chat_state()
    _init_async_state()

    _drain_queue()

    if st.session_state.get("tpl_pending"):
        with st.container():
            c1, c2, c3 = st.columns([2, 2, 6])
            with c1:
                st.caption("🔄 Auto Refresh (Template)")
            with c2:
                st.session_state["tpl_auto_refresh"] = st.toggle(
                    "On",
                    value=bool(st.session_state.get("tpl_auto_refresh", True)),
                    key="tpl_auto_refresh_toggle",
                    label_visibility="collapsed",
                )
            with c3:
                elapsed = 0.0
                try:
                    elapsed = time.time() - float(st.session_state.get("tpl_started_at") or time.time())
                except Exception:
                    pass
                st.caption(f"Status: pending • elapsed {elapsed:.1f}s")

        if st.session_state.get("tpl_auto_refresh", True):
            time.sleep(0.8)
            st.rerun()

    default_key = _get_profile_api_key(ctx)
    if "gemini_api_key_override" not in st.session_state:
        st.session_state["gemini_api_key_override"] = default_key

    with st.expander("🔑 API Config", expanded=False):
        api_key = st.text_input(
            "Gemini API Key (temporary override)",
            type="password",
            value=st.session_state.get("gemini_api_key_override", default_key),
        )

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Use this key", use_container_width=True):
                st.session_state["gemini_api_key_override"] = api_key.strip()
                st.success("Key set (session only).")
        with c2:
            if st.button("Reset to profile/env", use_container_width=True):
                st.session_state["gemini_api_key_override"] = default_key
                st.success("Reset OK.")

        if st.button("Test Connection", use_container_width=True):
            k = st.session_state.get("gemini_api_key_override", "").strip()
            if not k:
                st.error("API key kosong.")
            else:
                try:
                    client = GeminiClient(k)
                    client.generate("ping", max_tokens=5)
                    st.success("Connected")
                except Exception as e:
                    st.error(f"Error connecting to Gemini: {e}")

    api_key_use = st.session_state.get("gemini_api_key_override", "").strip()

    with st.expander("🧪 Debug (GeminiClient)", expanded=False):
        import modules.gemini_client as gc

        st.write("gemini_client file:", gc.__file__)
        st.write("has re:", "re" in gc.__dict__)

    if not api_key_use:
        st.warning("Masukkan Gemini API Key terlebih dahulu (via Portal Profile atau API Config di atas).")
        return

    st.subheader("⚙️ Model Settings")
    temperature = st.slider("Temperature", 0.0, 1.0, 0.7, key="gem_temp")
    max_tokens = st.slider("Max Tokens", 256, 4096, 1024, key="gem_max_tokens")

    st.subheader("📄 Context Integration")
    context_mode = st.radio(
        "Kirim konten:",
        ["Full Content", "500 Karakter Pertama", "Tanpa Konten"],
        key="gem_ctx_mode",
        horizontal=True,
    )
    custom_instruction = st.text_area("Custom Instruction (opsional)", key="gem_custom_instr")

    active_text = st.session_state.get("active_content_text", "") or ""
    active_path = st.session_state.get("active_content_path", "") or ""
    if active_path:
        st.caption(f"File aktif: {active_path}")
        st.code(active_text[:300])

    st.subheader("💬 Chat")

    if st.session_state.get("gemini_last_error"):
        st.error(f"Gemini error: {st.session_state['gemini_last_error']}")

    if st.session_state.get("gemini_pending"):
        st.info("Gemini sedang berpikir...")

    for msg in st.session_state["gemini_chat_messages"]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_prompt = st.chat_input("Ketik pesan ke Gemini...")
    if user_prompt and not st.session_state.get("gemini_pending"):
        add_user_message(user_prompt)

        prefix = ""
        if context_mode != "Tanpa Konten":
            prefix = _context_prefix(active_text, context_mode, custom_instruction)

        _start_gemini_job(
            api_key=api_key_use,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            prefix=prefix,
        )
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Clear Chat", use_container_width=True):
            clear_chat()
            st.rerun()
    with col2:
        st.caption("")

    st.subheader("🧾 Generate Content (JSON Template)")

    with st.expander("Buat template JSON LONG video (max 8 chapter)", expanded=False):
        topic_long = st.text_input("Topic / nama tema", value="misteri_laut")
        n_chapters = st.number_input("Jumlah chapter (max 8)", min_value=1, max_value=8, value=6, step=1)

        target_dur = st.text_input("Target durasi (MM:SS)", value="04:00")
        kategori = st.text_input("Kategori", value="Edukasi / Fakta Unik")
        tone = st.text_input("Tone", value="Misterius & Menegangkan")

        extra_keywords = st.text_input(
            "Keywords tambahan (opsional, pisahkan koma)",
            value="",
            help="Contoh: laut dalam, zona hadal, palung mariana",
        )

        prompt_long = f"""
Kamu adalah generator template video LONG (3-5 menit).
Output HARUS JSON VALID saja (tanpa markdown, tanpa penjelasan).

Buat JSON dengan format:
- title: judul utama
- hook: 1 kalimat hook yang memancing
- cta: 1 kalimat CTA
- video_project:
    - judul: sama dengan title
    - target_durasi: "{target_dur}"
    - kategori: "{kategori}"
    - tone: "{tone}"
- content_flow: array berisi TEPAT {int(n_chapters)} chapter
    Aturan tiap chapter:
    - segmen: judul segmen (singkat)
    - narasi: 2-4 kalimat bahasa Indonesia, mengalir, cocok narator
    - image_keyword: keyword ENGLISH untuk pencarian gambar (realistic photo), 3-6 kata
- keywords: 5-10 kata/phrase (Indonesia)

Topik utama: {topic_long}
Keyword tambahan (opsional): {extra_keywords}
""".strip()

        if st.button(
            "✨ Generate JSON LONG Template",
            use_container_width=True,
            disabled=st.session_state.get("tpl_pending", False),
            key="btn_gen_json_long_template",
        ):
            _start_template_job(
                api_key=api_key_use,
                prompt=prompt_long,
                max_tokens=4000,
                schema=LONG_TEMPLATE_SCHEMA,
                out_dir=templates_dir_user,
                topic=(topic_long.strip() or "long"),
                kind="long",
            )
            st.rerun()

    with st.expander("Buat template JSON SHORT video", expanded=False):
        topic = st.text_input("Topic / nama file", value="otomotif", help="akan jadi nama file json")
        n_facts = st.number_input(
            "Jumlah konten (facts) dalam 1 file", min_value=1, max_value=50, value=10, step=1
        )
        seconds = st.number_input("Durasi (seconds)", min_value=10, max_value=120, value=30, step=1)
        title = st.text_input("Judul template", value=f"Fakta {topic.title()}")

        prompt = f"""
Kamu adalah generator template konten video pendek.
Output HARUS JSON VALID saja (tanpa markdown, tanpa penjelasan).

Buat JSON dengan format:
- title: "{title}"
- seconds: {int(seconds)}
- facts: array berisi TEPAT {int(n_facts)} item

Aturan facts[i]:
- title, hook, fact, cta: bahasa Indonesia, singkat, catchy
- lines: array TEPAT 3 kalimat (masing-masing 14–18 kata, jangan terlalu pendek)
- bg.query: keyword English untuk stock image/video (realistic photo)
- bg.variants: 2 string
- bg.avoid: selalu ["portrait","person","selfie"]

ATURAN JSON (WAJIB):
- Output harus JSON valid saja (tanpa ```).
- Jangan menulis karakter kutip ganda (") di dalam isi kalimat (konten string).
  Jika perlu kutip, pakai kutip tunggal (') atau parafrase.
- Jangan membuat newline literal di dalam string JSON.

Topik utama: {topic}
""".strip()

        if st.button(
            "✨ Generate JSON Template",
            use_container_width=True,
            disabled=st.session_state.get("tpl_pending", False),
            key="btn_gen_json_template",
        ):
            _start_short_template_job(
                api_key=api_key_use,
                topic=topic.strip() or "template",
                title=title,
                seconds=int(seconds),
                n_facts=int(n_facts),
                schema=FACT_TEMPLATE_SCHEMA,
                out_dir=templates_dir_user,
                target_seconds=int(seconds),
            )
            st.rerun()

        if st.session_state.get("tpl_pending"):
            st.info("Gemini sedang membuat template JSON...")

        if st.session_state.get("tpl_error"):
            st.error(st.session_state["tpl_error"])

        if st.session_state.get("tpl_text"):
            st.code(st.session_state["tpl_text"], language="json")

        if st.session_state.get("tpl_file"):
            p = Path(st.session_state["tpl_file"])
            if p.exists():
                st.download_button(
                    "⬇️ Download JSON",
                    data=p.read_bytes(),
                    file_name=p.name,
                    mime="application/json",
                    use_container_width=True,
                    key="btn_dl_json_template",
                )

    if st.session_state.get("tpl_pending"):
        st.info("Gemini sedang membuat template JSON...")

    if st.session_state.get("tpl_error"):
        st.error(st.session_state["tpl_error"])

    tpl_text = (st.session_state.get("tpl_text") or "").strip()
    if tpl_text:
        st.code(tpl_text, language="json")

    tpl_file = (st.session_state.get("tpl_file") or "").strip()
    if tpl_file:
        p = Path(tpl_file)
        if p.exists():
            st.success(f"Saved: `{p.name}`")
            st.caption(str(p))
            st.download_button(
                "⬇️ Download JSON (Last)",
                data=p.read_bytes(),
                file_name=p.name,
                mime="application/json",
                use_container_width=True,
                key="btn_dl_json_template_last",
            )
        else:
            st.warning(f"tpl_file tercatat tapi file tidak ditemukan: {tpl_file}")

    with st.expander("Hasil Template Terakhir", expanded=False):
        if (not tpl_text) and (not tpl_file):
            try:
                cand = sorted(templates_dir_user.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
                if cand:
                    lastp = cand[0]
                    st.info(f"Fallback latest file: `{lastp.name}`")
                    st.code(lastp.read_text(encoding="utf-8", errors="ignore")[:8000], language="json")
                    st.download_button(
                        "⬇️ Download JSON (Fallback Latest)",
                        data=lastp.read_bytes(),
                        file_name=lastp.name,
                        mime="application/json",
                        use_container_width=True,
                        key="btn_dl_json_template_fallback",
                    )
            except Exception:
                pass


    with st.expander("⚡ Quick Actions", expanded=False):
        def quick_action(prompt_fn):
            if st.session_state.get("gemini_pending"):
                st.warning("Masih ada request yang berjalan. Tunggu selesai dulu.")
                return

            if not active_text.strip():
                st.warning("Tidak ada active content.")
                return

            text = active_text if context_mode == "Full Content" else active_text[:500]
            prompt = prompt_fn(text)

            if custom_instruction.strip():
                prompt += f"\n\nInstruksi tambahan:\n{custom_instruction.strip()}"

            add_user_message(prompt)

            _start_gemini_job(
                api_key=api_key_use,
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                prefix="",
            )
            st.rerun()

        qa_cols = st.columns(3)
        qa_cols[0].button("Generate Hook", use_container_width=True, on_click=lambda: quick_action(prompt_templates.generate_hook_prompt))
        qa_cols[1].button("Generate Script", use_container_width=True, on_click=lambda: quick_action(prompt_templates.generate_script_prompt))
        qa_cols[2].button("Improve Content", use_container_width=True, on_click=lambda: quick_action(prompt_templates.viral_rewrite_prompt))

        qa_cols2 = st.columns(3)
        qa_cols2[0].button("Generate SEO Title", use_container_width=True, on_click=lambda: quick_action(prompt_templates.seo_title_prompt))
        qa_cols2[1].button("Rewrite to Viral Style", use_container_width=True, on_click=lambda: quick_action(prompt_templates.viral_rewrite_prompt))
        qa_cols2[2].button("Generate Description + CTA", use_container_width=True, on_click=lambda: quick_action(prompt_templates.description_cta_prompt))

        msgs = st.session_state.get("gemini_chat_messages", [])
        if msgs:
            last_msg = msgs[-1]
            if last_msg.get("role") == "assistant" and last_msg.get("content"):
                if st.button("💾 Save output to file", use_container_width=True):
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    fname = f"gemini_output_{ts}.txt"
                    fpath = generated_dir / fname
                    fpath.write_text(str(last_msg["content"]), encoding="utf-8")
                    st.success(f"Disimpan ke {fpath}")


