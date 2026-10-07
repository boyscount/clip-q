"""Thin ffmpeg / ffprobe wrappers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def require() -> None:
    if not FFMPEG or not FFPROBE:
        raise SystemExit(
            "หา ffmpeg/ffprobe ไม่เจอใน PATH\n"
            "ติดตั้งด้วย:  winget install Gyan.FFmpeg\n"
            "แล้วเปิด terminal ใหม่"
        )


def run(args: list[str], cwd: Path | None = None) -> None:
    require()
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr.strip()}")


def duration(path: Path) -> float:
    require()
    proc = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(json.loads(proc.stdout)["format"]["duration"])
