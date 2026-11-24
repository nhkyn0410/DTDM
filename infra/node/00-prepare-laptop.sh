#!/usr/bin/env bash
# Chuẩn bị một laptop Ubuntu Server 24.04 làm node của cụm minicloud.
# Dùng:   sudo ./00-prepare-laptop.sh laptop-1
# Tuỳ chọn: CHARGE_LIMIT=80 (giới hạn mức sạc pin, nếu máy hỗ trợ)
set -euo pipefail

NODE_NAME="${1:?Thiếu tên node. Ví dụ: sudo $0 laptop-1}"
CHARGE_LIMIT="${CHARGE_LIMIT:-80}"

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
if ! grep -q 'VERSION_ID="24.04"' /etc/os-release; then
  echo "Cảnh báo: script viết cho Ubuntu Server 24.04." >&2
fi

echo "==> Đặt hostname: ${NODE_NAME}"
hostnamectl set-hostname "${NODE_NAME}"

echo "==> Gập máy không ngủ"
install -d /etc/systemd/logind.conf.d
cat >/etc/systemd/logind.conf.d/90-minicloud.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
systemctl restart systemd-logind

echo "==> Cài gói cần thiết"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl ca-certificates gnupg jq chrony ethtool zfsutils-linux stress-ng
systemctl enable --now chrony   # metering và cụm Incus cần đồng hồ chuẩn

echo "==> Giới hạn mức sạc pin ở ${CHARGE_LIMIT}% (nếu máy hỗ trợ)"
if compgen -G "/sys/class/power_supply/BAT*/charge_control_end_threshold" >/dev/null; then
  cat >/usr/local/sbin/minicloud-charge-limit <<EOF
#!/bin/sh
for f in /sys/class/power_supply/BAT*/charge_control_end_threshold; do
  [ -w "\$f" ] && echo ${CHARGE_LIMIT} > "\$f"
done
exit 0
EOF
  chmod 755 /usr/local/sbin/minicloud-charge-limit
  cat >/etc/systemd/system/minicloud-charge-limit.service <<'EOF'
[Unit]
Description=minicloud: giới hạn mức sạc pin

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/minicloud-charge-limit

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now minicloud-charge-limit.service
  echo "  Đã đặt."
else
  echo "  Máy không hỗ trợ qua Linux; nếu BIOS có tuỳ chọn giới hạn sạc thì bật ở đó."
fi

echo "==> Kiểm tra phần cứng"
if grep -Eq '(vmx|svm)' /proc/cpuinfo; then
  echo "  VT-x/AMD-V: có"
else
  echo "  CẢNH BÁO: CPU không có VT-x/AMD-V, chỉ chạy được container."
fi
if [[ -e /dev/kvm ]]; then
  echo "  /dev/kvm: có"
else
  echo "  /dev/kvm: chưa có. Bật Virtualization trong BIOS để chạy VM."
fi
if compgen -G "/sys/class/powercap/intel-rapl:*" >/dev/null; then
  echo "  RAPL: có"
else
  echo "  RAPL: không thấy, chỉ đo điện được qua pin."
fi

echo "==> Cổng mạng dây (dùng cho Wake-on-LAN)"
for dev in /sys/class/net/*; do
  name="$(basename "${dev}")"
  [[ -e "${dev}/device" && ! -d "${dev}/wireless" ]] || continue
  wol="$(ethtool "${name}" 2>/dev/null | awk -F': ' '/Wake-on:/ {v=$2} END {print v}')"
  echo "  ${name}  MAC $(cat "${dev}/address")  Wake-on: ${wol:-không rõ}"
done

cat <<'EOF'

Xong. Việc còn lại làm bằng tay:
  1. Ghi MAC cổng mạng dây ở trên vào docs/hardware.md.
  2. Bật Wake-on-LAN trong BIOS, rồi thêm 'wakeonlan: true' cho cổng mạng dây
     trong /etc/netplan/*.yaml và chạy 'sudo netplan apply'.
  3. Chạy tiếp: sudo ./10-install-incus.sh
EOF
