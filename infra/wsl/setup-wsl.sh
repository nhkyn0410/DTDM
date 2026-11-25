#!/usr/bin/env bash
# Chuẩn bị distro WSL2 (Ubuntu 24.04) làm node minicloud theo ADR-0006. Chạy bằng root trong WSL:
#   sudo bash infra/wsl/setup-wsl.sh prepare laptop-2                # tên node, systemd, gói cần thiết,
#                                                                    # chép repo vào /root/minicloud
#   sudo bash infra/wsl/setup-wsl.sh prepare laptop-2 --reset-incus  # xoá Incus cũ trong WSL này trước
#                                                                    # (MẤT mọi instance của nó)
#   sudo bash infra/wsl/setup-wsl.sh harden     # WSL không thấy ổ Windows và không chạy được .exe nữa
#   sudo bash infra/wsl/setup-wsl.sh unharden   # bật lại hai thứ đó
#   sudo bash infra/wsl/setup-wsl.sh status
# Sau prepare, harden hay unharden: chạy "wsl --shutdown" trên Windows để WSL đọc lại /etc/wsl.conf.
# Hướng dẫn đầy đủ: docs/buoi-thu-wsl.md.
set -euo pipefail

WSL_CONF="${WSL_CONF:-/etc/wsl.conf}"
REPO_DEST="${REPO_DEST:-/root/minicloud}"
REPO_SRC="$(cd "$(dirname "$0")/../.." && pwd)"

in_wsl() {
  # MINICLOUD_ALLOW_NON_WSL=1 chỉ để thử script này trên máy ảo thường.
  [[ "${MINICLOUD_ALLOW_NON_WSL:-0}" == 1 ]] || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null
}

set_ini() { # set_ini <mục> <khoá> <giá trị>: sửa WSL_CONF, giữ nguyên các dòng khác
  python3 - "${WSL_CONF}" "$1" "$2" "$3" <<'PY'
import pathlib
import re
import sys

path, section, key, value = sys.argv[1:5]
conf = pathlib.Path(path)
lines = conf.read_text().splitlines() if conf.exists() else []
out, in_section, seen, done = [], False, False, False
for line in lines:
    header = re.match(r"^\s*\[(.+?)\]\s*$", line)
    if header:
        if in_section and not done:
            out.append(f"{key}={value}")
            done = True
        in_section = header.group(1).strip().lower() == section.lower()
        seen = seen or in_section
        out.append(line)
        continue
    if in_section and re.match(rf"^\s*{re.escape(key)}\s*=", line):
        if not done:
            out.append(f"{key}={value}")
            done = True
        continue
    out.append(line)
if in_section and not done:
    out.append(f"{key}={value}")
if not seen:
    if out and out[-1] != "":
        out.append("")
    out += [f"[{section}]", f"{key}={value}"]
conf.write_text("\n".join(out) + "\n")
PY
}

incus_has_config() {
  command -v incus >/dev/null 2>&1 && [[ -n "$(incus storage list --format csv 2>/dev/null)" ]]
}

reset_incus() {
  # Thứ tự quan trọng: pool btrfs vẫn gắn và có subvolume chỉ-đọc thì không xoá được thư mục.
  if command -v incus >/dev/null 2>&1; then
    timeout 180 incus admin shutdown >/dev/null 2>&1 || true # dừng mọi instance rồi dừng daemon
  fi
  systemctl stop incus.service incus.socket incus-user.service incus-user.socket 2>/dev/null || true
  awk '$2 ~ "^/var/lib/incus" {print $2}' /proc/mounts | sort -r | while read -r mount; do
    umount -l "${mount}" 2>/dev/null || true
  done
  losetup -nO NAME,BACK-FILE 2>/dev/null | awk '$2 ~ "^/var/lib/incus/" {print $1}' | while read -r dev; do
    losetup -d "${dev}" 2>/dev/null || true
  done
  DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq 'incus*' >/dev/null 2>&1 || true
  if ! rm -rf /var/lib/incus /var/log/incus /var/cache/incus 2>/dev/null; then
    echo "Không xoá hết được /var/lib/incus. Chạy 'wsl --shutdown' trên Windows rồi chạy lại lệnh này." >&2
    exit 1
  fi
}

