"""Pull the product catalogue from Shopee Affiliate and download the photos.

    python tools/fetch_products.py --limit 12            # live API
    python tools/fetch_products.py --mock                # no credentials needed
    python tools/fetch_products.py --limit 12 --sync-app # also patch the web app

Writes app/products.json and app/assets/products/<itemId>/01.jpg…, which
make_preview_video.py picks up automatically in place of the gradients.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import images  # noqa: E402

APP = ROOT / "app"
PHOTOS = APP / "assets" / "products"
OUT = APP / "products.json"
SHOTS_PER_PRODUCT = 5

# Shopee gives no selling points, so the script planner needs them from
# somewhere. Until the LLM pass runs, fall back to what the listing does say.
FALLBACK_BULLETS = [
    "ยอดขาย {sales:,} ชิ้น คนซื้อซ้ำเยอะ",
    "คะแนนร้าน {rating:.1f} จาก 5",
    "ส่งจาก {shop}",
]


def hue_for(item_id: str) -> int:
    """Stable colour per product so the app card and the clip agree."""
    return sum(ord(c) * (i + 7) for i, c in enumerate(str(item_id))) % 360


def to_app_product(offer, bullets: list[str]) -> dict:
    was = round(offer.price_max) if offer.price_max > offer.price else round(offer.price * 1.8)
    return {
        "id": str(offer.item_id),
        "name": offer.name,
        "price": round(offer.price),
        "was": was,
        "com": round(offer.commission_rate * 100) if offer.commission_rate <= 1 else round(offer.commission_rate),
        "sold": offer.sales,
        "hue": hue_for(offer.item_id),
        "free": False,
        "shots": SHOTS_PER_PRODUCT,
        "bullets": bullets,
        "link": offer.offer_link,
        "shop": offer.shop_name,
    }


def bullets_for(offer) -> list[str]:
    """Drop a line when the listing has nothing to put in it — judged on the
    value, not on the rendered text (a sales count of 1,240 ends in '0 ชิ้น')."""
    available = {
        "sales": offer.sales > 0,
        "rating": offer.rating > 0,
        "shop": bool(offer.shop_name.strip()),
    }
    out = []
    for template in FALLBACK_BULLETS:
        field = next((k for k in available if "{" + k in template), None)
        if field and not available[field]:
            continue
        out.append(template.format(sales=offer.sales, rating=offer.rating, shop=offer.shop_name))
    return out[:3]


def mock_offers(limit: int):
    """Offline stand-in with the same shape the API returns, so the whole
    import path can be exercised without credentials."""
    from src.shopee import Offer

    # ids match the products already in the web app, so a mock import lands on
    # the same folders the renderer and the page look in
    seeds = [
        ("P-1042", "หูฟังบลูทูธ TWS รุ่น Pro Max", 399, 890, 1240, 0.12),
        ("P-0871", "ครีมกันแดด SPF50+ PA++++", 249, 420, 3180, 0.20),
        ("P-0520", "ไฟ LED ติดห้อง RGB ยาว 5 เมตร", 129, 299, 2470, 0.18),
        ("P-0333", "ขวดน้ำเก็บความเย็น 1 ลิตร", 189, 350, 1930, 0.15),
        ("P-1190", "ที่ชาร์จเร็ว 65W GaN 3 ช่อง", 359, 690, 860, 0.13),
        ("P-0744", "หม้อหุงข้าวมินิ 1.2 ลิตร", 690, 1190, 540, 0.14),
    ]
    out = []
    for i, (pid, name, price, was, sales, com) in enumerate(seeds[:limit]):
        out.append(Offer(
            item_id=pid, name=name, image_url="", price=price, price_max=was,
            sales=sales, commission_rate=com, offer_link=f"https://s.shopee.co.th/mock{i}",
            shop_name="ร้านดีดีช้อป", rating=4.8,
        ))
    return out


def write_bullets(products: list[dict], mode: str) -> int:
    """Fill in selling points. Returns how many came from Claude."""
    import os

    if mode == "fallback":
        return 0
    if mode == "auto" and not (os.environ.get("ANTHROPIC_API_KEY")
                               or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("ไม่มี ANTHROPIC_API_KEY — ใช้จุดขายจากยอดขาย/เรตติ้งแทน")
        return 0

    from src import bullets as writer

    print(f"\nให้ Claude เขียนจุดขาย {len(products)} สินค้า ({writer.DEFAULT_MODEL})…", flush=True)
    try:
        written = writer.generate(products)
    except Exception as exc:  # noqa: BLE001 — never lose a finished import over copy
        print(f"เขียนจุดขายไม่สำเร็จ: {exc}\nใช้จุดขายสำรองแทน", file=sys.stderr)
        return 0

    count = 0
    for product in products:
        lines = written.get(product["id"])
        if lines:
            product["bullets"] = lines
            product["bulletsBy"] = "claude"
            count += 1
    return count


def mock_photo(offer, dest: Path) -> Path:
    """A landscape stand-in photo, so --mock exercises the real squaring and
    crop-variant code instead of skipping straight to the gradients."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1600, 1200
    hue = hue_for(offer.item_id)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        k = y / (h - 1)
        import colorsys
        r, g, b = colorsys.hls_to_rgb(((hue + 14 * k) % 360) / 360, 0.46 - 0.14 * k, 0.40)
        for x in range(w):
            px[x, y] = (round(r * 255), round(g * 255), round(b * 255))

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((160, 140, w - 160, h - 140), radius=44,
                           outline=(255, 255, 255), width=5)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\LeelaUIb.ttf", 86)
    except OSError:
        font = ImageFont.load_default()
    draw.text((w / 2, h / 2), offer.name, font=font, fill=(255, 255, 255),
              anchor="mm", align="center")

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, quality=92)
    return dest


