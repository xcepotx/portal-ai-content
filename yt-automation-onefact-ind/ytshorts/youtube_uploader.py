# ytshorts/youtube_uploader.py
import os
import pickle
from typing import Optional, List

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

import json
from googleapiclient.errors import HttpError, ResumableUploadError

class UploadLimitExceeded(RuntimeError):
    pass

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _save_token(creds, token_path: str):
    with open(token_path, "wb") as f:
        pickle.dump(creds, f)


def _load_token(token_path: str):
    if os.path.exists(token_path):
        with open(token_path, "rb") as f:
            return pickle.load(f)
    return None


def _oauth_console_flow(flow: InstalledAppFlow):
    """
    Console OAuth: print URL, user opens on their machine, paste code back.
    Works on headless servers.
    """
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )
    print("\n=== YOUTUBE OAUTH (HEADLESS) ===")
    print("1) Buka URL ini di laptop/PC (browser):\n")
    print(auth_url)
    print("\n2) Login Google -> Allow")
    code = input("\n3) Paste code yang kamu dapat di sini: ").strip()
    flow.fetch_token(code=code)
    return flow.credentials


def get_youtube_client(
    client_secrets: str = "client_secret.json",
    token_path: str = "token.pickle",
):
    creds = _load_token(token_path)

    if creds and creds.valid:
        return build("youtube", "v3", credentials=creds)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds, token_path)
        return build("youtube", "v3", credentials=creds)

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)

    # Try local server flow first (works on desktop with browser)
    try:
        creds = flow.run_local_server(port=0)
    except Exception as e:
        # Headless fallback
        print(f"[INFO] run_local_server gagal ({type(e).__name__}: {e}). Pakai console OAuth.")
        # Some versions provide run_console(); prefer if available
        if hasattr(flow, "run_console"):
            try:
                creds = flow.run_console()
            except Exception:
                creds = _oauth_console_flow(flow)
        else:
            creds = _oauth_console_flow(flow)

    _save_token(creds, token_path)
    return build("youtube", "v3", credentials=creds)


def upload_short(
    video_path: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy: str = "unlisted",
    publish_at_rfc3339: Optional[str] = None,
) -> str:
    """
    If publish_at_rfc3339 is provided, privacy will be forced to 'private' and publishAt set.
    RFC3339 example: '2026-01-20T14:00:00+07:00'
    """
    youtube = get_youtube_client()

    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
    }

    # Scheduling requires private + publishAt
    if publish_at_rfc3339:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at_rfc3339

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": "2",  # Autos & Vehicles
        },
        "status": status,
    }

    media = MediaFileUpload(video_path, resumable=True)

    req = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    try:
        res = req.execute()
        return res["id"]
    except (HttpError, ResumableUploadError) as e:
        if _is_upload_limit_error(e):
            raise UploadLimitExceeded(
                "Upload limit harian tercapai (uploadLimitExceeded). Stop upload untuk run ini."
            ) from e
        raise
    return res["id"]

def _is_upload_limit_error(e: Exception) -> bool:
    # Kasus A: HttpError / ResumableUploadError dengan payload json reason=uploadLimitExceeded
    content = None
    if hasattr(e, "content") and e.content:
        try:
            content = e.content.decode("utf-8", errors="ignore")
        except Exception:
            content = str(e.content)

    # beberapa error tidak punya .content, tapi punya str(e)
    s = (content or str(e) or "").lower()

    # cepat: string match
    if "uploadlimitexceeded" in s:
        return True

    # kalau json, parse reason
    try:
        data = json.loads(content) if content else None
        if isinstance(data, dict):
            err = data.get("error") or {}
            errors = err.get("errors") or []
            for it in errors:
                if str(it.get("reason", "")).lower() == "uploadlimitexceeded":
                    return True
    except Exception:
        pass

    return False
