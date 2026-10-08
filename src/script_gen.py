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

SOLD = "ขายไปแล้ว {sold:,} ชิ้น คนซื้อซ้ำเยอะ"
SHOP = "ส่งจากร้าน {shop} ของแท้แน่นอน"

# ข้อมูลสินค้าที่เลือกได้ว่าจะให้พูดถึงหรือไม่ — ชื่อ ราคา และ CTA พูดเสมอ
# เพราะคลิปที่ไม่บอกว่าขายอะไรราคาเท่าไรก็ไม่ใช่คลิปขายของ
FACTS = {
    "discount": "ราคาเดิมและส่วนลด",
    "bullets": "จุดขายของสินค้า",
    "sold": "ยอดขายที่ผ่านมา",
    "free": "ส่งฟรี เก็บปลายทาง",
    "shop": "ชื่อร้านที่ส่ง",
}
ALL_FACTS = frozenset(FACTS)

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


def _beats(product: dict, fmt: str, rnd: random.Random, style=None,
           facts=None, talk=None) -> tuple[list[str], list[tuple[int, str]]]:
    """Return (fixed beats, optional beats as (insert_position, text)).

    facts บอกว่าข้อมูลชิ้นไหนพูดถึงได้บ้าง None = พูดได้หมดเหมือนเดิม
    """
    use = ALL_FACTS if facts is None else (set(facts) & ALL_FACTS)
    price = f"{product['price']:,}"
    # สไตล์การพูดที่เลือกไว้ชนะน้ำเสียงที่ติดมากับแนวคลิป ไม่ได้เลือกก็ถอยไป
    # ใช้ของแนว แล้วค่อยถอยไปกองกลางถ้าแนวนั้นไม่มีของตัวเอง
    voice_of = talk or style
    hooks = list(getattr(voice_of, "hooks", ()) or ()) or HOOKS
    closings = list(getattr(voice_of, "closings", ()) or ()) or CLOSINGS
    hook = rnd.choice(hooks).format(name=product["name"], price=price)
    cta = CTA_FREE if (product.get("free") and "free" in use) else CTA
    fixed = [hook, cta]

    # position is the index in the final script, counting from the hook
    optional: list[tuple[int, str]] = []
    bullets = product.get("bullets", []) if "bullets" in use else []
    if bullets:
        optional.append((2, bullets[0]))
    if product.get("was") and "discount" in use:
        optional.append((1, DISCOUNT.format(was=f"{product['was']:,}")))
    if len(bullets) > 1:
        optional.append((3, bullets[1]))
    if product.get("sold") and "sold" in use:
        optional.append((5, SOLD.format(sold=product["sold"])))
    if product.get("shop") and "shop" in use:
        optional.append((6, SHOP.format(shop=product["shop"])))
    optional.append((90, rnd.choice(closings)))
    if len(bullets) > 2:
        optional.append((4, bullets[2]))
    if fmt == "story":
        optional.append((0, rnd.choice(STORY_OPENERS)))
    for i, extra in enumerate(EXTRAS):
        optional.append((50 + i, extra))
    return fixed, optional


def plan_lines(product: dict, fmt: str = "quick", target: float | None = None,
               seed: int | None = None, style=None, facts=None,
               talk=None) -> list[str]:
    """Pick the set of beats whose estimated duration lands closest to target."""
    target = TARGETS.get(fmt, 24) if target is None else target
    m = speech.model()
    rnd = random.Random(seed)
    (hook, cta), optional = _beats(product, fmt, rnd, style, facts, talk)

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


def _llm_facts(product: dict, use: set) -> str:
    """ข้อมูลที่อนุญาตให้ Claude พูดถึง — ชิ้นที่ไม่ได้เลือกจะไม่ถูกส่งไปเลย

    ตัดออกตั้งแต่ตอนสร้าง prompt ไม่ใช่สั่งว่า "ห้ามพูดถึง" เพราะข้อมูลที่
    ไม่ได้ส่งไป โมเดลแต่งขึ้นเองไม่ได้
    """
    out = [f"ราคา: {product['price']} บาท"]
    if product.get("was") and "discount" in use:
        out.append(f"ราคาปกติ: {product['was']} บาท")
    if product.get("bullets") and "bullets" in use:
        out.append("จุดขาย:\n" + "\n".join("- " + b for b in product["bullets"]))
    if product.get("sold") and "sold" in use:
        out.append(f"ขายไปแล้ว: {product['sold']:,} ชิ้น")
    if product.get("shop") and "shop" in use:
        out.append(f"ร้าน: {product['shop']}")
    if product.get("free") and "free" in use:
        out.append("ส่งฟรี มีเก็บปลายทาง")
    return "\n".join(out)


def build_llm_script(product: dict, fmt: str = "quick", style=None, facts=None,
                     talk=None) -> str:
    from anthropic import Anthropic

    target = TARGETS.get(fmt, 24)
    m = speech.model()
    budget = int((target - m["per_line"] * 7) / m["per_char"])
    tone = (getattr(talk, "tone", "") or getattr(style, "tone", "")
            or "ภาษาพูดแบบคนรีวิวจริง")
    use = ALL_FACTS if facts is None else (set(facts) & ALL_FACTS)

    prompt = textwrap.dedent(f"""
        เขียนสคริปต์พูดสำหรับคลิปรีวิวสินค้าแนวตั้ง

        สินค้า: {product['name']}
        {_llm_facts(product, use)}

        น้ำเสียงที่ต้องการ: {tone}

        กติกา
        - ความยาวเมื่อพูดจริงต้องได้ประมาณ {target} วินาที
        - รวมทุกบรรทัดแล้วต้องอยู่ที่ประมาณ {budget} ตัวอักษร ห้ามเกิน {int(budget * 1.1)}
        - ไม่ใช่โฆษณาแข็ง ๆ
        - ประโยคสั้น หนึ่งบรรทัดหนึ่งประโยค
        - บรรทัดแรกต้องเป็น hook ที่หยุดนิ้วคนดูได้ใน 2 วินาที
        - ปิดท้ายด้วย: {CTA_FREE if (product.get('free') and 'free' in use) else CTA}
        - พูดได้เฉพาะข้อมูลที่ให้ไว้ข้างบน ห้ามเติมตัวเลขหรือคุณสมบัติที่ไม่ได้ให้
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
                 fmt: str = "quick", style=None, facts=None, talk=None) -> str:
    if mode == "llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("mode=llm ต้องตั้ง ANTHROPIC_API_KEY ก่อน")
        return build_llm_script(product, fmt, style, facts, talk)
    return "\n".join(plan_lines(product, fmt, seed=seed, style=style, facts=facts))


if __name__ == "__main__":
    import sys

    product = json.loads(open(sys.argv[1], encoding="utf-8").read())
    fmt = sys.argv[2] if len(sys.argv) > 2 else "quick"
    lines = plan_lines(product, fmt, seed=0)
    print("\n".join(lines))
    print(f"\n{len(lines)} บรรทัด · ประเมิน {speech.estimate(lines):.1f} วิ (เป้า {TARGETS[fmt]} วิ)")
