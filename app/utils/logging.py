"""Minimal structured-ish logging. Keeps the demo readable in the console."""
from __future__ import annotations

from app.utils.time import now_utc


def log(message: str) -> None:
    print(f"{now_utc().strftime('%H:%M:%S')}  {message}")
