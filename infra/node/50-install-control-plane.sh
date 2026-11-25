#!/usr/bin/env bash
# Cài control plane (API + worker) thành dịch vụ systemd trên laptop-1.
# Dùng (trong thư mục repo):   sudo ./infra/node/50-install-control-plane.sh [tên-miền]
# Chạy lại bất cứ lúc nào để cập nhật code; dữ liệu và cấu hình đã có được giữ nguyên.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DOMAIN="${1:-vexenhanh.id.vn}"
APP=/opt/minicloud/app
ETC=/etc/minicloud
DATA=/var/lib/minicloud

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 rsync curl openssl

echo "==> User dịch vụ minicloud"
id -u minicloud >/dev/null 2>&1 ||
  useradd --system --home-dir "${DATA}" --shell /usr/sbin/nologin minicloud
install -d -m 0750 -o minicloud -g minicloud "${DATA}"
install -d -m 0755 -o minicloud -g minicloud /etc/traefik/dynamic # file tuyến cho Traefik

echo "==> uv (quản lý môi trường Python của dự án)"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

echo "==> Code: ${REPO_ROOT} -> ${APP}"
install -d -m 0755 "${APP}"
rsync -a --delete \
  --exclude .git --exclude .venv --exclude secrets --exclude dev-routes --exclude .env --exclude backups \
  --exclude '*.db' --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  --exclude node_modules \
  "${REPO_ROOT}/" "${APP}/"
(cd "${APP}" && uv sync --frozen --no-dev --python /usr/bin/python3 --quiet)

echo "==> Chứng chỉ để control plane gọi Incus"
install -d -m 0700 -o minicloud -g minicloud "${ETC}/secrets"
if [[ ! -f "${ETC}/secrets/client.key" ]]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -sha384 -nodes \
    -keyout "${ETC}/secrets/client.key" -out "${ETC}/secrets/client.crt" \
    -days 3650 -subj "/CN=minicloud-$(hostname)" 2>/dev/null
fi
fp="$(openssl x509 -in "${ETC}/secrets/client.crt" -noout -fingerprint -sha256 |
  cut -d= -f2 | tr -d : | tr 'A-F' 'a-f')"
if ! incus config trust show "${fp}" >/dev/null 2>&1; then
  incus config trust add-certificate "${ETC}/secrets/client.crt" --name "minicloud-$(hostname)"
fi
if [[ -f /var/lib/incus/cluster.crt ]]; then
  install -m 0644 /var/lib/incus/cluster.crt "${ETC}/secrets/incus.crt" # cụm nhiều node
else
  install -m 0644 /var/lib/incus/server.crt "${ETC}/secrets/incus.crt" # một node (WSL)
fi
chown -R minicloud:minicloud "${ETC}/secrets"
chmod 600 "${ETC}/secrets/client.key"

addr="$(incus config get core.https_address)"
case "${addr}" in
  "[::]"* | "0.0.0.0"* | ":"*) incus_url="https://127.0.0.1:${addr##*:}" ;;
  *) incus_url="https://${addr}" ;;
esac

echo "==> Cấu hình ${ETC}/minicloud.env"
if [[ ! -f "${ETC}/minicloud.env" ]]; then
  cat >"${ETC}/minicloud.env" <<EOF
# Cấu hình control plane, systemd đọc file này. Sửa xong: sudo systemctl restart minicloud-api
MINICLOUD_DATABASE_URL=sqlite:///${DATA}/minicloud.db
MINICLOUD_DRIVER=incus
MINICLOUD_INCUS_URL=${incus_url}
MINICLOUD_INCUS_CLIENT_CERT=${ETC}/secrets/client.crt
MINICLOUD_INCUS_CLIENT_KEY=${ETC}/secrets/client.key
MINICLOUD_INCUS_CA_CERT=${ETC}/secrets/incus.crt
MINICLOUD_PUBLIC_DOMAIN=${DOMAIN}
MINICLOUD_INGRESS_ROUTES_FILE=/etc/traefik/dynamic/minicloud.yml
MINICLOUD_SIGNUP_INVITE_CODE=$(openssl rand -hex 4)
MINICLOUD_CORS_ORIGINS=https://cloud.${DOMAIN}
MINICLOUD_GREEN_PINNED_NODES=$(hostname)
EOF
  chown root:minicloud "${ETC}/minicloud.env"
  chmod 640 "${ETC}/minicloud.env"
