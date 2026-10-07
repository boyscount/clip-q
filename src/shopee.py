"""Shopee Affiliate Open API client.

The affiliate API is a single GraphQL endpoint. Every request carries an
Authorization header of the form

    SHA256 Credential=<app_id>, Timestamp=<unix>, Signature=<sig>
    sig = sha256(app_id + timestamp + raw_json_body + secret)

Credentials come from the environment, never from an argument that could end
up in a shell history or a log:

    SHOPEE_APP_ID, SHOPEE_APP_SECRET, SHOPEE_REGION (default th)

NOT VERIFIED AGAINST THE LIVE API — written from the documented shape of
productOfferV2. If a field name has drifted in your account's API version,
adjust PRODUCT_QUERY; the error message will name the field.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# The host is not "shopee.<region>" — each market has its own TLD. Verified by
# probing every one of these; shopee.th in particular does not resolve at all.
DOMAINS = {
    "th": "co.th", "my": "com.my", "sg": "sg", "vn": "vn",
    "ph": "ph", "id": "co.id", "tw": "tw", "br": "com.br",
}
ENDPOINT = "https://open-api.affiliate.shopee.{domain}/graphql"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

PRODUCT_QUERY = """
query productOffer($page: Int, $limit: Int, $keyword: String, $shopId: Int64) {
  productOfferV2(page: $page, limit: $limit, keyword: $keyword, shopId: $shopId) {
    nodes {
      itemId
      productName
      imageUrl
      price
      priceMin
      priceMax
      sales
      commissionRate
      offerLink
      productLink
      shopName
      ratingStar
    }
    pageInfo { page limit hasNextPage }
  }
}
""".strip()


CONVERSION_QUERY = """
query conversionReport($start: Int64, $end: Int64, $limit: Int, $scrollId: String) {
  conversionReport(purchaseTimeStart: $start, purchaseTimeEnd: $end,
                   limit: $limit, scrollId: $scrollId) {
    nodes {
      conversionId
      purchaseTime
      utmContent
      orders {
        orderId
        orderStatus
        items {
          itemId
          itemName
          qty
          actualAmount
          itemCommission
          itemSellerCommission
          refundAmount
        }
      }
    }
    pageInfo { hasNextPage scrollId limit }
  }
}
""".strip()


class ShopeeError(RuntimeError):
    pass


def to_satang(value) -> int:
    """Money arrives as a decimal string; keep it exact by going to integers."""
    if value in (None, "", "null"):
        return 0
    try:
        from decimal import Decimal
        return int((Decimal(str(value)) * 100).to_integral_value())
    except Exception:  # noqa: BLE001 — a malformed amount must not stop a sync
        return 0


def load_env() -> None:
    """Read .env into os.environ without clobbering what is already set."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Offer:
    # itemId is kept as text: Shopee's ids run past 2^53, so turning them into
    # JS numbers downstream would silently round the last digits
    item_id: str
    name: str
    image_url: str
    price: float
    price_max: float
    sales: int
    commission_rate: float
    offer_link: str
    shop_name: str
    rating: float

    @classmethod
    def from_node(cls, node: dict) -> "Offer":
        def num(key, default=0.0):
            try:
                return float(node.get(key) or default)
            except (TypeError, ValueError):
                return default

        return cls(
            item_id=str(node.get("itemId") or "").strip(),
            name=(node.get("productName") or "").strip(),
            image_url=node.get("imageUrl") or "",
            price=num("priceMin") or num("price"),
            price_max=num("priceMax") or num("price"),
            sales=int(num("sales")),
            commission_rate=num("commissionRate"),
            offer_link=node.get("offerLink") or node.get("productLink") or "",
            shop_name=node.get("shopName") or "",
            rating=num("ratingStar"),
        )


