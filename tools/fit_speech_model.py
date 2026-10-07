"""Fit the Thai speech-rate model from rendered previews.

Reads app/preview-manifest.json (which carries the exact lines and the measured
duration of every clip) and solves the least squares fit for

    duration = per_line * n_lines + per_char * n_chars

then writes app/speech-model.json, which src/speech.py picks up.

    python tools/fit_speech_model.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "app" / "preview-manifest.json"
OUT = ROOT / "app" / "speech-model.json"


def fit(rows: list[tuple[int, int, float]]) -> tuple[float, float]:
    s11 = sum(n * n for n, _, _ in rows)
    s12 = sum(n * c for n, c, _ in rows)
    s22 = sum(c * c for _, c, _ in rows)
    s1y = sum(n * d for n, _, d in rows)
    s2y = sum(c * d for _, c, d in rows)
    det = s11 * s22 - s12 * s12
    if not det:
        raise SystemExit("ข้อมูลไม่พอสำหรับ fit")
    return (s1y * s22 - s2y * s12) / det, (s11 * s2y - s12 * s1y) / det


def main() -> int:
    if not MANIFEST.exists():
        print("ยังไม่มี preview-manifest.json — รัน tools/make_preview_audio.py ก่อน", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    clips = {k: v for k, v in manifest.items() if isinstance(v, dict) and "lines" in v}
    if not clips:
        print("manifest ไม่มีฟิลด์ lines — เรนเดอร์ใหม่ด้วยเวอร์ชันล่าสุด", file=sys.stderr)
        return 1

    rows = [(len(v["lines"]), sum(len(l) for l in v["lines"]), v["duration"]) for v in clips.values()]
    per_line, per_char = fit(rows)
    errors = [abs(per_line * n + per_char * c - d) for n, c, d in rows]

    OUT.write_text(json.dumps({
        "per_line": round(per_line, 4),
        "per_char": round(per_char, 5),
        "voice": "th-TH-PremwadeeNeural",
        "rate": "+8%",
        "samples": len(rows),
        "mean_error_sec": round(statistics.mean(errors), 3),
    }, indent=1), encoding="utf-8")

    print(f"per_line {per_line:.4f}s · per_char {per_char:.5f}s ({1 / per_char:.1f} ตัวอักษร/วินาที)")
    print(f"คลาดเคลื่อนเฉลี่ย {statistics.mean(errors):.2f}s สูงสุด {max(errors):.2f}s จาก {len(rows)} คลิป")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
