"""Product dict -> narration script of a given length.

Beats are added in order of how much they earn their seconds: the hook and the
call to action always ship, everything else only if it moves the estimated
duration closer to the target. That is why a 16-second clip drops to two
selling points while a 42-second one picks up the story opener and the filler
beats at the end.

Modes:
  template  offline, no API key, randomised hooks so clips are not identical
  llm       Claude writes the script (set ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import json
import os
import random
import textwrap

from . import speech

HOOKS = [
    "บอกเลยว่าของมันต้องมี {name} ลดเหลือ {price} บาท",
    "ใครกำลังหา {name} อยู่ หยุดก่อน ตัวนี้เหลือ {price} บาทเท่านั้น",
    "เจอแล้วตัวคุ้มสุดในราคา {price} บาท {name}",
    "ราคานี้ไม่น่าเชื่อ {name} เหลือแค่ {price} บาท",
]

STORY_OPENERS = [
    "เมื่อก่อนเจอปัญหานี้ทุกวันจนเลิกใช้ของเดิมไปเลย",
    "ซื้อมาสามตัวก่อนหน้านี้ พังหมด จนมาเจอตัวนี้",
]

DISCOUNT = "จากปกติ {was} บาท ลดมาเกินครึ่ง"

CLOSINGS = [
    "ของดีราคานี้หมดเร็วแน่นอน",
    "เหลือไม่เยอะแล้วนะ",
    "ลองแล้วจะติดใจ",
]

CTA = "กดตะกร้าส้มใต้คลิปเลย"
CTA_FREE = "กดตะกร้าส้มใต้คลิปเลย ส่งฟรีมีเก็บปลายทาง"

# Seller-voice filler, used only when the target length needs more to say.
EXTRAS = [
    "ตอนแรกลังเลเพราะราคาถูกกว่าเจ้าอื่นเยอะ แต่ลองแล้วไม่ผิดหวัง",
    "ใช้มาสองอาทิตย์แล้ว ยังดีเหมือนวันแรก",
    "รีวิวในร้านก็บอกตรงกันว่าคุ้มเกินราคา",
    "ร้านมีโค้ดลดเพิ่ม กดรับที่หน้าสินค้าได้เลย",
    "ของส่งจากในไทย ไม่ต้องรอนาน",
    "ซื้อไปฝากที่บ้านอีกชิ้น เขาก็ชอบเหมือนกัน",
    "ราคาเท่านี้ซื้อติดไว้ก่อนได้เลย ไม่ต้องคิดเยอะ",
    "ตอนนั้นหาข้อมูลอยู่หลายวัน เทียบมาหลายร้านมากกว่าจะตัดสินใจ",
    "พอได้ลองของจริงถึงรู้ว่าต่างจากของเดิมชัดเจน",
    "เพื่อนเห็นแล้วถามว่าซื้อจากไหน เลยต้องมารีวิวให้ดูกัน",
    "ถ้าย้อนกลับไปได้ก็คงซื้อตัวนี้ตั้งแต่แรก ไม่ต้องเสียเงินหลายรอบ",
]

# Target seconds per clip format, matched to FORMATS in the web app.
TARGETS = {"quick": 16, "show": 24, "story": 42}


def _beats(product: dict, fmt: str, rnd: random.Random) -> tuple[list[str], list[tuple[int, str]]]:
    """Return (fixed beats, optional beats as (insert_position, text))."""
    price = f"{product['price']:,}"
    hook = rnd.choice(HOOKS).format(name=product["name"], price=price)
    cta = CTA_FREE if product.get("free") else CTA
    fixed = [hook, cta]

    # position is the index in the final script, counting from the hook
    optional: list[tuple[int, str]] = []
    bullets = product.get("bullets", [])
    if bullets:
        optional.append((2, bullets[0]))
    if product.get("was"):
        optional.append((1, DISCOUNT.format(was=f"{product['was']:,}")))
    if len(bullets) > 1:
        optional.append((3, bullets[1]))
    optional.append((90, rnd.choice(CLOSINGS)))
    if len(bullets) > 2:
        optional.append((4, bullets[2]))
    if fmt == "story":
        optional.append((0, rnd.choice(STORY_OPENERS)))
    for i, extra in enumerate(EXTRAS):
        optional.append((50 + i, extra))
    return fixed, optional


def plan_lines(product: dict, fmt: str = "quick", target: float | None = None,
               seed: int | None = None) -> list[str]:
    """Pick the set of beats whose estimated duration lands closest to target."""
    target = TARGETS.get(fmt, 24) if target is None else target
    m = speech.model()
    rnd = random.Random(seed)
    (hook, cta), optional = _beats(product, fmt, rnd)

    chosen: list[tuple[int, str]] = []

    def assemble(extra=None) -> list[str]:
        picked = sorted(chosen + ([extra] if extra else []), key=lambda b: b[0])
        # position 0 is the story opener, which sets up the hook and so runs
        # before it; everything else sits between the hook and the CTA
        before = [text for pos, text in picked if pos < 1]
        after = [text for pos, text in picked if pos >= 1]
        return [*before, hook, *after, cta]

    best_gap = abs(speech.estimate(assemble(), m) - target)
    for beat in optional:
        if speech.estimate(assemble(), m) >= target:
            break  # already at length; every beat only adds more
        gap = abs(speech.estimate(assemble(beat), m) - target)
        if gap < best_gap:
            # a beat that overshoots is skipped, not fatal — a shorter one
            # further down the list may still fit
            chosen.append(beat)
            best_gap = gap

    return assemble()


def build_llm_script(product: dict, fmt: str = "quick") -> str:
    from anthropic import Anthropic

    target = TARGETS.get(fmt, 24)
    m = speech.model()
    budget = int((target - m["per_line"] * 7) / m["per_char"])

    prompt = textwrap.dedent(f"""
        เขียนสคริปต์พูดสำหรับคลิปรีวิวสินค้าแนวตั้ง

        สินค้า: {product['name']}
        ราคา: {product['price']} บาท (ปกติ {product.get('was', '-')} บาท)
        จุดขาย:
        {chr(10).join('- ' + b for b in product.get('bullets', []))}

        กติกา
        - ความยาวเมื่อพูดจริงต้องได้ประมาณ {target} วินาที
        - รวมทุกบรรทัดแล้วต้องอยู่ที่ประมาณ {budget} ตัวอักษร ห้ามเกิน {int(budget * 1.1)}
        - ภาษาพูดแบบคนรีวิวจริง ไม่ใช่โฆษณาแข็ง ๆ
        - ประโยคสั้น หนึ่งบรรทัดหนึ่งประโยค
        - บรรทัดแรกต้องเป็น hook ที่หยุดนิ้วคนดูได้ใน 2 วินาที
        - ปิดท้ายด้วย: {CTA_FREE if product.get('free') else CTA}
        - ห้ามเคลมสรรพคุณเกินจริงหรือเรื่องสุขภาพ
        - ตอบกลับมาเฉพาะตัวสคริปต์ ไม่ต้องมีหัวข้อหรือคำอธิบาย
    """).strip()

    resp = Anthropic().messages.create(
        model="claude-sonnet-5-5",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def build_script(product: dict, mode: str = "template", seed: int | None = None,
                 fmt: str = "quick") -> str:
    if mode == "llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("mode=llm ต้องตั้ง ANTHROPIC_API_KEY ก่อน")
        return build_llm_script(product, fmt)
    return "\n".join(plan_lines(product, fmt, seed=seed))


if __name__ == "__main__":
    import sys

    product = json.loads(open(sys.argv[1], encoding="utf-8").read())
    fmt = sys.argv[2] if len(sys.argv) > 2 else "quick"
    lines = plan_lines(product, fmt, seed=0)
    print("\n".join(lines))
    print(f"\n{len(lines)} บรรทัด · ประเมิน {speech.estimate(lines):.1f} วิ (เป้า {TARGETS[fmt]} วิ)")
