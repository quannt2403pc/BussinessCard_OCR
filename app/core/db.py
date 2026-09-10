"""Engine + session SQLAlchemy 2.0.

Chủ sở hữu: Q | Task: 1.2 | xem Task.md

Dùng engine **async**: các endpoint gọi LLM qua httpx async (Plan.md mục 2.4), session đồng bộ
sẽ chặn event loop. Driver `psycopg` (v3) chạy được cả async lẫn sync trên cùng một URL, nên
Alembic (`alembic/env.py`, task 1.6) dùng lại đúng chuỗi kết nối này ở chế độ đồng bộ.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Base khai báo cho toàn bộ model. Model tách theo file trong `app/models/`."""


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependency FastAPI: mở session cho mỗi request, rollback khi có lỗi."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
