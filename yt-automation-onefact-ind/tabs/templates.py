import os
import sys
import time
import json
import re
import shlex
import html
import subprocess
from pathlib import Path

import streamlit as st


def _ws_root(ctx) -> Path:
    if isinstance(ctx, dict):
        paths = ctx.get("paths") or {}
        user_root = paths.get("user_root")
        if user_root:
            return Path(user_root).expanduser().resolve()
    # fallback standalone: repo root (../)
    return Path(__file__).resolve().parents[1]


def _resolve_repo_root(ctx=None) -> Path:
    """
    Cari repo yt-automation yang punya main.py.
    Prioritas:
    1) ctx['paths']['repo_root'] / ctx['paths']['automation_root'] (kalau ada)
    2) parents dari file ini
    3) sibling dari CWD: ../yt-automation*/main.py (kasus portal)
    """
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict):
        for k in ("repo_root", "automation_root", "yt_root"):
            v = ctx["paths"].get(k)
            if v:
                p = Path(v).expanduser().resolve()
                if (p / "main.py").exists():
                    return p

    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "main.py").exists():
            return p

    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        for mp in base.glob("../yt-automation*/main.py"):
            if mp.exists():
                return mp.parent.resolve()

    return here.parents[1].resolve()


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        name = "new_script.json"
    if not name.lower().endswith(".json"):
        name += ".json"
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
    if not name.lower().endswith(".json"):
        name += ".json"
    return name


def _list_json_files(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.name.lower().endswith(".json")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in files]


def _list_topics(contents_dir: Path) -> list[str]:
    if not contents_dir.exists():
        return []
    out = [p.name for p in contents_dir.iterdir() if p.is_dir()]
    out.sort(key=lambda x: x.lower())
    return out


