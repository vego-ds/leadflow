"""
Seed counselors from data/counselors.json into the database.

Run once after initializing a fresh database (or whenever the roster changes).
Idempotent: existing counselors are updated, new ones are inserted.

Usage:
    python -m scripts.seed_counselors
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.db.engine import async_session
from app.db.models import CounselorRow

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COUNSELORS_FILE = DATA_DIR / "counselors.json"


async def _seed() -> None:
    with open(COUNSELORS_FILE, encoding="utf-8") as f:
        counselors: list[dict] = json.load(f)

    async with async_session() as session:
        for c in counselors:
            existing = await session.get(CounselorRow, c["name"])
            if existing is None:
                row = CounselorRow(
                    name=c["name"],
                    languages_spoken=c.get("languages_spoken", []),
                    available_slots=c.get("available_slots", []),
                    current_load=c.get("current_load", 0),
                )
                session.add(row)
            else:
                existing.languages_spoken = c.get("languages_spoken", [])
                existing.available_slots = c.get("available_slots", [])
                existing.current_load = c.get("current_load", 0)
        await session.commit()

    print(f"Seeded {len(counselors)} counselors into the database.")


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
