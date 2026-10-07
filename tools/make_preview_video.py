"""Render real mp4 previews from the voiceovers already on disk.

Reuses app/assets/pv_*.mp3 and the cue times in app/preview-manifest.json, so
no TTS call is made here — only ffmpeg work. Shot images are generated to match
the gradient the web app paints for the same product, so the clip and the card
look like the same thing.

    python tools/make_preview_video.py            # 9:16 for every clip
    python tools/make_preview_video.py --all      # plus 4:5 and 1:1 for P-1042
"""

from __future__ import annotations

import colorsys
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from src import ff, render, subtitle  # noqa: E402
from src.voice import Cue  # noqa: E402

APP = ROOT / "app"
ASSETS = APP / "assets"
MANIFEST = APP / "preview-manifest.json"
FONT_PATH = r"C:\Windows\Fonts\LeelaUIb.ttf"

# Keep in step with ASPECTS in app/clipqueue.html. Rendered at 2/3 of the app's
# nominal size: plenty for a preview, a third of the bytes.
ASPECTS = {
    "9:16": (720, 1280),
    "4:5": (720, 900),
    "1:1": (720, 720),
}
CRF = 26  # preview quality; the real pipeline ships CRF 20

# Mirrors PRODUCTS in app/clipqueue.html
META = {
    "P-1042": {"hue": 214, "shots": 6, "name": "หูฟังบลูทูธ TWS รุ่น Pro Max", "price": 399, "original_price": 890},
    "P-0871": {"hue": 28, "shots": 5, "name": "ครีมกันแดด SPF50+ PA++++", "price": 249, "original_price": 420},
    "P-0520": {"hue": 282, "shots": 4, "name": "ไฟ LED ติดห้อง RGB ยาว 5 เมตร", "price": 129, "original_price": 299},
    "P-0333": {"hue": 168, "shots": 5, "name": "ขวดน้ำเก็บความเย็น 1 ลิตร", "price": 189, "original_price": 350},
}


def hsl(h: float, s: float, l: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l, s)
    return round(r * 255), round(g * 255), round(b * 255)


def shot_image(product: dict, index: int, count: int, out: Path) -> Path:
    """Same gradient the web app paints with tint(hue, 46)."""
    size = 1200
    top = hsl(product["hue"] + index * 9, 0.38, 0.46)
    bottom = hsl(product["hue"] + index * 9 + 18, 0.44, 0.32)

    strip = Image.new("RGB", (1, size))
    px = strip.load()
    for y in range(size):
        k = y / (size - 1)
        px[0, y] = tuple(round(a + (b - a) * k) for a, b in zip(top, bottom))
    img = strip.resize((size, size))

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((110, 110, size - 110, size - 110), radius=46,
                           outline=(255, 255, 255, 140), width=5)
    try:
        font = ImageFont.truetype(FONT_PATH, 104)
    except OSError:
        font = ImageFont.load_default()
    draw.text((size / 2, size / 2), f"{product['name'][:2]}\n{index + 1}/{count}",
              font=font, fill=(255, 255, 255), anchor="mm", align="center", spacing=18)

    img.save(out, quality=90)
    return out


def real_photos(pid: str, count: int) -> list[Path]:
    """Shots downloaded by tools/fetch_products.py, if that has been run."""
    folder = ASSETS / "products" / pid
    if not folder.is_dir():
        return []
    return sorted(folder.glob("[0-9][0-9].jpg"))[:count]


def render_clip(key: str, entry: dict, ratio: str, size: tuple[int, int]) -> dict:
    pid = key.split("_")[0]
    product = META[pid]
    w, h = size
    cues = [Cue(c["text"], c["t"], c["e"]) for c in entry["cues"]]
    total = entry["duration"]
    voice = APP / entry["src"]

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        images = real_photos(pid, product["shots"]) or [
            shot_image(product, i, product["shots"], work / f"shot_{i}.jpg")
            for i in range(product["shots"])
        ]
        ass = subtitle.write_ass(work / "captions.ass", cues, product, total, w, h)
        visual = render.build_visual(images, total, work, w, h, CRF)

        name = f"pv_{key}.mp4" if ratio == "9:16" else f"pv_{key}_{ratio.replace(':', 'x')}.mp4"
        dest = ASSETS / name
        render.mux(visual, voice.resolve(), ass, dest, crf=CRF)

    return {
        "src": f"assets/{name}",
        "w": w, "h": h,
        "sizeKB": round(dest.stat().st_size / 1024),
    }


def main(argv: list[str]) -> int:
    ff.require()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    clips = {k: v for k, v in manifest.items() if not k.startswith("_")}

    every_ratio = "--all" in argv
    jobs: list[tuple[str, str]] = []
    for key in clips:
        jobs.append((key, "9:16"))
        if every_ratio and key.startswith("P-1042"):
            jobs += [(key, "4:5"), (key, "1:1")]

    for n, (key, ratio) in enumerate(jobs, start=1):
        print(f"[{n}/{len(jobs)}] {key} · {ratio}", flush=True)
        info = render_clip(key, clips[key], ratio, ASPECTS[ratio])
        manifest[key].setdefault("video", {})[ratio] = info
        print(f"        {info['w']}x{info['h']} · {info['sizeKB']} KB", flush=True)

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total_kb = sum(
        v["sizeKB"] for c in clips.values() for v in c.get("video", {}).values()
    )
    print(f"\nเรนเดอร์ {len(jobs)} คลิป · รวม {total_kb / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
