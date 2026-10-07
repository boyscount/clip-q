"""Script -> Thai voiceover + caption cues.

Each line of the script is synthesised on its own. That buys two things:
  - an explicit pause between beats, which no SSML support is needed for
  - caption lines that match the script lines exactly

The Thai edge-tts voices do not emit WordBoundary events (the English ones
do), so there is no word-level timing to align captions against. Long lines
are therefore split proportionally by character count, which is accurate
enough at a 2-3 chunk granularity and needs no extra model.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from . import ff

VOICES = {
    "female": "th-TH-PremwadeeNeural",
    "male": "th-TH-NiwatNeural",
}

PAUSE = 0.28  # seconds of silence between lines
MAX_CHARS = 28  # longer than this and a caption line wraps badly at 1080 wide
ATTEMPTS = 6  # the free endpoint drops requests at random, not just in bursts
BACKOFF = 1.5  # seconds, multiplied by the attempt number
THROTTLE = 0.8  # seconds between requests


@dataclass
class Cue:
    text: str
    start: float
    end: float


async def _say(text: str, voice: str, out: Path, rate: str) -> None:
    """Synthesise one line, retrying through the endpoint's rate limiting."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            audio = bytearray()
            async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
            if audio:
                out.write_bytes(bytes(audio))
                return
            last = RuntimeError("empty audio")
        except Exception as exc:  # noqa: BLE001 - endpoint throws several types
            last = exc
        print(f"    retry {attempt + 1}/{ATTEMPTS}: {out.name}", flush=True)
        await asyncio.sleep(BACKOFF * (attempt + 1))
    raise RuntimeError(f"TTS ล้มเหลวหลังลอง {ATTEMPTS} ครั้ง: {last}") from last


def _trim(path: Path) -> Path:
    """Cut the dead air edge-tts leaves at both ends of every line.

    It ships roughly a second of silence per line, which on an 8-line script
    is ten seconds of nothing. PAUSE below is the gap we actually want.
    """
    gate = "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-45dB:detection=peak"
    out = path.with_name(path.stem + "_t.mp3")
    ff.run([
        "-i", str(path),
        "-af", f"{gate},areverse,{gate},areverse",
        str(out),
    ])
    out.replace(path)
    return path


async def _say_all(lines: list[str], voice: str, workdir: Path, rate: str) -> list[Path]:
    paths = []
    for i, line in enumerate(lines):
        path = workdir / f"line_{i:02d}.mp3"
        await _say(line, voice, path, rate)
        _trim(path)
        paths.append(path)
        await asyncio.sleep(THROTTLE)
    return paths


def _chunk(text: str) -> list[str]:
    """Break a caption line into readable pieces, preferring space boundaries."""
    if len(text) <= MAX_CHARS:
        return [text]

    chunks: list[str] = []
    current = ""
    for word in text.split(" "):
        while len(word) > MAX_CHARS:  # a single unbroken Thai run
            if current:
                chunks.append(current)
                current = ""
            chunks.append(word[:MAX_CHARS])
            word = word[MAX_CHARS:]
        candidate = f"{current} {word}".strip()
        if len(candidate) > MAX_CHARS:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _cues_for(text: str, start: float, duration: float) -> list[Cue]:
    chunks = _chunk(text)
    if len(chunks) == 1:
        return [Cue(text, start, start + duration)]

    total_chars = sum(len(c) for c in chunks)
    cues: list[Cue] = []
    clock = start
    for chunk in chunks:
        span = duration * len(chunk) / total_chars
        cues.append(Cue(chunk, clock, clock + span))
        clock += span
    cues[-1].end = start + duration
    return cues


def synthesize(
    script: str,
    workdir: Path,
    voice: str = "female",
    rate: str = "+8%",
) -> tuple[Path, list[Cue], float]:
    """Render the script to workdir/voice.mp3 and return (audio, cues, duration)."""
    lines = [ln.strip() for ln in script.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("script ว่าง")

    workdir.mkdir(parents=True, exist_ok=True)
    paths = asyncio.run(_say_all(lines, VOICES.get(voice, voice), workdir, rate))
    durations = [ff.duration(p) for p in paths]

    # Lay the lines out on one timeline with PAUSE between them.
    cues: list[Cue] = []
    offsets: list[float] = []
    clock = 0.0
    for line, dur in zip(lines, durations):
        offsets.append(clock)
        cues.extend(_cues_for(line, clock, dur))
        clock += dur + PAUSE
    total = clock - PAUSE

    out = workdir / "voice.mp3"
    _mixdown(paths, offsets, total, out)
    return out, cues, total


def _mixdown(paths: list[Path], offsets: list[float], total: float, out: Path) -> None:
    """Place each line on a silent bed at its offset."""
    args: list[str] = [
        "-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=24000:cl=mono",
    ]
    for path in paths:
        args += ["-i", str(path)]

    chains = [
        f"[{i + 1}:a]adelay={int(off * 1000)}|{int(off * 1000)}[d{i}]"
        for i, off in enumerate(offsets)
    ]
    mix_in = "[0:a]" + "".join(f"[d{i}]" for i in range(len(paths)))
    chains.append(
        f"{mix_in}amix=inputs={len(paths) + 1}:normalize=0:duration=longest[out]"
    )

    args += [
        "-filter_complex", ";".join(chains),
        "-map", "[out]",
        "-ac", "1", "-ar", "24000", "-b:a", "96k",
        str(out),
    ]
    ff.run(args)
