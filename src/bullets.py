"""Write selling points for products that Shopee only gave us a name for.

The affiliate API returns a title, a price and a sales count — nothing a script
can sell with. This asks Claude for three short selling points per product,
in batches, with a schema so the result comes back keyed by item id instead of
as prose we would have to re-parse.

Results are cached on disk by (item id, name), so a rerun after adding new
products only pays for the new ones.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-5-5"
CACHE = Path(__file__).resolve().parent.parent / "app" / "bullets-cache.json"
BATCH = 6

# Line length matters downstream: src/script_gen.py picks beats to hit a target
# duration using character counts, so bullets outside this band skew the plan.
MIN_CHARS, MAX_CHARS = 16, 36

SYSTEM = f"""
คุณคือคนเขียนแคปชันรีวิวสินค้าภาษาไทยสำหรับคลิปสั้น

เขียน "จุดขาย" ให้สินค้าแต่ละชิ้น ชิ้นละ 3 บรรทัด ตามกติกานี้

ความยาว
- แต่ละบรรทัดยาว {MIN_CHARS}-{MAX_CHARS} ตัวอักษร ห้ามเกิน
- หนึ่งบรรทัดหนึ่งใจความ ไม่ใส่เครื่องหมายวรรคตอนท้ายบรรทัด

น้ำเสียง
- ภาษาพูดแบบคนรีวิวจริง ไม่ใช่โฆษณาแข็ง ๆ
- พูดถึงประโยชน์ที่คนซื้อจะได้ ไม่ใช่คุณสมบัติลอย ๆ

ความถูกต้อง — ข้อนี้สำคัญที่สุด
- ใช้ได้เฉพาะข้อมูลที่อยู่ในชื่อสินค้าที่ให้มา
- ห้ามแต่งตัวเลขที่ไม่มีในชื่อ เช่น ความจุแบต จำนวนชั่วโมง ระยะรับประกัน
  น้ำหนัก ขนาด วัสดุ หรือมาตรฐานกันน้ำ
- ถ้าชื่อสินค้าไม่ได้บอกสเปกอะไรเลย ให้เขียนเชิงประโยชน์การใช้งานทั่วไปแทน
- ห้ามเคลมเรื่องสุขภาพ การรักษาโรค หรือผลลัพธ์ที่รับประกันไม่ได้
- ห้ามเปรียบเทียบกับยี่ห้ออื่นโดยระบุชื่อ

ตอบกลับตาม schema ที่กำหนด โดยใส่ item_id ให้ตรงกับที่รับมาทุกชิ้น
""".strip()


class ProductBullets(BaseModel):
    item_id: str = Field(description="รหัสสินค้าที่รับมา ต้องตรงกันทุกตัวอักษร")
    bullets: list[str] = Field(description="จุดขาย 3 บรรทัด")


class BulletSet(BaseModel):
    products: list[ProductBullets]


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _key(product: dict) -> str:
    # the name is part of the key so a renamed listing gets fresh copy
    return f"{product['id']}|{product['name']}"


def _ask(client, model: str, products: list[dict]) -> dict[str, list[str]]:
    listing = "\n".join(
        f"- item_id: {p['id']} | ชื่อ: {p['name']} | ราคา: {p['price']} บาท"
        f" | ขายแล้ว: {p.get('sold', 0)} ชิ้น"
        for p in products
    )

    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"effort": "low"},
        output_format=BulletSet,
        messages=[{"role": "user", "content": f"เขียนจุดขายให้สินค้าต่อไปนี้\n\n{listing}"}],
    )

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "category", None)
        raise RuntimeError(f"โมเดลปฏิเสธคำขอ ({detail}) — ตรวจชื่อสินค้าในชุดนี้")

    parsed = next(
        (b.parsed_output for b in response.content
         if b.type == "text" and getattr(b, "parsed_output", None)),
        None,
    )
    if parsed is None:
        raise RuntimeError("ไม่ได้ผลลัพธ์ตาม schema กลับมา")

    return {item.item_id: _clean(item.bullets) for item in parsed.products}


def _clean(bullets: list[str]) -> list[str]:
    out = []
    for line in bullets:
        line = " ".join(line.split()).strip(" .·-—")
        if MIN_CHARS <= len(line) <= MAX_CHARS:
            out.append(line)
    return out[:3]


def generate(products: list[dict], model: str = DEFAULT_MODEL,
             client=None, use_cache: bool = True) -> dict[str, list[str]]:
    """Return {product id: [bullets]}. Only uncached products cost anything."""
    cache = _load_cache() if use_cache else {}
    result: dict[str, list[str]] = {}
    todo: list[dict] = []

    for product in products:
        hit = cache.get(_key(product))
        if hit:
            result[product["id"]] = hit
        else:
            todo.append(product)

    if not todo:
        return result

    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise RuntimeError(
                "ต้องมี ANTHROPIC_API_KEY ถึงจะให้ Claude เขียนจุดขายได้\n"
                "ใส่ไว้ใน .env หรือใช้ --bullets fallback"
            )
        from anthropic import Anthropic
        client = Anthropic()

    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        written = _ask(client, model, chunk)
        for product in chunk:
            lines = written.get(product["id"])
            if lines:
                result[product["id"]] = lines
                cache[_key(product)] = lines

    if use_cache:
        _save_cache(cache)
    return result
