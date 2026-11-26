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

# Giới hạn kernel cho nhiều container, theo hướng dẫn production của Incus. Mọi container không đặc
# quyền dùng chung một uid trên host nên chia nhau các giới hạn "mỗi user": với 1024 inotify
# instance, staging dừng ở 87 container (systemd trong container thứ 88 không chạy được) dù RAM
# còn trống 11 GB; hạn mức keyring 200 cũng bị chạm.
cat >/etc/sysctl.d/90-minicloud.conf <<'EOF'
fs.inotify.max_queued_events = 1048576
fs.inotify.max_user_instances = 1048576
fs.inotify.max_user_watches = 1048576
kernel.keys.maxkeys = 2000
kernel.keys.maxbytes = 2000000
EOF
sysctl --system >/dev/null

if [[ -n "${ADMIN_USER}" ]]; then
  usermod -aG incus-admin "${ADMIN_USER}"
  echo "Đã thêm ${ADMIN_USER} vào nhóm incus-admin (đăng nhập lại để có hiệu lực)."
fi
incus --version
echo "Tiếp theo: laptop-1 chạy 20-bootstrap-cluster.sh, laptop-2 và laptop-3 chạy 21-join-cluster.sh."
