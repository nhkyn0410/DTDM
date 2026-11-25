#!/usr/bin/env bash
# Cài cổng vào trên laptop-1: Traefik (định tuyến app của khách) và cloudflared (Cloudflare Tunnel).
# Dùng (trong thư mục repo):   sudo ./infra/node/60-install-ingress.sh
#   TRAEFIK_VERSION=v3.8.0   đổi bản Traefik nếu cần
# Tiếp theo chạy 61-setup-tunnel.sh để đăng nhập Cloudflare và tạo tunnel.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TRAEFIK_VERSION="${TRAEFIK_VERSION:-v3.8.0}"
ARCH="$(dpkg --print-architecture)"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates jq

echo "==> cloudflared (kho apt chính thức của Cloudflare)"
if ! command -v cloudflared >/dev/null; then
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    >/etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  apt-get install -y -qq cloudflared
fi
cloudflared --version

echo "==> Traefik ${TRAEFIK_VERSION} (bản phát hành chính thức trên GitHub, có kiểm tra SHA-256)"
installed="$(traefik version 2>/dev/null | awk '/^Version:/ {print "v"$2}' || true)"
if [[ "${installed}" != "${TRAEFIK_VERSION}" ]]; then
  tmp="$(mktemp -d)"
  base="https://github.com/traefik/traefik/releases/download/${TRAEFIK_VERSION}"
  file="traefik_${TRAEFIK_VERSION}_linux_${ARCH}.tar.gz"
  curl -fsSL -o "${tmp}/${file}" "${base}/${file}"
  curl -fsSL -o "${tmp}/checksums.txt" "${base}/traefik_${TRAEFIK_VERSION}_checksums.txt"
  (cd "${tmp}" && grep " ${file}\$" checksums.txt | sha256sum -c -)
  tar -xzf "${tmp}/${file}" -C "${tmp}" traefik
  install -m 0755 "${tmp}/traefik" /usr/local/bin/traefik
  rm -rf "${tmp}"
fi
traefik version | head -1

echo "==> Cấu hình Traefik"
id -u minicloud >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/minicloud --shell /usr/sbin/nologin minicloud
install -d -m 0755 /etc/traefik
# Control plane (user minicloud) ghi file tuyến vào đây, Traefik đọc và tự nạp lại.
install -d -m 0755 -o minicloud -g minicloud /etc/traefik/dynamic
install -m 0644 "${REPO_ROOT}/infra/edge/traefik/traefik.yml" /etc/traefik/traefik.yml

cat >/etc/systemd/system/traefik.service <<'EOF'
[Unit]
Description=Traefik: định tuyến app của khách minicloud
After=network-online.target
Wants=network-online.target

[Service]
DynamicUser=yes
ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml
Restart=on-failure
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable traefik.service
systemctl restart traefik.service

code="$(curl -s -o /dev/null -w '%{http_code}' --retry 15 --retry-connrefused --retry-delay 1 \
  -H 'Host: khong-co.example' http://127.0.0.1:8080/ || true)"
echo "Traefik trả HTTP ${code} cho một tên không có tuyến (mong đợi 404)."
echo "Tiếp theo: sudo ./infra/node/61-setup-tunnel.sh <tên-tunnel> <tên-miền>"
