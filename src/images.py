"""Download product photos and turn them into shots the renderer can use."""

from __future__ import annotations

from pathlib import Path

import requests
from PIL import Image, ImageFilter

SHOT_SIZE = 1200
UA = "ClipQueue/0.6 (+poc)"

# Shopee serves several sizes off the same path; the bare id is the largest.
SUFFIXES = ["", "_tn"]


def download(url: str, dest: Path, timeout: int = 20) -> Path | None:
    """Fetch one image. Returns None instead of raising so one bad URL in a
    catalogue of two hundred does not abort the whole import."""
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://cf.shopee.co.th/file/" + url.lstrip("/")

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        Image.open(dest).verify()  # reject HTML error pages saved as .jpg
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        return None


def to_shot(source: Path, dest: Path, size: int = SHOT_SIZE) -> Path:
    """Square the photo onto a blurred bed of itself, the way render.py fills
    the frame — so a 4:3 product photo never ends up with bare black bars."""
    img = Image.open(source).convert("RGB")

    bed = img.copy()
    scale = size / min(bed.size)
    bed = bed.resize((max(1, round(bed.width * scale)), max(1, round(bed.height * scale))),
                     Image.LANCZOS)
    left = (bed.width - size) // 2
    top = (bed.height - size) // 2
    bed = bed.crop((left, top, left + size, top + size)).filter(ImageFilter.GaussianBlur(26))

    fit = img.copy()
    fit.thumbnail((size, size), Image.LANCZOS)
    bed.paste(fit, ((size - fit.width) // 2, (size - fit.height) // 2))

    dest.parent.mkdir(parents=True, exist_ok=True)
    bed.save(dest, quality=90)
    return dest


def variants(source: Path, out_dir: Path, count: int) -> list[Path]:
    """Make `count` shots from however many photos we actually got.

    With one photo the clip would otherwise be a still, so the extra shots are
    progressively tighter centre crops — the same trick a human editor uses to
    get three angles out of one picture.
    """
    img = Image.open(source).convert("RGB")
    out: list[Path] = []
    for i in range(count):
        keep = 1.0 - 0.11 * i
        w, h = img.size
        box = ((w - w * keep) / 2, (h - h * keep) / 2,
               (w + w * keep) / 2, (h + h * keep) / 2)
        crop = img.crop(tuple(round(v) for v in box))
        tmp = out_dir / f"_crop_{i}.jpg"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        crop.save(tmp, quality=92)
        out.append(to_shot(tmp, out_dir / f"{i + 1:02d}.jpg"))
        tmp.unlink(missing_ok=True)
    return out


def prepare(photos: list[Path], out_dir: Path, count: int) -> list[Path]:
    """Normalise whatever photos we have into exactly `count` square shots."""
    photos = [p for p in photos if p and p.exists()]
    if not photos:
        return []
    if len(photos) == 1:
        return variants(photos[0], out_dir, count)

    shots = []
    for i in range(count):
        shots.append(to_shot(photos[i % len(photos)], out_dir / f"{i + 1:02d}.jpg"))
    return shots
