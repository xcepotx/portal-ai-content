from __future__ import annotations

import os
import re
from typing import List, Optional


def _sec_to_srt(t: float) -> str:
    if t < 0:
        t = 0.0
    hh = int(t // 3600)
    mm = int((t % 3600) // 60)
    ss = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _ensure_dir(p: str) -> None:
    if p:
        os.makedirs(p, exist_ok=True)


def extract_fact_line(seg_text: str) -> str:
    """
    Ambil baris yang diawali 'FAKTA:'.
    Fallback: kalimat/baris pertama.
    """
    for line in (seg_text or "").splitlines():
        s = line.strip()
        if s.lower().startswith("fakta:"):
            return s
    # fallback
    lines = [x.strip() for x in (seg_text or "").splitlines() if x.strip()]
    return lines[0] if lines else ""


def _sanitize_caption(s: str, max_len: int = 140) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    # hindari subtitle kosong
    if not s:
        return ""
    return s[:max_len]

def _wrap_text(s: str, max_chars: int = 40) -> List[str]:
    s = _sanitize_caption(s, 400)
    if not s:
        return []
    words = s.split()
    lines: List[str] = []
    cur: List[str] = []
    cur_len = 0

    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur_len + add > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += add

    if cur:
        lines.append(" ".join(cur))
    return lines

def write_ass_fact_only(
    *,
    segments_text: List[str],
    durations_sec: List[float],
    out_ass: str,
    font: str = "DejaVu Sans",
    fontsize: int = 38,
    margin_v: int = 110,
    margin_lr: int = 140,
    show_sec: float = 4.0,
):
    _ensure_dir(os.path.dirname(out_ass))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Style: Base,{font},{fontsize},&H00FFFF&,&H00FFFF&,&H000000&,&H00000000,0,0,0,0,100,100,0,0,1,3,1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
"""
    events = [header]

    t0 = 0.0
    for seg_text, dur in zip(segments_text, durations_sec):
        dur = max(1.0, float(dur))
        t1 = t0 + dur

        # ambil fakta saja
        fact = ""
        for ln in seg_text.splitlines():
            if ln.lower().startswith("fakta:"):
                fact = ln.strip()
                break
        if not fact:
            fact = seg_text.splitlines()[0].strip()

        fact = _sanitize_caption(fact, 120)

        end = min(t0 + show_sec, t1)
        events.append(
            f"Dialogue: 0,{_ass_time(t0)},{_ass_time(end)},Base,,0,0,0,,{fact}\n"
        )

        t0 = t1

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(events)

    return out_ass


def _to_two_line_chunks(texts: List[str], max_chars: int = 40) -> List[str]:
    """
    Convert banyak kalimat -> beberapa chunk, tiap chunk max 2 baris.
    Return format ASS newline: \\N
    """
    chunks: List[str] = []
    for t in texts:
        wrapped = _wrap_text(t, max_chars=max_chars)
        if not wrapped:
            continue
        for i in range(0, len(wrapped), 2):
            chunk = "\\N".join(wrapped[i:i+2])
            chunks.append(chunk)
    return chunks


def _alloc_durations(total: float, chunks: List[str], min_sec: float = 1.2) -> List[float]:
    """
    Bagi durasi berdasarkan panjang teks chunk (proxy) -> lebih sinkron.
    """
    if not chunks:
        return []
    weights = [max(1.0, len(c.replace("\\N", " "))) for c in chunks]
    sw = sum(weights)
    out = [total * (w / sw) for w in weights]
    out = [max(float(min_sec), x) for x in out]  # minimal tampil biar kebaca
    s2 = sum(out)
    if s2 > 0:
        out = [total * (x / s2) for x in out]
    return out


def _words(text: str) -> List[str]:
    text = _sanitize_caption(text, 300)
    if not text:
        return []
    # split sederhana
    return [w for w in re.split(r"\s+", text) if w]


def write_srt(
    *,
    segments_text: List[str],
    segments_audio_paths: List[str],
    durations_sec: List[float],
    out_srt: str,
    mode: str = "segment",          # "segment" | "word"
    word_window_sec: float = 4.0,   # untuk mode word: tampilkan word-by-word di awal segmen (misal 4 detik)
    word_min_dur: float = 0.08,     # durasi minimum per kata
    max_words_per_line: int = 6,    # untuk word mode: gabungkan beberapa kata per entry
) -> str:
    """
    Generate SRT captions dari segment text + durasi audio.
    - mode="segment": 1 entry per segmen (FAKTA: ...)
    - mode="word": word-by-word (simulasi) di awal segmen, durasi dibagi rata

    durations_sec harus sesuai urutan segments.
    """
    if len(segments_text) != len(durations_sec):
        raise ValueError("segments_text length != durations_sec length")
    if segments_audio_paths and len(segments_audio_paths) != len(durations_sec):
        # audio_paths opsional (kalau kamu mau simpan juga), tapi harus match jika diisi
        raise ValueError("segments_audio_paths length != durations_sec length")

    blocks: List[str] = []
    idx = 1
    t0 = 0.0

    for seg_text, dur in zip(segments_text, durations_sec):
        dur = max(0.2, float(dur))
        t1 = t0 + dur

        fact = _sanitize_caption(extract_fact_line(seg_text))
        if not fact:
            fact = "FAKTA:"

        if mode == "segment":
            blocks.append(str(idx)); idx += 1
            blocks.append(f"{_sec_to_srt(t0)} --> {_sec_to_srt(t1)}")
            blocks.append(fact)
            blocks.append("")
        else:
            # mode word: tampilkan word-by-word pada awal segmen
            w = _words(fact)
            if not w:
                w = ["FAKTA"]

            window = min(float(word_window_sec), dur)
            window = max(window, 0.5)

            # gabungkan N kata per entry supaya tidak terlalu cepat kedip
            group = max(1, int(max_words_per_line))
            chunks = [" ".join(w[i:i+group]) for i in range(0, len(w), group)]

            step = window / max(1, len(chunks))
            step = max(step, float(word_min_dur))

            cur = t0
            for ch in chunks:
                ch = _sanitize_caption(ch, 140)
                if not ch:
                    continue
                end = min(cur + step, t0 + window)
                blocks.append(str(idx)); idx += 1
                blocks.append(f"{_sec_to_srt(cur)} --> {_sec_to_srt(end)}")
                blocks.append(ch)
                blocks.append("")
                cur = end

            # setelah word window selesai, tahan full fact sampai segmen berakhir (biar tetap kebaca)
            if cur < t1:
                blocks.append(str(idx)); idx += 1
                blocks.append(f"{_sec_to_srt(cur)} --> {_sec_to_srt(t1)}")
                blocks.append(fact)
                blocks.append("")

        t0 = t1

    _ensure_dir(os.path.dirname(out_srt))
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))

    return out_srt

def _wrap_text(s: str, max_chars: int = 38) -> List[str]:
    """
    Wrap sederhana berbasis jumlah karakter agar tidak keluar frame.
    ASS WrapStyle=2 akan wrap, tapi ini bantu biar lebih terkontrol.
    """
    s = _sanitize_caption(s, 300)
    if not s:
        return []
    words = s.split()
    lines = []
    cur = []
    cur_len = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur_len + add > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += add
    if cur:
        lines.append(" ".join(cur))
    return lines


def burn_srt(
    *,
    run_ffmpeg,                 # function(cmd:list[str]) -> None (punya kamu di long_video.py)
    video_mp4: str,
    audio_in: str,
    srt_path: str,
    out_mp4: str,
    font: str = "DejaVu Sans",
    fontsize: int = 42,
    margin_v: int = 80,
    outline: int = 2,
    shadow: int = 1,
) -> None:
    """
    Burn-in SRT ke video (hard subtitle).
    """
    srt_abs = os.path.abspath(srt_path).replace("\\", "\\\\").replace(":", "\\:")
    style = (
        f"Fontname={font},"
        f"Fontsize={int(fontsize)},"
        f"Outline={int(outline)},"
        f"Shadow={int(shadow)},"
        f"MarginV={int(margin_v)}"
    )
    vf = f"subtitles='{srt_abs}':force_style='{style}'"

    run_ffmpeg([
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-nostats",
        "-y",
        "-i", video_mp4,
        "-i", audio_in,
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_mp4
    ])


def _ass_time(t: float) -> str:
    # ASS format: H:MM:SS.cc (centiseconds)
    if t < 0:
        t = 0.0
    hh = int(t // 3600)
    mm = int((t % 3600) // 60)
    ss = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"


def write_ass_karaoke(
    *,
    segments_text: List[str],
    durations_sec: List[float],
    out_ass: str,
    font: str = "DejaVu Sans",
    fontsize: int = 44,
    margin_v: int = 70,
    outline: int = 3,
    shadow: int = 1,
    # warna ASS pakai BGR hex: &HBBGGRR&
    # kuning (00FFFF) = &H00FFFF& (R=FF G=FF B=00)
    active_color: str = "&H00FFFF&",   # kuning untuk kata aktif
    idle_color: str = "&HFFFFFF&",     # putih untuk kata belum aktif
    border_color: str = "&H000000&",   # hitam outline
    window_sec: float = 5.0,           # highlight karaoke jalan selama N detik awal segmen
) -> str:
    """
    Buat ASS karaoke:
    - Text ada di bawah tengah (Alignment=2)
    - Kata berubah jadi active_color secara progresif (karaoke \k)
    - Tanpa forced-alignment: timing dibagi rata per kata dalam window_sec
    """

    if len(segments_text) != len(durations_sec):
        raise ValueError("segments_text length != durations_sec length")

    _ensure_dir(os.path.dirname(out_ass))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
; Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kara,{font},{fontsize},{active_color},{idle_color},{border_color},&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,60,60,{margin_v},1

[Events]
; Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = [header]

    t0 = 0.0
    for seg_text, dur in zip(segments_text, durations_sec):
        dur = max(0.2, float(dur))
        t1 = t0 + dur

        fact = _sanitize_caption(extract_fact_line(seg_text), 220)
        if not fact:
            fact = "FAKTA:"

        # karaoke hanya di window awal, setelah itu biarkan tetap tampil idle/active full
        win = min(float(window_sec), dur)
        win = max(win, 0.6)

        words = _words(fact)
        if not words:
            words = ["FAKTA"]

        # Durasi centiseconds per kata
        total_cs = int(round(win * 100))
        per_cs = max(8, total_cs // max(1, len(words)))  # min 0.08s per kata

        # Karaoke text: {\k<cs>}word ...
        kara = "".join([f"{{\\k{per_cs}}}{w} " for w in words]).strip()

        start = _ass_time(t0)
        end = _ass_time(t1)

        # tampilkan sepanjang segmen; karaoke hanya progress selama win
        # setelah win habis, renderer biasanya sudah semua jadi PrimaryColour (active_color)
        # jadi tetap kebaca sampai segmen berakhir.
        events.append(f"Dialogue: 0,{start},{end},Kara,,0,0,0,,{kara}\n")

        t0 = t1

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(events)

    return out_ass
def write_ass_fact_karaoke_2lines(
    *,
    segments_text: List[str],
    durations_sec: List[float],
    out_ass: str,
    font: str = "DejaVu Sans",
    fontsize: int = 38,          # lebih kecil biar aman
    margin_v: int = 110,         # bawah
    margin_lr: int = 140,        # kiri/kanan biar gak kepotong
    outline: int = 3,
    shadow: int = 1,
    active_color: str = "&H00FFFF&",   # kuning
    idle_color: str = "&HFFFFFF&",     # putih
    border_color: str = "&H000000&",   # hitam
    max_chars: int = 40,               # kontrol wrap
    fact_window_sec: float = 4.0,      # highlight fakta berjalan 4 detik pertama segmen
) -> str:
    """
    - 2 baris max (wrap sendiri)
    - Highlight (karaoke) hanya untuk FAKTA di awal segmen
    - Penjelasan tampil bergantian (putih) sepanjang sisa segmen
    """
    if len(segments_text) != len(durations_sec):
        raise ValueError("segments_text length != durations_sec length")

    _ensure_dir(os.path.dirname(out_ass))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
; Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{font},{fontsize},{idle_color},{idle_color},{border_color},&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1
Style: Kara,{font},{fontsize},{active_color},{idle_color},{border_color},&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
; Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = [header]

    t0 = 0.0
    for seg_text, seg_dur in zip(segments_text, durations_sec):
        seg_dur = max(0.8, float(seg_dur))
        t1 = t0 + seg_dur

        # ambil semua line segmen
        raw_lines = [x.strip() for x in (seg_text or "").splitlines() if x.strip()]
        if not raw_lines:
            raw_lines = ["FAKTA:"]

        # pisahkan fakta + penjelasan
        fact_line = ""
        rest_lines: List[str] = []
        for x in raw_lines:
            if not fact_line and x.lower().startswith("fakta:"):
                fact_line = x
            else:
                rest_lines.append(x)

        if not fact_line:
            fact_line = raw_lines[0]
            rest_lines = raw_lines[1:]

        # wrap fakta jadi max 2 baris
        fact_chunks = _to_two_line_chunks([fact_line], max_chars=max_chars)
        fact_text_2lines = fact_chunks[0] if fact_chunks else "FAKTA:"

        # karaoke window (awal segmen)
        win = min(float(fact_window_sec), seg_dur)
        win = max(0.8, win)

        # 1) Karaoke fakta selama 'win'
        # karaoke token berbasis kata, tapi tetap tampil 2 baris (kita tampilkan 2 baris tanpa karaoke token per-line)
        # kompromi: karaoke tetap jalan, text tetap 2 baris => kita karaoke-kan plain words,
        # dan tampilkan versi 2 baris sebagai "Base" setelah win agar tetap kebaca.
        plain_fact = fact_text_2lines.replace("\\N", " ")
        words = _words(plain_fact) or ["FAKTA"]
        total_cs = int(round(win * 100))
        per_cs = max(8, total_cs // max(1, len(words)))
        kara = "".join([f"{{\\k{per_cs}}}{w} " for w in words]).strip()

        events.append(
            f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t0+win)},Kara,,0,0,0,,{kara}\n"
        )

        # 2) Setelah win, tampilkan fakta 2 baris (putih) sebentar biar tidak “hilang”
        hold_end = min(t0 + win + 0.8, t1)  # 0.8 detik
        if hold_end > t0 + win:
            events.append(
                f"Dialogue: 0,{_ass_time(t0+win)},{_ass_time(hold_end)},Base,,0,0,0,,{fact_text_2lines}\n"
            )

        # 3) Penjelasan: wrap jadi chunk 2 baris, bagi durasi proporsional sisa waktu
        cur = hold_end
        remain = max(0.0, t1 - cur)

        explain_chunks = _to_two_line_chunks(rest_lines, max_chars=max_chars)
        if explain_chunks and remain >= 0.8:
            durs = _alloc_durations(remain, explain_chunks)
            for ch, cd in zip(explain_chunks, durs):
                st = cur
                en = min(cur + cd, t1)
                if en - st < 0.6:
                    break
                events.append(
                    f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Base,,0,0,0,,{ch}\n"
                )
                cur = en
        else:
            # kalau tidak ada penjelasan, tahan fakta sampai segmen selesai
            if cur < t1:
                events.append(
                    f"Dialogue: 0,{_ass_time(cur)},{_ass_time(t1)},Base,,0,0,0,,{fact_text_2lines}\n"
                )

        t0 = t1

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(events)

    return out_ass


def burn_subs(
    *,
    run_ffmpeg,
    video_mp4: str,
    audio_in: str,
    subs_path: str,
    out_mp4: str,
) -> None:
    """
    Burn ASS/SRT ke video (hard subtitle).
    """
    subs_abs = os.path.abspath(subs_path).replace("\\", "\\\\").replace(":", "\\:")
    vf = f"subtitles='{subs_abs}'"

    run_ffmpeg([
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-nostats",
        "-y",
        "-i", video_mp4,
        "-i", audio_in,
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_mp4
    ])

def _split_caption_lines(seg_text: str, max_chars: int = 38) -> List[str]:
    raw = [x.strip() for x in (seg_text or "").splitlines() if x.strip()]
    if not raw:
        return []

    fact = None
    rest = []
    for x in raw:
        if x.lower().startswith("fakta:") and fact is None:
            fact = x
        else:
            rest.append(x)

    out = []
    if fact:
        out.append(fact)
    out.extend(rest)

    # Reflow: jadikan beberapa caption "chunk" yang tiap chunk max 2 baris
    chunks: List[str] = []
    for x in out:
        wrapped = _wrap_text(x, max_chars=max_chars)
        if not wrapped:
            continue
        # gabung max 2 baris per event (biar gak kebanyakan)
        if len(wrapped) <= 2:
            chunks.append("\\N".join(wrapped))
        else:
            # pecah per 2 baris
            for i in range(0, len(wrapped), 2):
                chunks.append("\\N".join(wrapped[i:i+2]))

    return chunks

def _alloc_durations(total: float, texts: List[str]) -> List[float]:
    """
    Alokasi durasi berdasarkan panjang text (proxy).
    """
    if not texts:
        return []
    weights = [max(1.0, len(t.replace("\\N", " "))) for t in texts]
    sw = sum(weights)
    out = [total * (w / sw) for w in weights]
    # minimal 1.0s biar kebaca
    out = [max(1.0, x) for x in out]
    # normalize lagi agar total sama
    s2 = sum(out)
    if s2 > 0:
        out = [total * (x / s2) for x in out]
    return out

def write_ass_karaoke_chunks(
    *,
    segments_text: List[str],
    durations_sec: List[float],
    out_ass: str,
    font: str = "DejaVu Sans",
    fontsize: int = 44,
    margin_v: int = 95,
    outline: int = 3,
    shadow: int = 1,
    active_color: str = "&H00FFFF&",
    idle_color: str = "&HFFFFFF&",
    border_color: str = "&H000000&",
    max_chars: int = 38,
) -> str:
    """
    Semua caption per segmen (FAKTA + penjelasan) tampil sebagai beberapa chunk,
    masing-masing karaoke (warna aktif jalan).
    """
    if len(segments_text) != len(durations_sec):
        raise ValueError("segments_text length != durations_sec length")

    _ensure_dir(os.path.dirname(out_ass))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Style: Kara,{font},{fontsize},{active_color},{idle_color},{border_color},&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,80,80,{margin_v},1

[Events]
"""
    events: List[str] = [header]

    t0 = 0.0
    for seg_text, seg_dur in zip(segments_text, durations_sec):
        seg_dur = max(0.5, float(seg_dur))
        t1 = t0 + seg_dur

        chunks = _split_caption_lines(seg_text, max_chars=max_chars)
        if not chunks:
            chunks = ["FAKTA:"]

        chunk_durs = _alloc_durations(seg_dur, chunks)

        cur = t0
        for ch, cd in zip(chunks, chunk_durs):
            start = _ass_time(cur)
            end = _ass_time(min(cur + cd, t1))

            # karaoke per kata (kata di baris 1 dan 2 tetap dihitung)
            plain = ch.replace("\\N", " ")
            words = _words(plain) or ["FAKTA"]
            total_cs = max(50, int(round(cd * 100)))
            per_cs = max(6, total_cs // max(1, len(words)))

            # render text tetap 2 baris sesuai ch, tapi \k based on words plain
            # trik: karaoke text harus punya kata-kata; linebreak kita sisipkan manual kalau perlu
            # simple: pakai ch tapi hilangkan \N pada karaoke tokens (ASS tidak mudah karaoke multi-line tokenized)
            # solusi aman: karaoke on one line, tapi tampil 2 baris via \N tidak konsisten per-kata.
            # jadi: kita tampilkan ch sebagai 2 baris tanpa per-kata per-line, karaoke tetap jalan.
            kara = "".join([f"{{\\k{per_cs}}}{w} " for w in words]).strip()

            events.append(f"Dialogue: 0,{start},{end},Kara,,0,0,0,,{kara}\n")

            cur += cd

        t0 = t1

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(events)

    return out_ass


def write_ass_full_segment(
    *,
    segments_text: List[str],
    durations_sec: List[float],
    out_ass: str,
    font: str = "DejaVu Sans",
    fontsize: int = 44,
    margin_v: int = 90,
    outline: int = 3,
    shadow: int = 1,
    active_color: str = "&H00FFFF&",   # kuning
    idle_color: str = "&HFFFFFF&",     # putih
    border_color: str = "&H000000&",   # outline hitam
    karaoke_first_line: bool = True,
    karaoke_window_sec: float = 4.0,   # karaoke jalan di awal segmen untuk baris pertama saja
) -> str:
    """
    ASS caption untuk SEMUA isi segmen.
    - Baris 1 (biasanya FAKTA:) bisa karaoke (warna aktif beda)
    - Baris berikutnya tampil bergantian sampai segmen selesai
    """

    if len(segments_text) != len(durations_sec):
        raise ValueError("segments_text length != durations_sec length")

    _ensure_dir(os.path.dirname(out_ass))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
; Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{font},{fontsize},{idle_color},{idle_color},{border_color},&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,70,70,{margin_v},1
Style: Kara,{font},{fontsize},{active_color},{idle_color},{border_color},&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,70,70,{margin_v},1

[Events]
; Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = [header]

    t0 = 0.0
    for seg_text, dur in zip(segments_text, durations_sec):
        dur = max(0.2, float(dur))
        t1 = t0 + dur

        lines = _split_caption_lines(seg_text)
        if not lines:
            lines = ["FAKTA:"]

        # alokasi waktu:
        # - kalau karaoke_first_line: baris1 dapat karaoke_window (max dur)
        # - sisanya dibagi rata untuk sisa waktu
        kara_win = min(float(karaoke_window_sec), dur) if karaoke_first_line else 0.0
        kara_win = max(kara_win, 0.0)

        rest_lines = lines[1:] if len(lines) > 1 else []
        rest_time = max(0.0, dur - kara_win)

        # 1) baris pertama
        if karaoke_first_line and lines:
            fact = lines[0]
            words = _words(fact) or ["FAKTA"]
            total_cs = max(60, int(round(max(0.6, kara_win) * 100)))
            per_cs = max(8, total_cs // max(1, len(words)))
            kara = "".join([f"{{\\k{per_cs}}}{w} " for w in words]).strip()

            start = _ass_time(t0)
            end = _ass_time(t0 + max(0.6, kara_win))
            events.append(f"Dialogue: 0,{start},{end},Kara,,0,0,0,,{kara}\n")

        # 2) setelah karaoke: tampilkan baris-baris bergantian sampai segmen selesai
        cur = t0 + kara_win
        if rest_lines:
            per = rest_time / len(rest_lines) if rest_time > 0 else 0.8
            per = max(0.8, per)  # minimal 0.8s per caption biar kebaca
            for ln in rest_lines:
                start = _ass_time(cur)
                end = _ass_time(min(cur + per, t1))
                events.append(f"Dialogue: 0,{start},{end},Base,,0,0,0,,{ln}\n")
                cur += per

        # kalau tidak ada rest_lines, tahan fact sampai segmen berakhir
        if not rest_lines:
            start = _ass_time(t0 + kara_win)
            end = _ass_time(t1)
            events.append(f"Dialogue: 0,{start},{end},Base,,0,0,0,,{lines[0]}\n")

        t0 = t1

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(events)

    return out_ass
