#!/usr/bin/env bash
# Cho laptop-2 hoặc laptop-3 gia nhập cụm.
# Dùng:   sudo ./21-join-cluster.sh <tên-node> <IP-LAN-của-node> <token>
#   Token lấy trên laptop-1 bằng: sudo incus cluster add <tên-node>
#   POOL_SOURCE / POOL_SIZE / POOL_DRIVER giống script 20; POOL_DRIVER phải trùng với laptop-1.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
NODE_NAME="${1:?Thiếu tên node, ví dụ laptop-2}"
NODE_ADDR="${2:?Thiếu IP LAN của node}"
TOKEN="${3:?Thiếu token từ 'incus cluster add'}"
POOL_SIZE="${POOL_SIZE:-60GiB}"
POOL_SOURCE="${POOL_SOURCE:-}"
POOL_DRIVER="${POOL_DRIVER:-zfs}"

# Driver của pool do cụm quyết định; ở đây chỉ cần có sẵn công cụ của nó.
case "${POOL_DRIVER}" in
  zfs) command -v zpool >/dev/null || apt-get install -y zfsutils-linux ;;
  btrfs) command -v mkfs.btrfs >/dev/null || apt-get install -y btrfs-progs ;;
  *)
    echo "POOL_DRIVER chỉ nhận zfs hoặc btrfs." >&2
    exit 1
    ;;
esac

if [[ -n "${POOL_SOURCE}" ]]; then
  POOL_KEY=source
  POOL_VALUE="${POOL_SOURCE}"
else
  POOL_KEY=size
  POOL_VALUE="${POOL_SIZE}"
fi

PRESEED=/root/minicloud-join.yaml
umask 077   # token là bí mật cho tới khi dùng xong
cat >"${PRESEED}" <<EOF
cluster:
  enabled: true
  server_name: ${NODE_NAME}
  server_address: ${NODE_ADDR}:8443
  cluster_token: ${TOKEN}
  member_config:
  - entity: storage-pool
    name: local
    key: ${POOL_KEY}
    value: ${POOL_VALUE}
EOF

incus admin init --preseed <"${PRESEED}"
rm -f "${PRESEED}"
incus cluster list
