#!/usr/bin/env bash
# Deploy mot ban len may chu. Chay TREN VM, trong thu muc /opt/bizcard.
# Chu so huu: Q | Task: 13.8 | xem Task.md, docs/deploy.md
#
#   ./scripts/deploy.sh                 # deploy origin/main
#   ./scripts/deploy.sh <commit|nhanh>  # deploy dung mot ban
#   ./scripts/deploy.sh --rollback      # quay ve ban truoc do
#
# GitHub Actions (.github/workflows/cd.yml) goi dung script nay qua SSH. Giu logic o day chu
# khong nhet vao YAML vi mot ly do thuc te: khi Actions hong (het phut runner, khoa SSH sai,
# GitHub sap) thi van phai deploy duoc bang tay, va luc do khong ai muon ngoi doc lai YAML de
# doan xem no da chay nhung lenh gi.
#
# `set -euo pipefail`: mot buoc hong la dung han. Deploy chay tiep sau khi migration hong la
# kieu hong te nhat - app len voi schema cu, moi thu "gan nhu" chay.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.prod.yml)
BACKUP_DIR="${ROOT}/backups"
STATE_FILE="${ROOT}/.deploy-previous"
KEEP_BACKUPS=7
SMOKE_TRIES=30
SMOKE_INTERVAL=5

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[canh bao] %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m[loi] %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------- kiem tra tien de

check_prerequisites() {
    log "Kiem tra tien de"

    command -v docker >/dev/null || die "Chua cai Docker."
    docker compose version >/dev/null 2>&1 || die "Chua cai plugin 'docker compose'."

    # ⚠️ RANG BUOC THAT, KHONG PHAI HINH THUC. docker-compose.prod.yml dung hai the YAML
    # `!reset` va `!override` de GO cac cong ma docker-compose.yml da publish. Compose cu hon
    # v2.24 **bo qua the do ma khong bao loi** -> deploy van xanh, va PostgreSQL cung
    # CLIProxy phoi thang ra Internet. Day la rui ro R9 o dang nguy hiem nhat: im lang.
    local raw major minor
    raw="$(docker compose version --short 2>/dev/null | tr -d 'v')"
    major="${raw%%.*}"
    minor="$(printf '%s' "$raw" | cut -d. -f2)"
    if [ "${major:-0}" -lt 2 ] || { [ "${major:-0}" -eq 2 ] && [ "${minor:-0}" -lt 24 ]; }; then
        die "Docker Compose $raw qua cu. Can >= v2.24 cho the !reset/!override, ban cu se BO QUA chung va de lo cong 5432/8317 ra Internet."
    fi
    echo "  docker compose $raw - dat"

    [ -f "${ROOT}/.env" ] || die "Thieu ${ROOT}/.env. Chep tu .env.prod.example roi dien gia tri (xem docs/deploy.md)."

    # Bat cac gia tri con nguyen van mau. Mot ban deploy voi POSTGRES_PASSWORD=change-me la
    # ban khong ai muon phat hien vao ngay nghiem thu.
    #
    # ⚠️ CHI soi dong KHAI BIEN, phai bo qua chu thich. `.env.prod.example` nhac ca ba chuoi
    # nay ngay trong phan giai thich (dong noi ve `secret-key: "change-me"` cua CLIProxy,
    # dong huong dan sinh bi mat), ma `.env` that thi chep tu file do nen thua huong luon.
    # Grep ca file = bao dong gia 100%: vap that o lan deploy dau tien 2026-09-22, chan dung
    # mot `.env` hoan toan hop le.
    if grep -vE '^[[:space:]]*#' "${ROOT}/.env" \
            | grep -qE '(change-me|SINH-NGAU-NHIEN|doi-thanh-email-that)'; then
        die ".env con gia tri mau (change-me / SINH-NGAU-NHIEN / doi-thanh-email-that). Dien gia tri that truoc da."
    fi
    echo "  .env - co, khong con gia tri mau"

    # shellcheck disable=SC1091
    set -a; . "${ROOT}/.env"; set +a
    : "${SITE_DOMAIN:?thieu SITE_DOMAIN trong .env}"
    : "${POSTGRES_USER:?thieu POSTGRES_USER trong .env}"
    : "${POSTGRES_DB:?thieu POSTGRES_DB trong .env}"
    echo "  ten mien: ${SITE_DOMAIN}"
}

# --------------------------------------------------------------------- sao luu du lieu

