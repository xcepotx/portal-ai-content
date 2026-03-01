import json
import os
import shutil
from pathlib import Path

REVIEW_DIR = "review_projects"

def init_review_dir():
    if not os.path.exists(REVIEW_DIR):
        os.makedirs(REVIEW_DIR)

def save_project_state(topic, slug, lines, tts_files, bg_paths, audio_bgm=None):
    """
    Menyimpan state project ke folder review_projects/<topic>_<slug>/
    """
    init_review_dir()
    
    project_name = f"{topic}_{slug}"
    project_path = os.path.join(REVIEW_DIR, project_name)
    os.makedirs(project_path, exist_ok=True)
    
    # Copy aset gambar ke folder project agar aman (tidak tertimpa cache)
    local_bg_paths = []
    for i, src in enumerate(bg_paths):
        if src and os.path.exists(src):
            ext = os.path.splitext(src)[1]
            dst_name = f"segment_{i:03d}{ext}"
            dst_path = os.path.join(project_path, dst_name)
            shutil.copy2(src, dst_path)
            local_bg_paths.append(dst_name) # Simpan nama file relatif
        else:
            local_bg_paths.append(None)

    # Struktur data JSON
    state = {
        "topic": topic,
        "slug": slug,
        "lines": lines,
        "tts_files": tts_files, # Asumsi path absolute atau relatif aman
        "bg_files": local_bg_paths,
        "bgm": audio_bgm,
        "status": "draft"
    }
    
    json_path = os.path.join(project_path, "project_state.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        
    return project_path

def load_project_state(project_folder_name):
    path = os.path.join(REVIEW_DIR, project_folder_name, "project_state.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def update_segment_image(project_folder_name, segment_index, new_image_file):
    """
    Mengganti gambar untuk segmen tertentu dari upload user
    """
    project_path = os.path.join(REVIEW_DIR, project_folder_name)
    json_path = os.path.join(project_path, "project_state.json")
    
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Simpan file baru
    filename = new_image_file.name
    dst_path = os.path.join(project_path, filename)
    
    with open(dst_path, "wb") as f:
        f.write(new_image_file.getbuffer())
        
    # Update JSON
    data["bg_files"][segment_index] = filename
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    return True
