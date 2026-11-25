#!/usr/bin/env bash
# Tạo Cloudflare Tunnel và trỏ các tên máy về nó (ADR-0005).
# Dùng (trong thư mục repo):   sudo ./infra/node/61-setup-tunnel.sh [tên-tunnel] [tên-miền]
# Lần đầu lệnh in ra một link: mở link, đăng nhập Cloudflare, chọn tên miền rồi bấm Authorize.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TUNNEL="${1:-minicloud}"
DOMAIN="${2:-vexenhanh.id.vn}"

if [[ ! -f /root/.cloudflared/cert.pem ]]; then
  cloudflared tunnel login
fi

if ! cloudflared tunnel info "${TUNNEL}" >/dev/null 2>&1; then
  cloudflared tunnel create "${TUNNEL}"
fi
UUID="$(cloudflared tunnel list --output json | jq -r --arg n "${TUNNEL}" '.[] | select(.name == $n) | .id')"
if [[ -z "${UUID}" ]]; then
  echo "Không tìm thấy tunnel ${TUNNEL}." >&2
  exit 1
fi

echo "==> Cấu hình /etc/cloudflared cho tunnel ${TUNNEL} (${UUID})"
install -d -m 0755 /etc/cloudflared
install -m 0600 "/root/.cloudflared/${UUID}.json" "/etc/cloudflared/${UUID}.json"
sed -e "s/<TUNNEL-UUID>/${UUID}/g" -e "s/vexenhanh\.id\.vn/${DOMAIN}/g" \
  "${REPO_ROOT}/infra/edge/cloudflared/config.yml.example" >/etc/cloudflared/config.yml
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate

echo "==> Trỏ tên máy về tunnel"
# --overwrite-dns: tên đang trỏ về tunnel khác (ví dụ bản thử trên WSL) sẽ chuyển sang tunnel này.
cloudflared tunnel route dns --overwrite-dns "${TUNNEL}" "cloud-api.${DOMAIN}"
cloudflared tunnel route dns --overwrite-dns "${TUNNEL}" "cloud.${DOMAIN}"
cloudflared tunnel route dns --overwrite-dns "${TUNNEL}" "*.${DOMAIN}" ||
  echo "Không tạo được wildcard: tạo tay CNAME '*' -> ${UUID}.cfargotunnel.com (bật proxy)."

echo "==> Dịch vụ cloudflared"
if [[ ! -f /etc/systemd/system/cloudflared.service ]]; then
  cloudflared service install # cài và chạy luôn; khởi động lại ngay sẽ làm tunnel rớt vài giây
else
  systemctl restart cloudflared
fi
for _ in $(seq 1 30); do # chờ tunnel kết nối tới Cloudflare, thường dưới 10 giây
  cloudflared tunnel info "${TUNNEL}" 2>/dev/null | grep -q CONNECTOR && break
  sleep 1
done
cloudflared tunnel info "${TUNNEL}" | tail -3
echo "Thử từ máy khác: https://cloud-api.${DOMAIN}/healthz"
