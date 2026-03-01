import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


class SceneBuildError(RuntimeError):
    pass


@dataclass
class Scene:
    kind: str               # hook|scene|cta
    duration: float         # seconds
    clip_path: Optional[str] = None  # local clip file path
    text_overlay: Optional[str] = None


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise SceneBuildError(f"Command gagal: {' '.join(cmd)}\n{p.stderr[-2000:]}")


def probe_video_duration_seconds(video_path: Path) -> float:
    if not video_path.exists():
        raise SceneBuildError(f"Video tidak ditemukan: {video_path}")
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise SceneBuildError(f"ffprobe gagal: {p.stderr[-2000:]}")
    try:
        return float(p.stdout.strip())
    except Exception as e:
        raise SceneBuildError(f"Gagal parse durasi video: {e}")


def build_scenes(
    local_clips: List[Path],
    target_total: float = 30.0,
    hook_text: str = "",
    cta_text: str = "",
    hook_range: Tuple[float, float] = (3.0, 5.0),
    scene_range: Tuple[float, float] = (5.0, 7.0),
    cta_range: Tuple[float, float] = (3.0, 5.0),
    seed: Optional[int] = None,
) -> List[Scene]:
    """
    Creates:
    Hook, 3 scenes, CTA = 5 scenes total.
    Durations are chosen inside ranges and then normalized to target_total.
    Each scene (except hook/cta overlays) gets a clip assigned.
    """
    if not local_clips:
        raise SceneBuildError("Tidak ada clip lokal untuk disusun.")

    rng = random.Random(seed)

    hook_d = rng.uniform(*hook_range)
    cta_d = rng.uniform(*cta_range)
    mid = target_total - (hook_d + cta_d)
    if mid < 10:
        # ensure sane
        hook_d = min(hook_d, 3.5)
        cta_d = min(cta_d, 3.5)
        mid = max(10.0, target_total - (hook_d + cta_d))

    # three scenes
    s1 = rng.uniform(*scene_range)
    s2 = rng.uniform(*scene_range)
    s3 = rng.uniform(*scene_range)
    total_mid = s1 + s2 + s3
    scale = mid / total_mid
    s1 *= scale
    s2 *= scale
    s3 *= scale

    scenes: List[Scene] = [
        Scene(kind="hook", duration=hook_d, clip_path=str(local_clips[0]), text_overlay=hook_text.strip() or None),
        Scene(kind="scene", duration=s1, clip_path=str(local_clips[1 % len(local_clips)])),
        Scene(kind="scene", duration=s2, clip_path=str(local_clips[2 % len(local_clips)])),
        Scene(kind="scene", duration=s3, clip_path=str(local_clips[3 % len(local_clips)])),
        Scene(kind="cta", duration=cta_d, clip_path=str(local_clips[4 % len(local_clips)]), text_overlay=cta_text.strip() or None),
    ]

    # Final normalization to exactly target_total (fix rounding)
    cur = sum(s.duration for s in scenes)
    if abs(cur - target_total) > 1e-3:
        scenes[-1].duration += (target_total - cur)
        scenes[-1].duration = max(1.0, scenes[-1].duration)

    return scenes