fi

cat >/usr/local/bin/minicloud-admin <<EOF
#!/bin/sh
# Lệnh quản trị với đúng cấu hình của dịch vụ. Ví dụ: sudo minicloud-admin make-admin an@example.com
set -a
. ${ETC}/minicloud.env
set +a
cd ${DATA}
exec runuser -u minicloud -- ${APP}/.venv/bin/minicloud "\$@"
EOF
chmod 755 /usr/local/bin/minicloud-admin

echo "==> Database: dừng API, sao lưu, nâng schema (Alembic)"
install -d -m 0700 -o minicloud -g minicloud "${DATA}/backups"
systemctl stop minicloud-api.service 2>/dev/null || true # không ghi gì trong lúc migrate
if [[ -f "${DATA}/minicloud.db" ]]; then
  /usr/local/bin/minicloud-admin backup --dir "${DATA}/backups" --keep 14 # bản ngay trước khi đổi schema
fi
/usr/local/bin/minicloud-admin migrate # lỗi thì dừng ở đây, API cũ không bị bật lại với schema lạ

echo "==> Dịch vụ minicloud-api"
cat >/etc/systemd/system/minicloud-api.service <<EOF
[Unit]
Description=minicloud control plane (API + worker)
After=network-online.target incus.service
Wants=network-online.target incus.service

[Service]
User=minicloud
Group=minicloud
EnvironmentFile=${ETC}/minicloud.env
WorkingDirectory=${DATA}
ExecStart=${APP}/.venv/bin/uvicorn minicloud.main:create_app --factory --host 127.0.0.1 --port 8000 --proxy-headers
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
echo "==> Sao lưu database mỗi ngày lúc 03:00 giờ Việt Nam (giữ 14 bản)"
cat >/etc/systemd/system/minicloud-backup.service <<EOF
[Unit]
Description=Sao lưu database minicloud

[Service]
Type=oneshot
User=minicloud
Group=minicloud
UMask=0077
EnvironmentFile=${ETC}/minicloud.env
WorkingDirectory=${DATA}
ExecStart=${APP}/.venv/bin/minicloud backup --dir ${DATA}/backups --keep 14
NoNewPrivileges=yes
EOF
cat >/etc/systemd/system/minicloud-backup.timer <<EOF
[Unit]
Description=Sao lưu database minicloud hằng ngày

[Timer]
OnCalendar=*-*-* 03:00:00 Asia/Ho_Chi_Minh
RandomizedDelaySec=15min
# Máy tắt hoặc ngủ lúc 3 giờ thì sao lưu ngay khi bật lại.
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl enable incus.service >/dev/null 2>&1 || true # incusd chạy ngay khi máy khởi động
systemctl daemon-reload
systemctl enable minicloud-api.service
systemctl restart minicloud-api.service
systemctl enable --now minicloud-backup.timer

health="$(curl -s --retry 20 --retry-connrefused --retry-delay 1 http://127.0.0.1:8000/healthz || true)"
echo "healthz: ${health}"
echo "Bản sao lưu: ${DATA}/backups (lần tới: $(systemctl show minicloud-backup.timer -p NextElapseUSecRealtime --value))"
echo "Mã mời đăng ký: $(grep '^MINICLOUD_SIGNUP_INVITE_CODE=' "${ETC}/minicloud.env" | cut -d= -f2)"
echo "Sau khi đăng ký, cấp quyền admin: sudo minicloud-admin make-admin <email>"
