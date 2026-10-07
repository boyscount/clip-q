"""POC pipeline: product.json -> vertical review clip.

    python -m src.main product.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import ff, render, script_gen, subtitle, voice


def _log(step: str, started: float) -> float:
    print(f"  {step:<28} {time.monotonic() - started:5.1f}s", flush=True)
    return time.monotonic()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="สร้างคลิปรีวิวสินค้าแนวตั้งจาก product.json")
    ap.add_argument("product", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out/clip.mp4"))
    ap.add_argument("--voice", default="female", choices=["female", "male"])
    ap.add_argument("--mode", default="template", choices=["template", "llm"])
    ap.add_argument("--rate", default="+8%", help="ความเร็วเสียงพากย์ เช่น +15%%")
    ap.add_argument("--seed", type=int, default=None, help="ล็อกการสุ่ม hook ให้ได้ผลเดิม")
    ap.add_argument("--bgm", type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="ไม่ลบไฟล์ชั่วคราว")
    args = ap.parse_args(argv)

    ff.require()

    product = json.loads(args.product.read_text(encoding="utf-8"))
    base = args.product.parent
    images = [base / p for p in product["images"]]
    missing = [p for p in images if not p.exists()]
    if missing:
        print("หารูปไม่เจอ: " + ", ".join(str(p) for p in missing), file=sys.stderr)
        return 1

    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    workdir = out.parent / "work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    t0 = time.monotonic()
    print(f"\n{product['name']}")

    script = script_gen.build_script(product, args.mode, args.seed)
    (workdir / "script.txt").write_text(script, encoding="utf-8")
    t = _log(f"script ({args.mode})", t0)

    audio, cues, total = voice.synthesize(script, workdir, args.voice, args.rate)
    t = _log(f"voiceover ({total:.1f}s)", t)

    ass = subtitle.write_ass(workdir / "captions.ass", cues, product, total)
    t = _log(f"captions ({len(cues)} cues)", t)

    visual = render.build_visual(images, total, workdir)
    t = _log(f"visual ({len(images)} shots)", t)

    render.mux(visual, audio, ass, out, args.bgm)
    _log("mux + burn-in", t)

    print(f"\nเสร็จแล้ว  {out}  ({out.stat().st_size / 1e6:.1f} MB, {total:.1f}s)")
    print("\n--- สคริปต์ที่พูด ---")
    print(script)

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
