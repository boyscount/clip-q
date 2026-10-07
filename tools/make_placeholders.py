"""Generate stand-in product photos so the pipeline can be run without assets.

    python tools/make_placeholders.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "images"
FONT_PATH = r"C:\Windows\Fonts\LeelaUIb.ttf"

SHOTS = [
    ("ภาพสินค้า", (36, 42, 58), (92, 120, 168)),
    ("มุมที่ 2", (58, 38, 44), (170, 96, 104)),
    ("ในกล่อง", (34, 54, 46), (92, 162, 128)),
    ("ใส่ใช้งาน", (52, 46, 32), (176, 150, 84)),
]


def gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        k = y / (h - 1)
        px[0, y] = tuple(int(a + (b - a) * k) for a, b in zip(top, bottom))
    return img.resize((w, h))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype(FONT_PATH, 96)
    except OSError:
        font = ImageFont.load_default()

    for i, (label, top, bottom) in enumerate(SHOTS, start=1):
        img = gradient((1200, 1200), top, bottom)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((120, 120, 1080, 1080), radius=48, outline=(255, 255, 255), width=6)
        draw.text((600, 600), f"{label}\n{i}/{len(SHOTS)}", font=font,
                  fill=(255, 255, 255), anchor="mm", align="center", spacing=20)
        path = OUT / f"{i:02d}.jpg"
        img.save(path, quality=92)
        print(path)


if __name__ == "__main__":
    main()
