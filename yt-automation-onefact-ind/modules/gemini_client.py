import os
import time
import socket
import json
import traceback
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Union, Optional, Tuple
import urllib.request
import urllib.error

# Optional: tetap support SDK lama kalau ada
try:
    import google.generativeai as genai  # type: ignore
    _HAS_OLD_SDK = True
except Exception:
    genai = None  # type: ignore
    _HAS_OLD_SDK = False

os.makedirs("logs", exist_ok=True)
LOG_PATH = "logs/gemini_chat.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

OpenAILikeMsg = Dict[str, str]  # {"role": "...", "content": "..."}

def _append_jsonl(path: Path, obj: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        # jangan sampai logging bikin generate gagal
        pass


def _usage_from_rest(last_api_json: dict) -> dict | None:
    """
    Normalize REST usage metadata to snake_case:
      prompt_token_count, candidates_token_count, total_token_count
    Supports both camelCase and snake_case fields.
    """
    if not isinstance(last_api_json, dict):
        return None

    um = last_api_json.get("usageMetadata") or last_api_json.get("usage_metadata") or None
    if not isinstance(um, dict):
        return None

    def g(*keys, default=0):
        for k in keys:
            v = um.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return int(default)

    return {
        "prompt_token_count": g("promptTokenCount", "prompt_token_count"),
        "candidates_token_count": g("candidatesTokenCount", "candidates_token_count"),
        "total_token_count": g("totalTokenCount", "total_token_count"),
    }

def _normalize_model_name(name: str) -> str:
    name = (name or "").strip()
    if name.startswith("models/"):
        name = name.split("/", 1)[1]
    return name


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    return s

import base64

def _file_to_inline_part(path: Path, mime_type: str) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"inline_data": {"mime_type": mime_type, "data": b64}}

def _extract_outer_json(s: str) -> str:
    s = (s or "").strip()
    i_obj = s.find("{")
    i_arr = s.find("[")
    if i_obj == -1 and i_arr == -1:
        return s

    if i_arr != -1 and (i_obj == -1 or i_arr < i_obj):
        i = i_arr
        j = s.rfind("]")
    else:
        i = i_obj
        j = s.rfind("}")

    if i != -1 and j != -1 and j > i:
        return s[i : j + 1].strip()
    return s


def _sanitize_json_text(s: str) -> str:
    """
    Sanitize output Gemini supaya json.loads() tahan banting:
    - normalize smart quotes
    - escape newline/tab literal di dalam string
    - heuristic: bedakan quote penutup string vs quote di tengah kalimat
    - remove trailing comma sebelum } atau ]
    """
    s = (s or "")
    s = s.lstrip("\ufeff")
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")

    out: list[str] = []
    in_str = False
    esc = False
    n = len(s)

    def _next_nonspace_idx(start: int) -> int:
        j = start
        while j < n and s[j] in " \t\r\n":
            j += 1
        return j

    for i, ch in enumerate(s):
        if in_str:
            if esc:
                out.append(ch)
                esc = False
                continue

            if ch == "\\":
                out.append(ch)
                esc = True
                continue

            if ch == '"':
                j = _next_nonspace_idx(i + 1)
                nn = s[j] if j < n else ""

                # kandidat closing quote (JSON syntax)
                if nn in (":", "}", "]", ""):
                    out.append('"')
                    in_str = False
                    continue

                if nn == ",":
                    # Ambigu: bisa delimiter JSON, bisa tanda baca dalam kalimat.
                    k = _next_nonspace_idx(j + 1)
                    nn2 = s[k] if k < n else ""
                    # kalau setelah koma huruf/angka/('(') -> kemungkinan itu masih kalimat, jadi quote harus di-escape
                    if nn2 and (nn2.isalnum() or nn2 in ("(", "-", "_")):
                        out.append('\\"')
                        continue
                    # kalau setelah koma token JSON (quote/} / ]) -> ini closing quote beneran
                    out.append('"')
                    in_str = False
                    continue

                # selain itu: anggap quote ini quote di tengah kalimat → escape
                out.append('\\"')
                continue

            # escape control chars inside string
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue

            out.append(ch)
        else:
            if ch == '"':
                out.append('"')
                in_str = True
            else:
                out.append(ch)

    s2 = "".join(out)
    s2 = re.sub(r",\s*([}\]])", r"\1", s2)  # trailing comma
    return s2


