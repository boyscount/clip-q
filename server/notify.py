"""Tell you on your phone when a clip is ready to post.

Pick one provider with CLIPQUEUE_NOTIFY:

  ntfy      ง่ายสุด ไม่ต้องสมัคร — ติดตั้งแอป ntfy แล้ว subscribe หัวข้อที่ตั้งเอง
  telegram  ต้องสร้างบอทกับ @BotFather แล้วเอา token กับ chat id มาใส่
  discord   วาง webhook URL ของห้องที่ต้องการ
  none      ปิด (ค่าเริ่มต้น)

LINE Notify ไม่อยู่ในรายการเพราะปิดบริการไปแล้ว ถ้าต้องการ LINE ต้องใช้
Messaging API ซึ่งต้องสร้าง official account และบอทเอง
"""

from __future__ import annotations

import os
import urllib.parse

from .config import load_env

# These settings are captured at import time, so the file has to be read here
# rather than relying on whichever entry point got there first.
load_env()

PROVIDER = os.environ.get("CLIPQUEUE_NOTIFY", "none").strip().lower()
BASE_URL = os.environ.get("CLIPQUEUE_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
TIMEOUT = 10


class NotifyError(RuntimeError):
    pass


def enabled() -> bool:
    return PROVIDER not in ("", "none")


def describe() -> str:
    if not enabled():
        return "ปิดอยู่"
    try:
        _config()
        return f"{PROVIDER} · พร้อมใช้"
    except NotifyError as exc:
        return f"{PROVIDER} · {exc}"


def _config() -> dict:
    if PROVIDER == "ntfy":
        topic = os.environ.get("NTFY_TOPIC", "").strip()
        if not topic:
            raise NotifyError("ยังไม่ได้ตั้ง NTFY_TOPIC")
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        return {"url": f"{server}/{urllib.parse.quote(topic)}"}

    if PROVIDER == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat:
            raise NotifyError("ต้องมีทั้ง TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID")
        return {"url": f"https://api.telegram.org/bot{token}/sendMessage", "chat": chat}

    if PROVIDER == "discord":
        url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        if not url:
            raise NotifyError("ยังไม่ได้ตั้ง DISCORD_WEBHOOK_URL")
        return {"url": url}

    raise NotifyError(f"ไม่รู้จัก provider '{PROVIDER}' — ใช้ ntfy, telegram, discord หรือ none")


def send(title: str, body: str, link: str = "") -> bool:
    """Best effort. A failed notification must never fail a finished render."""
    if not enabled():
        return False

    import requests

    try:
        cfg = _config()
    except NotifyError:
        return False

    full = f"{body}\n{link}".strip()
    try:
        if PROVIDER == "ntfy":
            headers = {"Title": title.encode("utf-8"), "Tags": "clapper"}
            if link:
                headers["Click"] = link
            resp = requests.post(cfg["url"], data=full.encode("utf-8"),
                                 headers=headers, timeout=TIMEOUT)
        elif PROVIDER == "telegram":
            resp = requests.post(cfg["url"], timeout=TIMEOUT, json={
                "chat_id": cfg["chat"],
                "text": f"*{title}*\n{full}",
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
        else:  # discord
            resp = requests.post(cfg["url"], timeout=TIMEOUT,
                                 json={"content": f"**{title}**\n{full}"})
        return resp.status_code < 300
    except Exception:  # noqa: BLE001 — network trouble is not our caller's problem
        return False


def clip_ready(product_name: str, account: str, caption_first_line: str, waiting: int) -> bool:
    return send(
        title=f"คลิปพร้อมอัป · {account}",
        body=f"{product_name}\n{caption_first_line}"
             + (f"\n\nรออัปทั้งหมด {waiting} คลิป" if waiting > 1 else ""),
        link=f"{BASE_URL}/post",
    )