def _run_streaming(cmd_args: list[str], cwd: Path, env: dict, log_path: Path) -> tuple[int, list[str]]:
    proc = subprocess.Popen(
        cmd_args,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    MAX_LINES = 280
    log_ph = st.empty()

    def _render(lines: list[str]):
        safe = [html.escape(x) for x in lines]
        log_ph.markdown(
            f"""
            <div style="
                height:260px; overflow-y:auto;
                border-radius:14px; padding:12px 14px;
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                box-shadow: 0 10px 30px rgba(0,0,0,0.20);
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono','Courier New', monospace;
                font-size: 11.5px; line-height: 1.5;
            ">
            {"<br>".join(safe)}
            </div>
            """,
            unsafe_allow_html=True,
        )

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"=== JOB START {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        f.write("CWD: " + str(cwd) + "\n")
        f.write("CMD: " + shlex.join(cmd_args) + "\n")
        f.write("===========================================\n")

        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line and proc.poll() is not None:
                break
            if not line:
                continue

            for p in line.replace("\r", "\n").split("\n"):
                p = p.strip()
                if not p:
                    continue
                logs.append(p)
                if len(logs) > MAX_LINES:
                    logs = logs[-MAX_LINES:]
                f.write(p + "\n")
                _render(logs)

        rc = proc.wait()
        f.write(f"=== JOB END {time.strftime('%Y-%m-%d %H:%M:%S')} rc={rc} ===\n")

    return rc, logs


def render(ctx):
    st.header("📝 JSON Script Editor (Raw Mode)")

    ws_root = _ws_root(ctx)
    templates_dir = (ws_root / "templates").resolve()
    templates_dir.mkdir(parents=True, exist_ok=True)

    repo_root = _resolve_repo_root(ctx)
    main_py = (repo_root / "main.py").resolve()

    # info lokasi workspace
    st.caption(f"Workspace: `{ws_root}`")
    st.caption(f"Templates dir: `{templates_dir}`")
    st.caption(f"main.py: `{main_py}`")

    # role
    role = ""
    if isinstance(ctx, dict):
        role = str(ctx.get("auth_role") or "")
    if not role:
        role = str(st.session_state.get("auth_role", "") or "")
    can_edit = (role == "admin")

    # ============ INIT STATE ============
    if "json_content" not in st.session_state:
        st.session_state.json_content = (
            '{\n'
            '  "title": "Contoh Template",\n'
            '  "seconds": 30,\n'
            '  "facts": []\n'
            "}\n"
        )
    if "json_filename" not in st.session_state:
        st.session_state.json_filename = "new_script.json"

    # ============ NEW: GENERATE TXT FROM JSON ============
    with st.expander("⚡ Generate .txt dari Template JSON (pindahan dari Control Panel)", expanded=True):
        if not main_py.exists():
            st.error(f"main.py tidak ditemukan: {main_py}")
        else:
            contents_dir = (ws_root / "contents").resolve()
            contents_dir.mkdir(parents=True, exist_ok=True)

            topics = _list_topics(contents_dir)
            if not topics:
                st.info("Belum ada folder topic di `contents/`. Kamu bisa buat baru di bawah.")

            left, right = st.columns([1.2, 1])

            with left:
                topic_pick = st.selectbox(
                    "Target Topic (output akan masuk ke contents/<topic>/...)",
                    options=(["-- BUAT TOPIC BARU --"] + topics),
                    index=0,
                    key="tplgen_topic_pick",
                )
                if topic_pick == "-- BUAT TOPIC BARU --":
                    topic_name = st.text_input("Nama topic baru", value="automotif", key="tplgen_new_topic")
                else:
                    topic_name = topic_pick

                topic_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", (topic_name or "").strip()) or "automotif"

            with right:
                gen_count = st.number_input("Jumlah script .txt", min_value=1, max_value=100, value=5, step=1, key="tplgen_count")
                allow_repeat = st.checkbox("Boleh fakta kembar (--allow-repeat)", value=False, key="tplgen_repeat")

            st.divider()

            # pilih template file: default ke file yang sedang dibuka di editor (kalau ada)
            existing_files = _list_json_files(templates_dir)
            default_name = str(st.session_state.get("json_filename") or "").strip()
            default_idx = 0
            if default_name in existing_files:
                default_idx = existing_files.index(default_name)

            template_choice = ""
            if existing_files:
                template_choice = st.selectbox(
                    "Pilih Template JSON file (opsional). Kalau kamu edit di editor tanpa simpan, sistem akan pakai TEMP file.",
                    existing_files,
                    index=default_idx,
                    key="tplgen_template_choice",
                )
            else:
                st.warning("Belum ada file template JSON di folder templates/. Kamu bisa paste JSON di editor lalu Generate (akan pakai TEMP file).")

            st.caption("Catatan: tombol ini akan menjalankan `main.py --generate N --template ...` seperti sebelumnya di Control Panel.")

            # tombol generate
            if st.button("🚀 Generate TXT Scripts", use_container_width=True, key="btn_tplgen_run"):
                # 1) validasi JSON dari editor (biar bisa pakai temp file walau belum disimpan)
                json_body = st.session_state.get("json_content") or ""
                # ambil isi terbaru dari editor via widget (kalau ada)
                # (editor text_area di bawah akan update variable lokal, jadi kita parse lagi di bawah sebelum run)
                st.session_state["tplgen_last_error"] = ""

                # ambil content dari editor component (pakai key editor)
                # kalau belum ada key, fallback ke session json_content
                editor_val = st.session_state.get("tpl_editor_value")
                if isinstance(editor_val, str) and editor_val.strip():
                    json_body = editor_val

                try:
                    parsed = json.loads(json_body)
                except json.JSONDecodeError as e:
                    st.error("❌ JSON di editor tidak valid. Perbaiki dulu sebelum generate.")
                    st.code(f"Error di baris {e.lineno}, kolom {e.colno}: {e.msg}")
                    return

                # 2) tentukan template path yang dipakai
                ts = time.strftime("%Y%m%d_%H%M%S")
                use_path: Path

                # kalau file pilihan ada & editor tidak berubah, pakai file itu
                if template_choice:
                    file_path = (templates_dir / template_choice).resolve()
                else:
                    file_path = None

                # tulis TEMP template dari editor (selalu) supaya yang dipakai sesuai editor saat ini
                temp_name = _safe_filename(default_name or "template.json").replace(".json", f"_TEMP_{ts}.json")
                use_path = (templates_dir / temp_name).resolve()
                use_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

                # 3) build cmd
                cmd_args = [sys.executable, str(main_py), "--topic", topic_name, "--generate", str(int(gen_count)), "--template", str(use_path)]
                if allow_repeat:
                    cmd_args.append("--allow-repeat")

                # 4) env (ambil dari ctx kalau ada)
                env = os.environ.copy()
                api = (ctx or {}).get("api_keys", {}) if isinstance(ctx, dict) else {}
                if api:
                    if api.get("elevenlabs"):
                        env["ELEVENLABS_API_KEY"] = str(api["elevenlabs"])
                    if api.get("gemini"):
                        env["GEMINI_API_KEY"] = str(api["gemini"])
                        env["GOOGLE_API_KEY"] = str(api["gemini"])
                    if api.get("pexels"):
                        env["PEXELS_API_KEY"] = str(api["pexels"])
                    if api.get("pixabay"):
                        env["PIXABAY_API_KEY"] = str(api["pixabay"])

                log_dir = (ws_root / "logs").resolve()
                log_path = (log_dir / f"generate_txt_{topic_name}_{ts}.log").resolve()

                st.info(f"Menjalankan:\n`{shlex.join(cmd_args)}`")
                with st.status("⏳ Generating scripts...", state="running", expanded=True):
                    rc, _logs = _run_streaming(cmd_args, cwd=ws_root, env=env, log_path=log_path)

                if rc == 0:
                    st.success("✅ Generate selesai.")
                else:
                    st.error(f"❌ Generate gagal (rc={rc}). Cek log di bawah.")

                # tombol download log
                if log_path.exists():
                    st.download_button(
                        "⬇️ Download Log",
                        data=log_path.read_bytes(),
                        file_name=log_path.name,
                        mime="text/plain",
                        use_container_width=True,
                        key="btn_tplgen_dl_log",
                    )

                # info hasil: hitung txt terbaru
                out_topic = (ws_root / "contents" / topic_name).resolve()
                txts = sorted(out_topic.rglob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if out_topic.exists() else []
                if txts:
                    st.caption(f"Contoh output terbaru: `{txts[0].relative_to(ws_root)}`")

    # =========================
    # Layout editor (seperti sebelumnya)
    # =========================
    col_list, col_editor = st.columns([1, 2])

    # LEFT: FILE MANAGER
    with col_list:
        st.subheader("📂 File Manager")

        existing_files = _list_json_files(templates_dir)
        selected_file = st.selectbox("Pilih File:", ["-- Buat Baru --"] + existing_files, key="tpl_selected_file")

        c1, c2 = st.columns(2)
        with c1:
            load_clicked = st.button("📂 Load", use_container_width=True)
        with c2:
            refresh_clicked = st.button("🔄 Refresh", use_container_width=True)

        if refresh_clicked:
            st.rerun()

        if load_clicked:
            if selected_file == "-- Buat Baru --":
                st.session_state.json_content = (
                    '{\n'
                    '  "title": "Contoh Template",\n'
                    '  "seconds": 30,\n'
                    '  "facts": []\n'
                    "}\n"
                )
                st.session_state.json_filename = "new_script.json"
                st.success("Template baru dibuat.")
                st.rerun()
            else:
                path = (templates_dir / selected_file).resolve()
                try:
                    data = json.loads(path.read_text(encoding="utf-8", errors="strict"))
                    st.session_state.json_content = json.dumps(data, indent=2, ensure_ascii=False)
                    st.session_state.json_filename = selected_file
                    st.success(f"Loaded: {selected_file}")
                    st.rerun()
                except UnicodeDecodeError:
                    raw = path.read_text(encoding="latin-1", errors="replace")
                    try:
                        data = json.loads(raw)
                        st.session_state.json_content = json.dumps(data, indent=2, ensure_ascii=False)
                        st.session_state.json_filename = selected_file
                        st.success(f"Loaded: {selected_file} (latin-1 fallback)")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error loading JSON: {e}")
                except Exception as e:
                    st.error(f"Error loading JSON: {e}")

        st.divider()
        st.caption("Tips: Template yang disimpan di sini akan dipakai untuk Generate script .txt.")

    # RIGHT: EDITOR
    with col_editor:
        st.subheader("✍️ Edit Source Code")

        new_filename = st.text_input(
            "Nama File (.json)",
            value=st.session_state.json_filename,
            help="Nama file akan disanitasi. Ekstensi .json otomatis.",
        )

        json_body = st.text_area(
            "JSON Content",
            value=st.session_state.json_content,
            height=520,
            help="Pastikan JSON valid (kutip dua, koma, kurung).",
            key="tpl_editor_value",
        )

        if not can_edit:
            st.info("Akun ini mode VIEWER: hanya bisa melihat & preview. Tombol Simpan dinonaktifkan.")

        col_save, col_validate = st.columns([1, 1])

        with col_validate:
            if st.button("✅ Validate JSON", use_container_width=True):
                try:
                    _ = json.loads(json_body)
                    st.success("JSON valid.")
                except json.JSONDecodeError as e:
                    st.error("Format JSON Error!")
                    st.code(f"Error di baris {e.lineno}, kolom {e.colno}: {e.msg}")

        with col_save:
            if st.button("💾 SIMPAN JSON", type="primary", use_container_width=True, disabled=not can_edit):
                try:
                    parsed_data = json.loads(json_body)

                    safe_name = _safe_filename(new_filename)
                    save_path = (templates_dir / safe_name).resolve()

                    save_path.write_text(json.dumps(parsed_data, indent=2, ensure_ascii=False), encoding="utf-8")

                    st.session_state.json_content = json.dumps(parsed_data, indent=2, ensure_ascii=False)
                    st.session_state.json_filename = safe_name

                    st.success(f"✅ Tersimpan: {safe_name}")
                    st.rerun()

                except json.JSONDecodeError as e:
                    st.error("❌ Format JSON Error!")
                    st.code(f"Error di baris {e.lineno}, kolom {e.colno}: {e.msg}")
                except Exception as e:
                    st.error(f"Gagal menyimpan: {e}")