def _salvage_fact_template(clean: str) -> str | None:
    """
    Kalau JSON kepotong, ambil facts yang objeknya sudah lengkap (brace balance),
    lalu bikin JSON baru yang valid.
    """
    try:
        title = "Template"
        m = re.search(r'"title"\s*:\s*"([^"]*)"', clean)
        if m:
            title = m.group(1)

        seconds = 30
        m = re.search(r'"seconds"\s*:\s*(\d+)', clean)
        if m:
            seconds = int(m.group(1))

        k = clean.find('"facts"')
        if k < 0:
            return None
        b = clean.find("[", k)
        if b < 0:
            return None

        facts: list[dict] = []
        i = b + 1
        n = len(clean)

        in_str = False
        esc = False
        depth = 0
        start_obj = -1

        while i < n:
            ch = clean[i]

            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                i += 1
                continue

            if ch == '"':
                in_str = True
                i += 1
                continue

            if ch == "{":
                if depth == 0:
                    start_obj = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start_obj >= 0:
                        obj_txt = clean[start_obj : i + 1]
                        try:
                            obj = json.loads(obj_txt)
                            if isinstance(obj, dict):
                                facts.append(obj)
                        except Exception:
                            pass
                        start_obj = -1
            elif ch == "]" and depth == 0:
                break

            i += 1

        if not facts:
            return None

        data = {"title": title, "seconds": seconds, "facts": facts}
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return None


