"""Request validation.

Everything a client sends is bounded here, before it reaches the database or
the renderer. The limits are deliberate: a queue request that fans out
products x clips x accounts can ask for thousands of renders by accident, and
each one costs real CPU minutes.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator, model_validator

FORMATS = {"quick": 16, "show": 24, "story": 42}
ASPECTS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
VOICES = {"female", "male"}

MAX_PRODUCTS = 50
MAX_ACCOUNTS = 10
MAX_SLOTS = 12
MAX_PER = 10
MAX_JOBS_PER_BATCH = 200  # products x per x accounts, after dedupe
MAX_SCHEDULE_DAYS = 60

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class QueueRequest(BaseModel):
    model_config = {"extra": "forbid"}

    product_ids: list[str] = Field(min_length=1, max_length=MAX_PRODUCTS)
    account_ids: list[str] = Field(min_length=1, max_length=MAX_ACCOUNTS)
    slots: list[str] = Field(min_length=1, max_length=MAX_SLOTS)
    format: str
    aspect: str
    persona: str = Field(min_length=1, max_length=60)
    voice: str = "female"
    per: int = Field(default=1, ge=1, le=MAX_PER)
    cart: bool = True
    start_date: str
    idempotency_key: str | None = Field(default=None, max_length=80)

    @field_validator("product_ids", "account_ids")
    @classmethod
    def _unique_ids(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("ต้องมีอย่างน้อยหนึ่งรายการ")
        seen, out = set(), []
        for item in cleaned:  # dedupe but keep the order the user picked
            if item not in seen:
                seen.add(item)
                out.append(item)
        if any(len(i) > 64 for i in out):
            raise ValueError("รหัสยาวเกินไป")
        return out

    @field_validator("slots")
    @classmethod
    def _valid_times(cls, value: list[str]) -> list[str]:
        out = sorted({v.strip() for v in value})
        for item in out:
            if not TIME_RE.match(item):
                raise ValueError(f"รอบเวลา '{item}' ต้องอยู่ในรูปแบบ HH:MM แบบ 24 ชั่วโมง")
        return out

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        if value not in FORMATS:
            raise ValueError(f"รูปแบบคลิปต้องเป็นอย่างใดอย่างหนึ่งใน {sorted(FORMATS)}")
        return value

    @field_validator("aspect")
    @classmethod
    def _known_aspect(cls, value: str) -> str:
        if value not in ASPECTS:
            raise ValueError(f"สัดส่วนต้องเป็นอย่างใดอย่างหนึ่งใน {sorted(ASPECTS)}")
        return value

    @field_validator("voice")
    @classmethod
    def _known_voice(cls, value: str) -> str:
        if value not in VOICES:
            raise ValueError(f"เสียงพากย์ต้องเป็น {sorted(VOICES)}")
        return value

    @field_validator("start_date")
    @classmethod
    def _sane_date(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("วันที่ต้องอยู่ในรูปแบบ YYYY-MM-DD") from exc
        today = date.today()
        if parsed < today:
            raise ValueError("เริ่มอัปย้อนหลังไม่ได้")
        if parsed > today + timedelta(days=MAX_SCHEDULE_DAYS):
            raise ValueError(f"ตั้งล่วงหน้าได้ไม่เกิน {MAX_SCHEDULE_DAYS} วัน")
        return value

    @model_validator(mode="after")
    def _batch_size(self) -> "QueueRequest":
        total = len(self.product_ids) * self.per * len(self.account_ids)
        if total > MAX_JOBS_PER_BATCH:
            raise ValueError(
                f"ขอ {total} คลิปในครั้งเดียว เกินเพดาน {MAX_JOBS_PER_BATCH} "
                f"({len(self.product_ids)} สินค้า × {self.per} คลิป × {len(self.account_ids)} บัญชี)"
            )
        return self


class CapRequest(BaseModel):
    model_config = {"extra": "forbid"}
    cap: int = Field(ge=0, le=99)
