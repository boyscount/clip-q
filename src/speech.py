"""How long a Thai line takes to say.

duration = per_line * n_lines + per_char * n_chars

per_char is the speaking rate. per_line is the fixed cost of each line: the
PAUSE between lines plus whatever silence survives trimming. Both are fitted
from real renders by tools/fit_speech_model.py; the defaults below are the
measured values for th-TH-PremwadeeNeural at +8%.
"""

from __future__ import annotations

import json
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "app" / "speech-model.json"

DEFAULT = {"per_line": 0.52, "per_char": 0.0660, "voice": "th-TH-PremwadeeNeural", "rate": "+8%"}


def model() -> dict:
    try:
        return {**DEFAULT, **json.loads(MODEL_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT)


def estimate(lines: list[str], m: dict | None = None) -> float:
    m = m or model()
    return m["per_line"] * len(lines) + m["per_char"] * sum(len(l) for l in lines)