class GeminiClient:
    """
    - Default: pakai SDK lama google.generativeai (kalau tersedia) untuk text biasa.
    - Jika force_json=True: pakai REST v1beta generateContent,
      karena JSON schema lebih stabil via REST.
    """

    def __init__(self, api_key: str, model: Optional[str] = None, timeout: int = 30):
        self.api_key = (api_key or "").strip()
        self.timeout = int(timeout)
        self._last_api_json: Dict[str, Any] = {}

        default_model = (os.getenv("GEMINI_MODEL") or "gemini-flash-latest").strip()
        self.model_name = _normalize_model_name(model or default_model)

        self._old_model = None
        if _HAS_OLD_SDK and self.api_key:
            try:
                genai.configure(api_key=self.api_key)  # type: ignore
                self._old_model = genai.GenerativeModel(self.model_name)  # type: ignore
            except Exception as e:
                logging.warning(f"Old SDK init failed, will fallback to REST. err={e}")
                self._old_model = None

    def _to_contents(self, messages: Union[str, List[Dict[str, Any]]]) -> Any:
        if isinstance(messages, str):
            return [{"role": "user", "parts": [{"text": messages}]}]

        contents = []
        for m in messages:
            role = (m.get("role") or "user").strip().lower()
            # Gemini REST: role biasanya user/model
            if role == "assistant":
                role = "model"
            elif role not in ("user", "model"):
                role = "user"

            text = (m.get("content") or m.get("text") or "").strip()
            if not text:
                continue
            contents.append({"role": role, "parts": [{"text": text}]})

        if not contents:
            contents = [{"role": "user", "parts": [{"text": ""}]}]
        return contents

    def _record_usage(self, usage_obj: Any) -> None:
        """
        Append token usage into <job_dir>/gemini_usage.jsonl if PORTAL_JOB_DIR set.
        """
        job_dir = os.getenv("PORTAL_JOB_DIR", "").strip()
        if not job_dir:
            return

        jd = Path(job_dir)
        usage = None

        # SDK usage_metadata object -> try best
        if isinstance(usage_obj, dict):
            usage = usage_obj
        else:
            # try REST usage metadata from last api json
            usage = _usage_from_rest(getattr(self, "_last_api_json", {}) or {})

        if not isinstance(usage, dict):
            return

        pt = usage.get("prompt_token_count")
        ct = usage.get("candidates_token_count")
        tt = usage.get("total_token_count")

        # normalize keys if camelCase
        if pt is None:
            pt = usage.get("promptTokenCount")
        if ct is None:
            ct = usage.get("candidatesTokenCount")
        if tt is None:
            tt = usage.get("totalTokenCount")

        try:
            pt = int(pt or 0)
            ct = int(ct or 0)
            tt = int(tt or 0)
        except Exception:
            return

        rec = {
            "ts": int(time.time()),
            "model": self.model_name,
            "prompt_token_count": pt,
            "candidates_token_count": ct,
            "total_token_count": tt,
        }

        _append_jsonl(jd / "gemini_usage.jsonl", rec)

    def _rest_generate(
        self,
        contents: Any,
        temperature: float,
        max_tokens: int,
        force_json: bool,
        json_schema: Optional[dict],
    ) -> str:
        # pakai header x-goog-api-key (lebih bersih dibanding query ?key=)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

        gen_cfg: Dict[str, Any] = {
            "temperature": float(temperature),
            "maxOutputTokens": int(max_tokens),
        }

        if force_json:
            gen_cfg["responseMimeType"] = "application/json"
            if json_schema:
                gen_cfg["responseJsonSchema"] = json_schema

        payload = {"contents": contents, "generationConfig": gen_cfg}
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        j = json.loads(raw)
        self._last_api_json = j

        cands = j.get("candidates") or []
        if not cands:
            pf = j.get("promptFeedback")
            raise RuntimeError(f"No candidates returned. promptFeedback={pf}")

        content = cands[0].get("content") or {}
        parts = content.get("parts") or []
        if not parts:
            raise RuntimeError("No parts in candidate content.")

        # gabung semua parts biar gak kepotong
        text = "".join([str(p.get("text") or "") for p in parts]).strip()

        cand0 = cands[0] if cands else {}
        finish = cand0.get("finishReason")
        safety = cand0.get("safetyRatings")
        logging.warning(f"[Gemini] finishReason={finish} safety={safety}")

        return text

    def _clean_json_text(self, raw_text: str) -> Tuple[str, str]:
        """
        Return (base_clean, best_clean).
        - base_clean: strip fences + ambil outer JSON
        - best_clean: base_clean kalau sudah valid, atau versi sanitized kalau perlu
        """
        base_clean = _extract_outer_json(_strip_code_fences(raw_text))

        # 1) coba parse base dulu (jangan rusak JSON valid)
        try:
            json.loads(base_clean)
            return base_clean, base_clean
        except json.JSONDecodeError:
            pass

        # 2) baru sanitize jika memang invalid
        sanitized = _sanitize_json_text(base_clean)
        return base_clean, sanitized

    def generate(
        self,
        messages: Union[str, List[OpenAILikeMsg]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        retries: int = 3,
        backoff_base: float = 2.0,
        force_json: bool = False,
        json_schema: dict | None = None,
    ) -> Dict[str, Any]:
        contents = self._to_contents(messages)
        attempt = 0

        while True:
            try:
                if force_json:
                    last_err: Optional[json.JSONDecodeError] = None
                    last_clean = ""
                    last_base = ""
                    contents_try = contents
                    temp_try = float(temperature)

                    for jtry in range(3):
                        raw_text = self._rest_generate(
                            contents=contents_try,
                            temperature=temp_try,
                            max_tokens=max_tokens,
                            force_json=True,
                            json_schema=json_schema,
                        )

                        base_clean, clean = self._clean_json_text(raw_text)
                        last_clean = clean
                        last_base = base_clean

                        try:
                            json.loads(clean)
                            self._record_usage(_usage_from_rest(self._last_api_json) or {})
                            return {"text": clean, "usage": _usage_from_rest(self._last_api_json), "raw": None}
                        except json.JSONDecodeError as e:
                            last_err = e

                            # salvage partial facts kalau kepotong
                            try:
                                salv = _salvage_fact_template(clean) or _salvage_fact_template(base_clean)
                                if salv:
                                    logging.warning("Gemini JSON invalid -> salvaged partial facts.")
                                    return {"text": salv, "usage": None, "raw": None}
                            except Exception:
                                pass

                            # retry berikutnya: temperature 0 + minta tulis ulang full JSON
                            temp_try = 0.0
                            contents_try = list(contents) + [
                                {
                                    "role": "user",
                                    "parts": [
                                        {
                                            "text": (
                                                "PERBAIKI: Output sebelumnya JSON tidak valid / terpotong.\n"
                                                "TULIS ULANG dari awal sebagai JSON yang lengkap dan valid.\n"
                                                "Pastikan semua string ditutup tanda kutip dan semua kurung { } [ ] tertutup.\n"
                                                "Output JSON saja, tanpa penjelasan, tanpa markdown."
                                            )
                                        }
                                    ],
                                }
                            ]

                            # dump log
                            os.makedirs("logs", exist_ok=True)
                            ts = time.strftime("%Y%m%d_%H%M%S")
                            Path("logs").joinpath(f"gemini_json_fail_RAW_{ts}_try{jtry+1}.txt").write_text(
                                raw_text, encoding="utf-8", errors="ignore"
                            )
                            Path("logs").joinpath(f"gemini_json_fail_BASE_{ts}_try{jtry+1}.txt").write_text(
                                base_clean, encoding="utf-8", errors="ignore"
                            )
                            Path("logs").joinpath(f"gemini_json_fail_CLEAN_{ts}_try{jtry+1}.txt").write_text(
                                clean, encoding="utf-8", errors="ignore"
                            )

                    if last_err is not None:
                        pos = int(getattr(last_err, "pos", 0) or 0)
                        a = max(0, pos - 180)
                        b = min(len(last_clean), pos + 180)
                        snippet = last_clean[a:b]

                        finish = None
                        try:
                            finish = ((self._last_api_json.get("candidates") or [{}])[0]).get("finishReason")
                        except Exception:
                            pass

                        raise RuntimeError(
                            f"Invalid JSON from Gemini after retries: {last_err.msg} | "
                            f"line={last_err.lineno} col={last_err.colno} pos={pos}\n"
                            f"finishReason={finish}\n"
                            f"Snippet:\n{snippet!r}\n"
                            f"See logs/gemini_json_fail_* for dumps.\n"
                            f"(Last BASE length={len(last_base)} CLEAN length={len(last_clean)})"
                        ) from last_err

                    raise RuntimeError("Invalid JSON from Gemini after retries.")

                # ===== Text biasa: coba SDK lama dulu =====
                if self._old_model is not None:
                    response = self._old_model.generate_content(  # type: ignore
                        contents,
                        generation_config={
                            "temperature": float(temperature),
                            "max_output_tokens": int(max_tokens),
                        },
                        request_options={"timeout": self.timeout},
                    )
                    text = getattr(response, "text", "") or ""

                    u = getattr(response, "usage_metadata", None)
                    # usage_metadata kadang object -> kita coba ambil attribute yg umum
                    ud = None
                    try:
                        ud = {
                            "prompt_token_count": int(getattr(u, "prompt_token_count", 0) or 0),
                            "candidates_token_count": int(getattr(u, "candidates_token_count", 0) or 0),
                            "total_token_count": int(getattr(u, "total_token_count", 0) or 0),
                        }
                    except Exception:
                        ud = None

                    self._record_usage(ud or {})
                    return {"text": text, "usage": u, "raw": response}
                    #return {"text": text, "usage": getattr(response, "usage_metadata", None), "raw": response}

                # ===== Fallback REST untuk text biasa =====
                text = self._rest_generate(
                    contents=contents,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_json=False,
                    json_schema=None,
                )

                self._record_usage(_usage_from_rest(self._last_api_json) or {})
                return {"text": text, "usage": _usage_from_rest(self._last_api_json), "raw": None}
                #return {"text": text, "usage": None, "raw": None}

            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                logging.warning(f"Gemini HTTPError {e.code}: {body}")
                if e.code not in (429, 500, 503):
                    raise RuntimeError(f"Gemini HTTPError {e.code}: {body}") from e

            except (TimeoutError, socket.timeout) as e:
                logging.warning(f"Gemini timeout: {e}")

            except urllib.error.URLError as e:
                if "timed out" in str(getattr(e, "reason", "")).lower():
                    logging.warning(f"Gemini URLError timeout: {e}")
                else:
                    logging.exception(f"Gemini URLError: {e}")
                    raise

            except Exception as e:
                logging.exception(f"Gemini generate error: {e}")
                raise

            attempt += 1
            if attempt > retries:
                raise

            time.sleep(backoff_base ** (attempt - 1))

