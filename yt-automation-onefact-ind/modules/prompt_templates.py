def generate_hook_prompt(text: str) -> str:
    return f"""
Buat 5 hook pembuka yang sangat menarik untuk konten short video (YouTube Shorts / TikTok).

Kriteria:
- Bahasa Indonesia
- 1 kalimat pendek per hook
- Emosional, memancing rasa penasaran
- Cocok untuk 3 detik pertama video

Konten sumber:
{text}
"""


def generate_script_prompt(text: str) -> str:
    return f"""
Buat script video pendek 30–45 detik dari konten berikut.

Format:
- Hook
- Isi utama (bullet)
- Closing singkat

Gunakan bahasa Indonesia yang natural dan engaging.

Konten:
{text}
"""


def seo_title_prompt(text: str) -> str:
    return f"""
Buat 10 judul SEO-friendly dan clickable.

Kriteria:
- Bahasa Indonesia
- Maksimal 70 karakter
- Cocok untuk YouTube / TikTok
- Mengandung rasa penasaran

Konten:
{text}
"""


def viral_rewrite_prompt(text: str) -> str:
    return f"""
Tulis ulang konten berikut agar terasa lebih viral.

Kriteria:
- Bahasa Indonesia
- Santai tapi tetap informatif
- Tambahkan emotional trigger
- Cocok untuk konten short

Konten asli:
{text}
"""


def description_cta_prompt(text: str) -> str:
    return f"""
Buat deskripsi video + CTA yang kuat.

Format:
- Deskripsi singkat (2–3 paragraf pendek)
- CTA di akhir (subscribe / follow / komentar)

Bahasa Indonesia, cocok untuk YouTube & TikTok.

Konten:
{text}
"""
