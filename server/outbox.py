"""Drop finished clips into a folder a phone-sync tool watches.

Syncthing, Google Drive, OneDrive and friends all work the same way: point
them at one folder and whatever lands there shows up on the phone. The caption
is written beside the video as a .txt so it is readable from the phone's file
app even when the web page is not open.

Set CLIPQUEUE_OUTBOX to that folder. Unset means the feature is off.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .config import load_env

# captured at import time — read the file here so import order cannot matter
load_env()

OUTBOX = Path(os.environ["CLIPQUEUE_OUTBOX"]) if os.environ.get("CLIPQUEUE_OUTBOX") else None
KEEP = int(os.environ.get("CLIPQUEUE_OUTBOX_KEEP", "60"))

# Filenames land on Android/iOS file pickers, so keep them to characters every
# filesystem and sync client agrees on. Underscore is excluded on purpose: it
# is the field separator here, and job ids contain one ("j_9965…"), so letting
# it through a slug would make the id impossible to find again.
UNSAFE = re.compile(r"[^0-9A-Za-z฀-๿.-]+")


def slug(text: str, limit: int = 28) -> str:
    cleaned = UNSAFE.sub("-", text.strip()).strip("-")
    return (cleaned[:limit] or "clip").rstrip("-")


def enabled() -> bool:
    return OUTBOX is not None


def filename(job, product_name: str, account: str) -> str:
    day = (job["scheduled_at"] or "")[:10].replace("-", "")
    time = (job["scheduled_at"] or "")[11:16].replace(":", "")
    return f"{day}-{time}_{slug(account, 16)}_{slug(product_name)}_{job['id']}"


def publish(job, video: Path, caption: str, product_name: str, account: str) -> Path | None:
    """Copy the clip and its caption into the outbox. Returns the video path."""
    if OUTBOX is None:
        return None

    OUTBOX.mkdir(parents=True, exist_ok=True)
    base = filename(job, product_name, account)
    target = OUTBOX / f"{base}.mp4"

    # copy, never move: the API still serves the original out of renders/
    shutil.copy2(video, target)
    (OUTBOX / f"{base}.txt").write_text(caption, encoding="utf-8")
    prune()
    return target


def prune() -> int:
    """Keep the outbox from growing without bound — the phone has to sync it."""
    if OUTBOX is None or KEEP <= 0:
        return 0
    clips = sorted(OUTBOX.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    removed = 0
    for old in clips[:-KEEP]:
        old.unlink(missing_ok=True)
        old.with_suffix(".txt").unlink(missing_ok=True)
        removed += 1
    return removed


def forget(job_id: str) -> int:
    """Remove a clip from the outbox once it has been posted."""
    if OUTBOX is None:
        return 0
    gone = 0
    for path in OUTBOX.glob(f"*_{job_id}.*"):
        path.unlink(missing_ok=True)
        gone += 1
    return gone
