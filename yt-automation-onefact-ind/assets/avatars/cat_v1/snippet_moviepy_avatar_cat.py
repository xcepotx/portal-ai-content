# snippet_moviepy_avatar_cat.py (example only)
import json
from moviepy.editor import ImageClip, CompositeVideoClip, AudioFileClip

audio = AudioFileClip("tts.mp3")
base = ImageClip("char_base_cat.png").set_duration(audio.duration)

with open("mouth_cues.json","r",encoding="utf-8") as f:
    cues = json.load(f).get("mouthCues", [])

mouth_clips = []
for cue in cues:
    v = cue.get("value","X")
    start = float(cue.get("start",0))
    end = float(cue.get("end",start))
    if end <= start:
        continue
    mouth = ImageClip(f"mouth_{v}.png").set_start(start).set_duration(end-start)
    mouth_clips.append(mouth)

avatar = CompositeVideoClip([base] + mouth_clips, size=(512,512)).set_duration(audio.duration)
