#!/usr/bin/env bash
# Cài Incus từ kho Zabbly (bản stable, kèm sẵn QEMU để chạy VM).
# Dùng:   sudo ./10-install-incus.sh [tên-user-quản-trị]
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
ADMIN_USER="${1:-${SUDO_USER:-}}"

install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://pkgs.zabbly.com/key.asc -o /etc/apt/keyrings/zabbly.asc
CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
ARCH="$(dpkg --print-architecture)"
cat >/etc/apt/sources.list.d/zabbly-incus-stable.sources <<EOF
Enabled: yes
Types: deb
URIs: https://pkgs.zabbly.com/incus/stable
Suites: ${CODENAME}
Components: main
Architectures: ${ARCH}
Signed-By: /etc/apt/keyrings/zabbly.asc
EOF

apt-get update
apt-get install -y incus

if [[ -n "${ADMIN_USER}" ]]; then
  usermod -aG incus-admin "${ADMIN_USER}"
  echo "Đã thêm ${ADMIN_USER} vào nhóm incus-admin (đăng nhập lại để có hiệu lực)."
fi
incus --version
echo "Tiếp theo: laptop-1 chạy 20-bootstrap-cluster.sh, laptop-2 và laptop-3 chạy 21-join-cluster.sh."
