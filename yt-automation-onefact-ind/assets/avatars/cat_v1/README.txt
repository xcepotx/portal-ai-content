CAT AVATAR PACK (2D PNG, transparent)
====================================
Files:
- char_base_cat.png    : base cat character (no mouth)
- mouth_A.png ...      : Rhubarb mouth shapes (A,B,C,D,E,F,G,H,X)
- preview_grid.png     : quick preview
- snippet_moviepy_avatar_cat.py : tiny code example

Usage (Rhubarb):
  rhubarb -f json -o mouth_cues.json tts.mp3
  (if wav needed)
  ffmpeg -y -i tts.mp3 -ac 1 -ar 48000 tts.wav
  rhubarb -f json -o mouth_cues.json tts.wav

Then composite base + mouth clips according to mouth_cues.json and overlay to your final video
position ("right","bottom") at ~20% of video height.
