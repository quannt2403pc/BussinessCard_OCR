import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class EmailTakenError(Exception):
    pass


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def create(
    db: AsyncSession, *, email: str, password_hash: str, display_name: str | None
) -> User:
    user = User(email=email, password_hash=password_hash, display_name=display_name)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise EmailTakenError(email) from exc
    await db.commit()
    await db.refresh(user)
    return user


async def record_login(db: AsyncSession, user: User, *, password_hash: str | None = None) -> None:
    values: dict[str, object] = {"last_login_at": func.now()}
    if password_hash is not None:
        values["password_hash"] = password_hash
    await db.execute(update(User).where(User.id == user.id).values(**values))
    await db.commit()
    await db.refresh(user)


async def set_password(db: AsyncSession, user: User, password_hash: str) -> None:
    await db.execute(update(User).where(User.id == user.id).values(password_hash=password_hash))
    await db.commit()
    await db.refresh(user)


async def set_display_name(db: AsyncSession, user: User, display_name: str | None) -> None:
    await db.execute(update(User).where(User.id == user.id).values(display_name=display_name))
    await db.commit()
    await db.refresh(user)
