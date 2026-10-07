"""Render real Thai voiceovers for the ClipQueue preview player.

Produces app/assets/pv_<product>_<format>.mp3 plus app/preview-manifest.json,
which carries the exact spoken lines and their real start/end times. The web
app reads its caption timing from that manifest, so the text on screen can
never drift from the audio.

    python tools/make_preview_audio.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import ff, script_gen, speech, voice  # noqa: E402

OUT_AUDIO = ROOT / "app" / "assets"
OUT_MANIFEST = ROOT / "app" / "preview-manifest.json"

# Mirrors the PRODUCTS entries in app/clipqueue.html for the ids listed here.
PRODUCTS = {
    "P-1042": {
        "name": "หูฟังบลูทูธ TWS รุ่น Pro Max", "price": 399, "was": 890, "free": True,
        "bullets": ["เสียงเบสหนักแน่น ตัดเสียงรบกวนรอบข้าง",
                    "แบตอึด ฟังต่อเนื่อง 8 ชั่วโมง",
                    "กันเหงื่อ IPX5 ใส่วิ่งได้สบาย"],
    },
    "P-0871": {
        "name": "ครีมกันแดด SPF50+ PA++++", "price": 249, "was": 420, "free": True,
        "bullets": ["เนื้อบางเบา ไม่วอกไม่เหนียว",
                    "กันน้ำกันเหงื่อ อยู่ได้ทั้งวัน",
                    "ทาแล้วแต่งหน้าต่อได้เลย"],
    },
    "P-0520": {
        "name": "ไฟ LED ติดห้อง RGB ยาว 5 เมตร", "price": 129, "was": 299, "free": False,
        "bullets": ["เปลี่ยนสีได้ 16 ล้านสี ผ่านแอป",
                    "มีกาวสองหน้าในตัว ติดเองได้",
                    "ตัดสายสั้นได้ตามมุมห้อง"],
    },
    "P-0333": {
        "name": "ขวดน้ำเก็บความเย็น 1 ลิตร", "price": 189, "was": 350, "free": False,
        "bullets": ["เก็บเย็นได้ 18 ชั่วโมง",
                    "สเตนเลสสองชั้น ไม่มีเหงื่อขวด",
                    "ฝาเกลียวแน่น ไม่หกในกระเป๋า"],
    },
}

FORMATS = ["quick", "show", "story"]

def lines_for(product: dict, fmt: str) -> list[str]:
    """Seed 0 of the length-aware planner — the script the web app shows."""
    return script_gen.plan_lines(product, fmt, seed=0)


def main() -> int:
    ff.require()
    OUT_AUDIO.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}

    jobs = [(pid, fmt) for pid in PRODUCTS for fmt in FORMATS]
    for n, (pid, fmt) in enumerate(jobs, start=1):
        product = PRODUCTS[pid]
        lines = lines_for(product, fmt)
        key = f"{pid}_{fmt}"
        target = script_gen.TARGETS[fmt]
        print(f"[{n}/{len(jobs)}] {key} · {len(lines)} บรรทัด · "
              f"ประเมิน {speech.estimate(lines):.1f}s (เป้า {target}s)", flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            audio, cues, total = voice.synthesize("\n".join(lines), Path(tmp))
            dest = OUT_AUDIO / f"pv_{key}.mp3"
            shutil.copy(audio, dest)

        manifest[key] = {
            "src": f"assets/pv_{key}.mp3",
            "duration": round(total, 2),
            "target": target,
            "sizeKB": round(dest.stat().st_size / 1024),
            "lines": lines,
            "cues": [
                {"t": round(c.start, 2), "e": round(c.end, 2), "text": c.text}
                for c in cues
            ],
        }
        print(f"        จริง {total:.1f}s · ห่างเป้า {total - target:+.1f}s · "
              f"{manifest[key]['sizeKB']} KB", flush=True)

    manifest["_model"] = speech.model()
    OUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    clips = {k: v for k, v in manifest.items() if not k.startswith("_")}
    total_kb = sum(m["sizeKB"] for m in clips.values())
    drift = [abs(m["duration"] - m["target"]) for m in clips.values()]
    print(f"\nเสร็จ {len(clips)} คลิป · รวม {total_kb / 1024:.1f} MB")
    print(f"ห่างจากเป้าเฉลี่ย {sum(drift) / len(drift):.1f}s · สูงสุด {max(drift):.1f}s")
    print(OUT_MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
