#!/usr/bin/env bash
# Khởi tạo cụm Incus trên laptop-1. Chỉ chạy một lần, trên một máy.
# Dùng:   sudo ./20-bootstrap-cluster.sh <tên-node> <IP-LAN-của-node>
#   POOL_SOURCE=/dev/sda3   dùng phân vùng trống cho ZFS (khuyên dùng)
#   POOL_SIZE=60GiB         nếu không có phân vùng: tạo file loop dung lượng này
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
NODE_NAME="${1:?Thiếu tên node, ví dụ laptop-1}"
NODE_ADDR="${2:?Thiếu IP LAN của node, ví dụ 192.168.1.11}"
POOL_SIZE="${POOL_SIZE:-60GiB}"
POOL_SOURCE="${POOL_SOURCE:-}"

if [[ -n "${POOL_SOURCE}" ]]; then
  POOL_LINE="source: ${POOL_SOURCE}"
else
  POOL_LINE="size: ${POOL_SIZE}"
fi

PRESEED=/root/minicloud-bootstrap.yaml
cat >"${PRESEED}" <<EOF
config:
  core.https_address: ${NODE_ADDR}:8443
networks:
- name: incusbr0
  type: bridge
  config:
    ipv4.address: auto
    ipv4.nat: "true"
    ipv6.address: none
storage_pools:
- name: local
  driver: zfs
  config:
    ${POOL_LINE}
profiles:
- name: default
  devices:
    root:
      path: /
      pool: local
      type: disk
    eth0:
      name: eth0
      network: incusbr0
      type: nic
cluster:
  server_name: ${NODE_NAME}
  enabled: true
EOF

incus admin init --preseed <"${PRESEED}"
incus cluster list

cat <<'EOF'

Cụm đã có node đầu tiên. Tạo token cho hai máy còn lại (mỗi token dùng một lần):
  sudo incus cluster add laptop-2
  sudo incus cluster add laptop-3
Rồi trên từng máy đó chạy 21-join-cluster.sh với token tương ứng.
EOF
