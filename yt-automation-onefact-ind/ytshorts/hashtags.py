# ytshorts/hashtags.py
from __future__ import annotations
import re
from typing import List, Optional

BASE_HASHTAGS = ["#shorts", "#ytshorts", "#fakta", "#faktaunik"]

TOPIC_HASHTAGS = {
    "automotif": ["#otomotif", "#mobil", "#cars", "#automotive", "#carfacts"],
    "motor": ["#motor", "#motorcycle", "#motofacts"],
    "teknologi": ["#teknologi", "#tech", "#gadget"],
    "sains": ["#sains", "#science"],
    "sejarah": ["#sejarah", "#history"],
}

_STOP = {
    "the","a","an","and","or","of","to","in","on","for","with","without",
    "photo","realistic","illustration","background","close","up","detail",
    "system","car","cars","automotive","vehicle",  # kata terlalu umum
    "engine","bay",  # nanti kita gabung jadi enginebay (opsional)
}

def _clean_topic(topic: str) -> str:
    return (topic or "").strip().lower()

def _slug_tokens(s: str) -> List[str]:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    toks = [t for t in s.split() if t and t not in _STOP and len(t) >= 3]
    return toks

def _norm_topic(topic: str) -> str:
    return (topic or "").strip().lower()

def _tokenize(text: str) -> List[str]:
    # ambil token alnum/hyphen, buang yang pendek
    tokens = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", (text or "").lower())
    return tokens

def _extract_keywords(lines: List[str], max_kw: int = 5) -> List[str]:
    """
    Ambil keyword paling relevan dari content.
    Priority: line fakta/penjelasan > hook.
    """
    if not lines:
        return []

    # prioritas: line yang mengandung "FAKTA:" kalau ada
    joined = "\n".join(lines)
    m = re.search(r"fakta\s*:\s*(.+)", joined, flags=re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        # fallback: ambil 2-4 baris awal isi (bukan hook doang)
        text = " ".join(lines[1:5]) if len(lines) > 1 else lines[0]

    tokens = _tokenize(text)

    # scoring sederhana: token lebih panjang & muncul berulang => lebih penting
    freq = {}
    for t in tokens:
        t = t.strip("-")
        if len(t) < 3:
            continue
        if t in STOPWORDS:
            continue
        freq[t] = freq.get(t, 0) + 1

    # urut: freq desc, panjang desc
    ranked = sorted(freq.keys(), key=lambda k: (freq[k], len(k)), reverse=True)

    out = []
    for k in ranked:
        if k not in out:
            out.append(k)
        if len(out) >= max_kw:
            break
    return out

def _to_hashtag(token: str) -> str:
    token = token.strip().lower()

    # map khusus
    if token in KEYWORD_TO_HASHTAG:
        return KEYWORD_TO_HASHTAG[token]

    # bersihkan
    token = re.sub(r"[^a-z0-9]", "", token)
    if not token or len(token) < 3:
        return ""

    # jangan hashtag angka murni
    if token.isdigit():
        return ""

    return "#" + token

def _make_hashtags_from_phrase(phrase: str, max_tags: int = 2) -> List[str]:
    """
    "engine bay radiator cap close up" -> ["#radiatorcap", "#enginebay"] (contoh)
    """
    toks = _slug_tokens(phrase)

    # gabung bigram yang umum di otomotif
    joined = []
    i = 0
    while i < len(toks):
        if i + 1 < len(toks):
            bigram = toks[i] + toks[i + 1]
            # heuristik: ambil bigram utk pasangan kata pendek
            if len(toks[i]) <= 6 and len(toks[i+1]) <= 6:
                joined.append(bigram)
                i += 2
                continue
        joined.append(toks[i])
        i += 1

    # pilih yang “paling niat” (lebih panjang biasanya lebih spesifik)
    joined = sorted(set(joined), key=lambda x: (-len(x), x))
    return ["#" + x for x in joined[:max_tags]]

def build_hashtags(topic: str, lines: List[str], extra_terms: Optional[List[str]] = None, max_n: int = 10) -> List[str]:
    t = _clean_topic(topic)
    tags: List[str] = []
    tags += BASE_HASHTAGS
    tags += TOPIC_HASHTAGS.get(t, [("#" + re.sub(r"[^a-z0-9]+", "", t))] if t else [])

    # extra_terms dari bg.variants (ambil yang paling relevan)
    if extra_terms:
        for phrase in extra_terms:
            tags += _make_hashtags_from_phrase(phrase, max_tags=2)

    # unik + limit
    seen = set()
    out = []
    for h in tags:
        hl = h.lower()
        if hl in seen:
            continue
        seen.add(hl)
        out.append(h)
        if len(out) >= max_n:
            break
    return out