backup_database() {
    log "Sao luu database truoc khi migrate"

    # Vi sao pg_dump chu khong phai snapshot dia (Plan.md muc 10): snapshot tu dong cua Google
    # tinh tien va giu ca o dia, con o day chi can du lieu. Tra lai cung don gian hon nhieu.
    # `ps -q db` rong = chua co container nao, tuc la lan deploy dau tien. Dung `-q` chu khong
    # phai `--status running --services`: hai co do khong co o moi ban Compose, ma script nay
    # phai chay duoc tren bat ky may nao dat nguong v2.24 o tren.
    if [ -z "$(docker compose "${COMPOSE_FILES[@]}" ps -q db 2>/dev/null)" ]; then
        warn "Chua co container 'db' - bo qua sao luu (lan deploy dau tien thi dung la vay)."
        return 0
    fi

    mkdir -p "$BACKUP_DIR"
    local file="${BACKUP_DIR}/pgdata-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"

    # `-T`: khong cap TTY. Thieu co nay thi lenh chay qua SSH khong tuong tac se treo.
    if docker compose "${COMPOSE_FILES[@]}" exec -T db \
            pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > "$file"; then
        echo "  $file ($(du -h "$file" | cut -f1))"
    else
        rm -f "$file"
        die "pg_dump that bai. KHONG deploy tiep - migration co the sua schema ma khong co duong lui."
    fi

    # Giu lai KEEP_BACKUPS ban gan nhat. Dia 50GB khong phai vo han, va mot ban dump day du
    # cua bo du lieu demo chi vai MB nen 7 ban la thua du de lui lai.
    ls -1t "${BACKUP_DIR}"/pgdata-*.sql.gz 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))" | while read -r old; do
        rm -f "$old" && echo "  don ban cu: $(basename "$old")"
    done
}

# --------------------------------------------------------------------- dua ma nguon ve

checkout() {
    local target="$1"
    log "Lay ma nguon: ${target}"

    git rev-parse HEAD > "$STATE_FILE"
    echo "  ban dang chay: $(cut -c1-12 < "$STATE_FILE")"

    git fetch --prune origin
    git checkout --force --detach "$target"
    echo "  chuyen sang:   $(git rev-parse --short=12 HEAD)  $(git log -1 --format=%s)"
}

# --------------------------------------------------------------------- dung he thong

bring_up() {
    log "Build va khoi dong"

    # --build: image `api` build ngay tren may. KHONG day len GHCR - repo private, goi Free
    # chi co 500MB luu tru trong khi rieng image embedder da 2.79GB (Plan.md muc 10).
    # Lan dau mat vai phut vi phai keo torch; cac lan sau Docker dung lai layer cache va chi
    # build lai `api`.
    #
    # Migration chay trong entrypoint cua `api` (task 9.2), khong goi tay o day.
    docker compose "${COMPOSE_FILES[@]}" up -d --build --remove-orphans
    docker image prune -f >/dev/null 2>&1 || true
}

# --------------------------------------------------------------------- smoke test

smoke_test() {
    log "Smoke test https://${SITE_DOMAIN}/health"

    local i body
    for i in $(seq 1 "$SMOKE_TRIES"); do
        # Khong dung `curl -f`: can doc body de phan biet "app tra loi that" voi "Caddy tra
        # trang loi cua no". Ca hai deu co the la HTTP 200 trong luc he thong dang len.
        body="$(curl -sS --max-time 10 "https://${SITE_DOMAIN}/health" 2>/dev/null || true)"
        if printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
            echo "  dat sau ${i} lan thu: ${body}"
            return 0
        fi
        printf '  lan %d/%d chua dat%s\n' "$i" "$SMOKE_TRIES" "${body:+ (${body:0:80})}"
        sleep "$SMOKE_INTERVAL"
    done

    warn "Smoke test truot sau $((SMOKE_TRIES * SMOKE_INTERVAL))s."
    docker compose "${COMPOSE_FILES[@]}" ps
    docker compose "${COMPOSE_FILES[@]}" logs --tail=60 api caddy || true
    return 1
}

# --------------------------------------------------------------------- quay ve ban truoc

rollback() {
    local previous
    [ -f "$STATE_FILE" ] || die "Khong co ${STATE_FILE} - khong biet quay ve dau. Chon commit bang tay: ./scripts/deploy.sh <commit>"
    previous="$(cat "$STATE_FILE")"
    log "Quay ve ${previous:0:12}"

    git checkout --force --detach "$previous"
    docker compose "${COMPOSE_FILES[@]}" up -d --build --remove-orphans

    # ⚠️ Quay ve MA NGUON, KHONG quay ve DATABASE. Migration da chay thi van con do.
    # Alembic co `downgrade` nhung ban demo nay chua bao gio chay thu no, nen tu dong hoa
    # mot buoc chua ai kiem la cach nhanh nhat de bien mot ban deploy hong thanh mat du lieu.
    # Can lui schema thi lam tay, tu ban dump trong ./backups.
    warn "Da lui ma nguon. Schema KHONG tu lui - neu ban vua roi co migration, xem ./backups va docs/deploy.md muc 'Khoi phuc'."
}

# --------------------------------------------------------------------- chay

main() {
    local target="${1:-origin/main}"

    if [ "$target" = "--rollback" ]; then
        check_prerequisites
        rollback
        smoke_test || die "Quay ve roi ma smoke test van truot - vao xem log bang tay."
        log "Da quay ve ban truoc."
        return 0
    fi

    check_prerequisites
    backup_database
    checkout "$target"
    bring_up

    if ! smoke_test; then
        warn "Deploy hong - dang tu quay ve ban truoc."
        rollback
        smoke_test || warn "Ca ban cu cung khong len duoc. Can nguoi vao xem."
        die "Deploy that bai, da quay ve $(cut -c1-12 < "$STATE_FILE")."
    fi

    log "Xong: https://${SITE_DOMAIN} dang chay $(git rev-parse --short=12 HEAD)"
}

main "$@"