def sync_app(products: list[dict]) -> None:
    """Replace the PRODUCTS array inlined in app/clipqueue.html."""
    page = APP / "clipqueue.html"
    html = page.read_text(encoding="utf-8")
    marker = "  const PRODUCTS = ["
    start = html.index(marker)
    end = html.index("\n  ];", start) + len("\n  ];")
    html = html[:start] + "  const PRODUCTS = " + json.dumps(products, ensure_ascii=False) + ";" + html[end:]
    page.write_text(html, encoding="utf-8")
    print(f"อัปเดต {page.name} แล้ว · {len(products)} สินค้า")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ดึงสินค้าและรูปจาก Shopee Affiliate")
    ap.add_argument("--limit", type=int, default=12, help="จำนวนสินค้าที่ดึง")
    ap.add_argument("--keyword", help="คำค้นสินค้า")
    ap.add_argument("--shop-id", type=int, help="ดึงเฉพาะร้านนี้")
    ap.add_argument("--mock", action="store_true", help="ไม่เรียก API ใช้ข้อมูลตัวอย่าง")
    ap.add_argument("--sync-app", action="store_true", help="เขียนทับ PRODUCTS ใน clipqueue.html")
    ap.add_argument("--fresh", action="store_true", help="ลบรูปเดิมก่อนดึงใหม่")
    ap.add_argument("--bullets", choices=["auto", "llm", "fallback"], default="auto",
                    help="จุดขาย: llm ให้ Claude เขียน, fallback ใช้ยอดขาย/เรตติ้ง, auto เลือกเองตามว่ามีคีย์ไหม")
    args = ap.parse_args(argv)

    if args.mock:
        offers = mock_offers(args.limit)
        print(f"โหมด mock · {len(offers)} สินค้า (ไม่เรียก API)")
    else:
        from src.shopee import Client, ShopeeError
        try:
            client = Client()
            print(f"เรียก {client.endpoint}")
            offers = client.product_offers(
                limit=min(args.limit, 50), keyword=args.keyword,
                shop_id=args.shop_id, max_items=args.limit,
            )
        except ShopeeError as exc:
            print(f"\n{exc}\n", file=sys.stderr)
            print("ยังไม่มีคีย์? ลองดูท่อทั้งหมดก่อนด้วย --mock", file=sys.stderr)
            return 1
        print(f"ได้ {len(offers)} สินค้า")

    if args.fresh and PHOTOS.exists():
        shutil.rmtree(PHOTOS)

    products = []
    for n, offer in enumerate(offers, start=1):
        if not offer.name:
            continue
        folder = PHOTOS / str(offer.item_id)
        if args.mock:
            photo = mock_photo(offer, folder / "source.jpg")
        else:
            photo = images.download(offer.image_url, folder / "source.jpg")
        shots = images.prepare([photo] if photo else [], folder, SHOTS_PER_PRODUCT)
        product = to_app_product(offer, bullets_for(offer))
        product["shots"] = len(shots) or SHOTS_PER_PRODUCT
        product["hasPhotos"] = bool(shots)
        products.append(product)
        state = f"{len(shots)} shots" if shots else "ไม่มีรูป ใช้ไล่สีแทน"
        print(f"[{n}/{len(offers)}] {offer.name[:40]:40s} · {state}", flush=True)

    written = write_bullets(products, args.bullets)

    OUT.write_text(json.dumps(products, ensure_ascii=False, indent=1), encoding="utf-8")
    withphotos = sum(1 for p in products if p["hasPhotos"])
    print(f"\nเขียน {OUT} · {len(products)} สินค้า · มีรูปจริง {withphotos} · จุดขายจาก Claude {written}")

    if args.sync_app:
        sync_app(products)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