class Client:
    def __init__(self, app_id: str | None = None, secret: str | None = None,
                 region: str | None = None, timeout: int = 20):
        load_env()
        self.app_id = app_id or os.environ.get("SHOPEE_APP_ID", "")
        self.secret = secret or os.environ.get("SHOPEE_APP_SECRET", "")
        self.region = (region or os.environ.get("SHOPEE_REGION", "th")).lower()
        self.timeout = timeout
        if not self.app_id or not self.secret:
            raise ShopeeError(
                "ไม่พบ SHOPEE_APP_ID / SHOPEE_APP_SECRET\n"
                f"ใส่ไว้ใน {ENV_FILE} หรือตั้งเป็น environment variable\n"
                "ดูตัวอย่างได้ที่ .env.example"
            )

    @property
    def endpoint(self) -> str:
        domain = DOMAINS.get(self.region)
        if domain is None:
            raise ShopeeError(
                f"ไม่รู้จักภูมิภาค '{self.region}' — ใช้ได้: {', '.join(sorted(DOMAINS))}"
            )
        return ENDPOINT.format(domain=domain)

    def _headers(self, body: str) -> dict:
        ts = int(time.time())
        raw = f"{self.app_id}{ts}{body}{self.secret}"
        signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return {
            "Content-Type": "application/json",
            "Authorization": (
                f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={signature}"
            ),
        }

    def query(self, query: str, variables: dict | None = None) -> dict:
        # The signature covers the exact bytes sent, so the body is serialised
        # once and reused — re-dumping it would change the separators.
        body = json.dumps({"query": query, "variables": variables or {}},
                          separators=(",", ":"), ensure_ascii=False)
        resp = requests.post(self.endpoint, data=body.encode("utf-8"),
                             headers=self._headers(body), timeout=self.timeout)
        if resp.status_code != 200:
            raise ShopeeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        payload = resp.json()
        if payload.get("errors"):
            first = payload["errors"][0]
            raise ShopeeError(f"API error: {first.get('message', first)}")
        data = payload.get("data")
        if not data:
            raise ShopeeError(f"ไม่มี data ในคำตอบ: {str(payload)[:300]}")
        return data

    def product_offers(self, limit: int = 50, keyword: str | None = None,
                       shop_id: int | None = None, max_items: int = 200) -> list[Offer]:
        """Walk productOfferV2 until max_items or the API runs out of pages."""
        offers: list[Offer] = []
        page = 1
        while len(offers) < max_items:
            variables = {"page": page, "limit": min(limit, max_items - len(offers))}
            if keyword:
                variables["keyword"] = keyword
            if shop_id:
                variables["shopId"] = shop_id

            block = self.query(PRODUCT_QUERY, variables).get("productOfferV2") or {}
            nodes = block.get("nodes") or []
            offers.extend(Offer.from_node(n) for n in nodes)
            if not nodes or not (block.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1
        return offers

    def conversions(self, start: int, end: int, limit: int = 100,
                    max_rows: int = 5000) -> list[dict]:
        """Flatten conversionReport into one row per ordered item.

        The report nests conversion -> orders -> items; the sub-id lives on the
        conversion, so it has to be carried down to every item.
        """
        out: list[dict] = []
        scroll_id = None
        while len(out) < max_rows:
            variables = {"start": start, "end": end, "limit": limit}
            if scroll_id:
                variables["scrollId"] = scroll_id

            block = self.query(CONVERSION_QUERY, variables).get("conversionReport") or {}
            nodes = block.get("nodes") or []
            for node in nodes:
                sub_id = (node.get("utmContent") or "").strip()
                purchased = node.get("purchaseTime")
                for order in node.get("orders") or []:
                    status = (order.get("orderStatus") or "").strip().lower()
                    for item in order.get("items") or []:
                        out.append({
                            "conversion_id": f"{order.get('orderId')}:{item.get('itemId')}",
                            "sub_id": sub_id,
                            "purchase_time": purchased,
                            "status": status,
                            "item_id": str(item.get("itemId") or ""),
                            "item_name": (item.get("itemName") or "").strip(),
                            "qty": int(item.get("qty") or 1),
                            "amount_satang": to_satang(item.get("actualAmount")),
                            "commission_satang": to_satang(
                                item.get("itemCommission") or item.get("itemSellerCommission")
                            ),
                        })

            page = block.get("pageInfo") or {}
            scroll_id = page.get("scrollId")
            if not nodes or not page.get("hasNextPage") or not scroll_id:
                break
        return out
