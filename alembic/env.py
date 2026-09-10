"""Cấu hình môi trường Alembic.

Chủ sở hữu: Q | Task: 1.6 | xem Task.md

Chuỗi kết nối lấy từ `app.core.config.settings` (biến `DATABASE_URL`), không viết vào
`alembic.ini` để file cấu hình không bao giờ chứa mật khẩu.
Driver `psycopg` (v3) chạy được cả sync lẫn async trên cùng URL nên ở đây dùng engine đồng bộ.
"""

from logging.config import fileConfig
from typing import Any

from sqlalchemy import Connection, engine_from_config, pool

import app.models  # noqa: F401  — nạp toàn bộ model vào Base.metadata cho autogenerate
from alembic import context
from app.core.config import settings
from app.core.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Bảng đã có trong DB nhưng CHƯA có model ORM, vì `app/models/company.py` thuộc quyền sở hữu
#: của T (bảng sở hữu, Task.md). Không bỏ qua thì autogenerate sẽ sinh `drop_table` cho chúng.
#: T khai model xong → Q xoá tập này đi.
TABLES_WITHOUT_MODEL = {"companies", "company_profiles"}


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Bỏ qua các bảng chưa có model ORM và mọi ràng buộc trỏ vào chúng."""
    if type_ == "table" and name in TABLES_WITHOUT_MODEL:
        return False
    if type_ == "foreign_key_constraint":
        referred = getattr(obj, "referred_table", None)
        if referred is not None and referred.name in TABLES_WITHOUT_MODEL:
            return False
    return True


def run_migrations_offline() -> None:
    """Sinh câu lệnh SQL ra stdout, không cần kết nối DB (`alembic upgrade head --sql`)."""
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Chạy migration trên kết nối thật."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
