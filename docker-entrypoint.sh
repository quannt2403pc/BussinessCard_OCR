#!/bin/sh
# Entrypoint container `api`: chay migration roi moi giao quyen cho CMD.
# Chu so huu: Q | Task: 9.2 | xem Task.md
#
# Vi sao can: truoc task nay, may sach phai go them mot lenh tay
#   docker compose exec api alembic upgrade head
# Quen buoc do thi app len duoc nhung moi truy van deu chet vi khong co bang nao -- dung kieu
# hong khien tieu chi A1 ("mot lenh `docker compose up -d` la chay") truot ma khong ai thay ngay.
#
# `set -e`: migration hong thi container PHAI chet, khong duoc chay tiep. App len voi schema cu
# la loai hong im lang te nhat -- moi thu "gan nhu" chay, den khi cham vao cot moi thi 500.
#
# `exec "$@"`: thay the tien trinh shell bang uvicorn, giu PID 1 cho uvicorn de `docker stop`
# gui SIGTERM den dung tien trinh (khong `exec` thi shell nuot tin hieu, container luon phai
# cho het 10 giay timeout moi tat).
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
else
    echo "[entrypoint] RUN_MIGRATIONS=0 -> bo qua migration"
fi

exec "$@"
