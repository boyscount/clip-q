"""Read .env into the environment.

Kept separate from src.shopee so every entry point can call it first, before
any module captures its settings at import time. Existing environment
variables win, so a container's real config is never overwritten by a .env
that happened to get baked into the image.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path | None = None) -> int:
    """Returns how many variables were taken from the file."""
    env_path = path or ENV_FILE
    if not env_path.exists():
        return 0

    taken = 0
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            taken += 1
    return taken
