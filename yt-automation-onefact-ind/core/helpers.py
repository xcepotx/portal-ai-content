import os, time, random, signal, subprocess, re
from pathlib import Path
import streamlit as st

PID_FILE = "running_process.pid"
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

_STOP_CONTAINER = None  # di-set dari sidebar

def set_stop_container(container):
    global _STOP_CONTAINER
    _STOP_CONTAINER = container

def make_log_path(prefix: str = "long") -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(LOG_DIR, f"{prefix}_{ts}.log")

def clean_json_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def save_pid(pid: int):
    with open(PID_FILE, "w") as f:
        f.write(str(pid))

def get_pid():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            try:
                return int(f.read().strip())
            except:
                return None
    return None

def clear_pid():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def kill_running_process():
    pid = get_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            st.toast(f"🛑 Proses (PID: {pid}) berhasil dihentikan paksa!", icon="💀")
        except ProcessLookupError:
            st.warning("Proses sudah tidak ada (mungkin sudah selesai).")
        except Exception as e:
            st.error(f"Gagal menghentikan proses: {e}")
        finally:
            clear_pid()
            show_stop_button(clear=True)
    else:
        st.info("Tidak ada proses yang tercatat sedang berjalan.")

def show_stop_button(clear: bool = False):
    global _STOP_CONTAINER
    if _STOP_CONTAINER is None:
        return
    if clear:
        _STOP_CONTAINER.empty()
        return

    with _STOP_CONTAINER.container():
        st.error("⚠️ Ada proses sedang berjalan!")
        if st.button("🛑 HENTIKAN PAKSA", type="primary", use_container_width=True, key="kill_btn_sidebar"):
            kill_running_process()
        st.divider()

def pick_from_json_field(data: dict, key: str, fallback=""):
    val = data.get(key, fallback)
    if isinstance(val, list):
        val = random.choice(val) if val else fallback
    if val is None:
        val = fallback
    return str(val).strip()

def get_topics():
    contents_path = Path("contents")
    if not contents_path.exists():
        os.makedirs(contents_path)
    return [d.name for d in contents_path.iterdir() if d.is_dir()]

def get_latest_video(topic: str):
    out_dir = Path(f"out/{topic}")
    if not out_dir.exists():
        return None
    files = list(out_dir.glob("*.mp4"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def get_latest_long_video():
    res_dir = Path("results")
    if not res_dir.exists():
        return None
    files = list(res_dir.glob("*.mp4"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def run_script(cmd_list):
    return subprocess.Popen(
        cmd_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

def init_state():
    if "process_log" not in st.session_state:
        st.session_state.process_log = ""
