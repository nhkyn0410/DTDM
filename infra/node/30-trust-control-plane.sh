#!/usr/bin/env bash
# Tạo chứng chỉ client cho control plane và thêm vào danh sách tin cậy của cụm.
# Chạy trên laptop-1, trong thư mục repo:   sudo ./infra/node/30-trust-control-plane.sh
# Chứng chỉ này có toàn quyền trên cụm: giữ trong secrets/, không gửi đi đâu.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
SECRETS="${1:-$(cd "$(dirname "$0")/../.." && pwd)/secrets}"
install -d -m 700 "${SECRETS}"

if [[ ! -f "${SECRETS}/client.key" ]]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -sha384 -nodes \
    -keyout "${SECRETS}/client.key" -out "${SECRETS}/client.crt" \
    -days 3650 -subj "/CN=minicloud-control-plane"
fi
chmod 600 "${SECRETS}/client.key"

if incus config trust list --format csv | grep -q minicloud-control-plane; then
  echo "Chứng chỉ đã được tin cậy từ trước."
else
  incus config trust add-certificate "${SECRETS}/client.crt" --name minicloud-control-plane
fi
cp /var/lib/incus/cluster.crt "${SECRETS}/cluster.crt"
if [[ -n "${SUDO_USER:-}" ]]; then
  chown -R "${SUDO_USER}:" "${SECRETS}"
fi

ADDR="$(incus config get core.https_address)"
cat <<EOF

Điền vào .env của control plane:
  MINICLOUD_DRIVER=incus
  MINICLOUD_INCUS_URL=https://${ADDR}
  MINICLOUD_INCUS_CLIENT_CERT=${SECRETS}/client.crt
  MINICLOUD_INCUS_CLIENT_KEY=${SECRETS}/client.key
  MINICLOUD_INCUS_CA_CERT=${SECRETS}/cluster.crt
EOF
