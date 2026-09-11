"""Settings đọc từ biến môi trường (Pydantic Settings).

Chủ sở hữu: Q | Task: 1.2 | xem Task.md

Tên trường khớp 1-1 với biến môi trường trong `.env.example` (pydantic-settings không
phân biệt hoa thường): `database_url` ← `DATABASE_URL`, `embedding_dim` ← `EMBEDDING_DIM`…
Thêm biến mới thì sửa cả `.env.example` (cùng chủ sở hữu Q, task 1.4/9.1).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Cấu hình toàn ứng dụng. Giá trị mặc định là giá trị dùng trong Docker Compose."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Ứng dụng ---
    app_name: str = "BusinessCard_OCR"
    debug: bool = False

    # --- Database ---
    database_url: str = "postgresql+psycopg://bizcard:change-me@db:5432/bizcard"

    # --- CLIProxyAPI (OAuth tới Gemini) — dùng từ D2, xem Plan.md mục 2.4 ---
    cliproxy_base_url: str = "http://cliproxy:8317"
    cliproxy_mgmt_key: str = ""
    cliproxy_auth_provider: str = "antigravity"
    #: Phải là model CÓ THẬT trong channel `antigravity` — kiểm bằng
    #: `GET /v0/management/model-definitions/antigravity`. `gemini-flash-latest` thuộc channel
    #: `aistudio`/`gemini` (cần API key), gọi qua `antigravity` sẽ không chạy. Xem I-03.
    llm_model: str = "gemini-3-flash"

    # --- Embedding: KHÔNG đi qua CLIProxy, xem Plan.md mục 2.6 ---
    embedder_url: str = "http://embedder:8001"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dim: int = 384

    # --- Lưu trữ ảnh danh thiếp ---
    upload_dir: Path = Path("/data/uploads")
    max_upload_mb: int = 10

    @property
    def sync_database_url(self) -> str:
        """URL cho công cụ chạy đồng bộ (Alembic, script). psycopg3 dùng chung một scheme."""
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """Settings dùng chung, đọc env một lần. Test ghi đè bằng `get_settings.cache_clear()`."""
    return Settings()


settings = get_settings()
