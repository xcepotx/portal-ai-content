from __future__ import annotations
import re

def sanitize_paths(text: str) -> str:
    if not text:
        return ""
    s = text

    # Linux home paths
    s = re.sub(r"/home/[^ \n\t]+", "/home/<redacted>", s)

    # common absolute paths
    s = re.sub(r"(/mnt/data/[^ \n\t]+)", "/mnt/data/<redacted>", s)
    s = re.sub(r"(/etc/[^ \n\t]+)", "/etc/<redacted>", s)
    s = re.sub(r"(/usr/[^ \n\t]+)", "/usr/<redacted>", s)
    s = re.sub(r"(/var/[^ \n\t]+)", "/var/<redacted>", s)
    s = re.sub(r"(/opt/[^ \n\t]+)", "/opt/<redacted>", s)

    # repo paths (custom)
    s = s.replace("user-management-portal", "<portal>")
    s = s.replace("yt-automation-onefact-ind", "<repo>")

    return s

def safe_log_text(text: str, *, hide_paths: bool) -> str:
    if not text:
        return ""
    return sanitize_paths(text) if hide_paths else text
