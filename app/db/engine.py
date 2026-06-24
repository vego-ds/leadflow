"""
Async SQLAlchemy engine + session factory.

The DB is LeadFlow's system of record for lead state, idempotency, and
outreach durability. Sheets remain a human-facing projection - see
app/adapters/db_store.py.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)


async def dispose_engine() -> None:
    await engine.dispose()
