
import sys, json
sys.stdout.reconfigure(line_buffering=True)
from ytlong.engine import build_long_video
with open("temp_long_process.json", "r", encoding="utf-8") as f:
    data = json.load(f)
build_long_video(
    data,
    tts_engine="gtts",
    voice_id=None,
    no_watermark=False,
    watermark_text='@AutoFactID',
    hook_subtitle='FAKTA CEPAT'
)
