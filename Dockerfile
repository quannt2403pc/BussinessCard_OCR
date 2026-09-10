# Image backend FastAPI.
# Chủ sở hữu: Q | Task: 1.4 (hoàn thiện ở 9.1/9.2)
#
# Chạy `alembic upgrade head` tự động lúc khởi động là task 9.2 — hiện tại migration chạy tay:
#   docker compose exec api alembic upgrade head

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Cài phụ thuộc trước, copy mã nguồn sau → sửa code không phải cài lại thư viện.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts

# Ảnh danh thiếp nằm ở volume `uploads` mount vào đây (khớp UPLOAD_DIR trong .env.example).
RUN mkdir -p /data/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