cmd_prepare() {
  local node="${1:?Thiếu tên node, ví dụ: prepare laptop-2}"
  local reset="${2:-}"
  if ! in_wsl; then
    echo "Script này chạy bên trong WSL2." >&2
    exit 1
  fi
  if incus_has_config && [[ "${reset}" != "--reset-incus" ]]; then
    echo "Incus trong WSL này đã có pool hoặc mạng (có thể từ docs/dev-incus-wsl.md)." >&2
    echo "Muốn vào cụm, Incus phải trống: chạy lại với --reset-incus. Việc đó xoá MỌI instance trong WSL này." >&2
    exit 1
  fi

  echo "==> ${WSL_CONF}: tên node ${node}, bật systemd"
  set_ini boot systemd true
  set_ini network hostname "${node}"

  echo "==> Gói cần thiết"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl ca-certificates gnupg jq rsync python3 btrfs-progs stress-ng >/dev/null

  if [[ "${reset}" == "--reset-incus" ]] && { command -v incus >/dev/null 2>&1 || [[ -d /var/lib/incus ]]; }; then
    echo "==> Xoá Incus cũ: instance, pool và mạng của nó trong WSL này"
    reset_incus
  fi

  if [[ "${REPO_SRC}" != "${REPO_DEST}" ]]; then
    echo "==> Chép repo vào ${REPO_DEST} (sau khi harden, WSL không còn thấy ổ Windows)"
    mkdir -p "${REPO_DEST}"
    rsync -a --delete --exclude .git --exclude .venv --exclude secrets --exclude .env \
      --exclude backups --exclude dev-routes --exclude '*.db' --exclude __pycache__ \
      "${REPO_SRC}/" "${REPO_DEST}/"
    chmod +x "${REPO_DEST}"/infra/node/*.sh "${REPO_DEST}"/infra/wsl/*.sh
  fi

  cat <<EOF

Xong phần chuẩn bị. Việc tiếp theo:
  1. Trên Windows: wsl --shutdown, rồi mở lại WSL để nhận tên node và systemd.
  2. Trong WSL: ip -4 -br addr   phải thấy IP LAN của Windows (mạng mirrored).
  3. cd ${REPO_DEST}/infra/node && sudo ./10-install-incus.sh
  4. Máy A: sudo POOL_DRIVER=btrfs POOL_SIZE=40GiB ./20-bootstrap-cluster.sh ${node} <IP LAN>
     Máy B: sudo POOL_DRIVER=btrfs POOL_SIZE=40GiB ./21-join-cluster.sh <tên node> <IP LAN> <token>
EOF
}

cmd_harden() {
  set_ini automount enabled false
  set_ini automount mountFsTab false
  set_ini interop enabled false
  set_ini interop appendWindowsPath false
  echo "Đã tắt: WSL sẽ không thấy ổ Windows và không chạy được .exe sau khi chạy 'wsl --shutdown'."
  echo "Từ giờ dùng repo ở ${REPO_DEST}. Muốn chép bản mới vào: unharden, wsl --shutdown, prepare lại."
}

cmd_unharden() {
  set_ini automount enabled true
  set_ini automount mountFsTab true
  set_ini interop enabled true
  set_ini interop appendWindowsPath true
  echo "Đã bật lại. Chạy 'wsl --shutdown' trên Windows để có hiệu lực."
}

cmd_status() {
  echo "nhân: $(uname -r)"
  echo "tên máy: $(hostname)"
  echo "init: $(ps -p 1 -o comm=)"
  echo "ổ Windows: $([[ -d /mnt/c/Windows ]] && echo 'đang thấy /mnt/c' || echo 'không thấy')"
  echo "== ${WSL_CONF}"
  cat "${WSL_CONF}" 2>/dev/null || echo "(chưa có)"
  echo "== IPv4 (mạng mirrored thì phải trùng IP LAN của Windows)"
  ip -4 -br addr show scope global || true
  echo "== Incus"
  if command -v incus >/dev/null 2>&1; then
    incus --version
    incus cluster list --format csv 2>/dev/null || incus storage list --format csv 2>/dev/null || true
  else
    echo "chưa cài"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ $EUID -ne 0 ]]; then
    echo "Cần chạy bằng sudo." >&2
    exit 1
  fi
  case "${1:-}" in
    prepare) shift; cmd_prepare "$@" ;;
    harden) cmd_harden ;;
    unharden) cmd_unharden ;;
    status) cmd_status ;;
    *) sed -n '2,12p' "$0" ;;
  esac
fi
