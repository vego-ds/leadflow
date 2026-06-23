"""Minimal structured-ish logging. Keeps the demo readable in the console."""
from __future__ import annotations

from datetime import datetime


def log(message: str) -> None:
    print(f"{datetime.utcnow().strftime('%H:%M:%S')}  {message}")
