"""Caption cues -> burned-in ASS subtitles.

ASS rather than drawtext because libass resolves a Thai font by name and
shapes the glyphs properly; drawtext would need an explicit .ttf path and
still breaks on combining vowels.
"""

from __future__ import annotations

from pathlib import Path

from .voice import Cue

FONT = "Leelawadee UI"  # ships with Windows, covers Thai

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{cap},&H00FFFFFF,&H00101010,&H90000000,-1,1,6,3,2,{side},{side},{cap_v},1
Style: Badge,{font},{badge},&H0000E5FF,&H00101010,&H90000000,-1,1,5,2,8,{side},{side},{badge_v},1
Style: Title,{font},{title},&H00FFFFFF,&H00101010,&H90000000,-1,1,5,2,8,{side},{side},{title_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _metrics(w: int, h: int) -> dict:
    """Type and margins scale with the frame, not with 1080x1920 alone.

    Font size follows the width so captions stay readable on a square crop;
    the margins follow the height so they keep clear of the safe area.
    """
    return {
        "w": w, "h": h,
        "cap": round(w * 0.063),
        "badge": round(w * 0.054),
        "title": round(w * 0.041),
        "side": round(w * 0.074),
        "cap_v": round(h * 0.224),
        "badge_v": round(h * 0.078),
        "title_v": round(h * 0.130),
    }


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _event(style: str, start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{text}"


def build_ass(cues: list[Cue], product: dict, total: float,
              w: int = 1080, h: int = 1920) -> str:
    lines = [HEADER.format(font=FONT, **_metrics(w, h))]

    # the catalogue calls it `was`; older callers passed `original_price`
    was = product.get("was") or product.get("original_price")
    price = f"{product['price']:,} บาท"
    if was:
        price = f"เหลือ {price}  (ปกติ {was:,})"
    lines.append(_event("Badge", 0.0, total, f"{{\\fad(300,300)}}{_clean(price)}"))
    lines.append(
        _event("Title", 0.0, total, f"{{\\fad(300,300)}}{_clean(product['name'])}")
    )

    for cue in cues:
        lines.append(
            _event("Caption", cue.start, cue.end, f"{{\\fad(90,90)}}{_clean(cue.text)}")
        )

    return "\n".join(lines) + "\n"


def write_ass(path: Path, cues: list[Cue], product: dict, total: float,
              w: int = 1080, h: int = 1920) -> Path:
    # utf-8-sig: libass needs the BOM to pick UTF-8 on some Windows builds
    path.write_text(build_ass(cues, product, total, w, h), encoding="utf-8-sig")
    return path
