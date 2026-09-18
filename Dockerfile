# Image backend FastAPI.
# Chủ sở hữu: Q | Task: 1.4 (hoàn thiện ở 9.1/9.2)
#
# Migration chạy **tự động** lúc container khởi động qua `docker-entrypoint.sh` (task 9.2).
# Tắt khi cần (ví dụ chạy một script một lần): `RUN_MIGRATIONS=0`.

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

# `chmod` ngay trong image: repo clone trên Windows không giữ bit thực thi, mà file này lại là
# thứ container chạy đầu tiên — thiếu bit là `exec format error` ngay lúc `up`.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# ENTRYPOINT + CMD tách đôi: compose ghi đè `command:` (uvicorn --reload cho môi trường dev)
# thì phần ghi đè đó trở thành tham số của entrypoint → migration vẫn chạy trước.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
