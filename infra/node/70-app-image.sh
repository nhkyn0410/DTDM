#!/usr/bin/env bash
# Dựng ảnh "minicloud-app-base": Ubuntu 24.04 có sẵn các runtime mà buildpack cần (nginx,
# Apache với PHP, composer, python3-venv, Node.js). Ứng dụng triển khai từ zip hay GitHub chạy
# trên ảnh này thì không phải tải các gói đó mỗi lần, nên lên nhanh hơn nhiều.
#
# Chạy trên một node; trong cụm, Incus tự chép ảnh sang node khác khi cần:
#   sudo ./infra/node/70-app-image.sh
# Chạy lại để cập nhật gói (máy đang chạy không bị ảnh hưởng). Lần đầu xong thì bật:
#   echo MINICLOUD_APP_IMAGE=app-base | sudo tee -a /etc/minicloud/minicloud.env
#   sudo systemctl restart minicloud-api
set -euo pipefail

ALIAS=minicloud-app-base
BUILDER=minicloud-app-base-build
SOURCE=images:ubuntu/24.04/cloud
NODE_VERSION=22.14.0 # giữ khớp với NODE_VERSION trong src/minicloud/buildpack.py

if [[ ${EUID} -ne 0 ]]; then
  echo "Chạy bằng sudo" >&2
  exit 1
fi
started=$(date +%s)

echo "==> Máy dựng ảnh ${BUILDER} (project default)"
incus delete --force "${BUILDER}" >/dev/null 2>&1 || true
trap 'incus delete --force "${BUILDER}" >/dev/null 2>&1 || true' EXIT
incus launch "${SOURCE}" "${BUILDER}" >/dev/null
incus exec "${BUILDER}" -- cloud-init status --wait >/dev/null || true

echo "==> Cài runtime (Node.js ${NODE_VERSION})"
incus exec "${BUILDER}" --env NODE_VERSION="${NODE_VERSION}" -- bash -euo pipefail -c '
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update -qq
apt-get -o DPkg::Lock::Timeout=300 install -y -qq --no-install-recommends \
  nginx apache2 libapache2-mod-php composer python3-venv ca-certificates curl xz-utils >/dev/null
# Buildpack bật đúng máy chủ web mà ứng dụng cần; trong ảnh thì tắt cả hai để khỏi giành cổng 80.
# Lần khởi động đầu, machine-id còn trống nên systemd chạy preset-all và bật lại mọi dịch vụ
# (thấy trên staging: Apache "failed" vì nginx đã giữ cổng 80); file preset giữ chúng tắt.
systemctl disable --now nginx apache2 >/dev/null 2>&1
mkdir -p /etc/systemd/system-preset
printf "disable nginx.service\ndisable apache2.service\ndisable apache-htcacheclean.service\n" \
  >/etc/systemd/system-preset/10-minicloud.preset
case "$(uname -m)" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; *) arch=$(uname -m) ;; esac
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.xz" |
  tar -xJ -C /usr/local --strip-components=1 --wildcards "*/bin" "*/lib" "*/include" "*/share"
echo "node $(node --version), npm $(npm --version), $(python3 --version), PHP $(php -r "echo PHP_VERSION;")"
apt-get clean
rm -rf /var/lib/apt/lists/* # buildpack tự chạy apt-get update nếu cần cài thêm
# Mỗi máy tạo từ ảnh phải có machine-id riêng: dùng chung thì DHCP có thể cấp trùng IP.
cloud-init clean --logs --machine-id
'

echo "==> Đóng ảnh ${ALIAS}"
old=$(incus image list "${ALIAS}" -f csv -c F 2>/dev/null | head -1)
incus stop "${BUILDER}"
incus publish "${BUILDER}" --alias "${ALIAS}" --reuse \
  description="Ubuntu 24.04, runtime cho ứng dụng minicloud (Node.js ${NODE_VERSION})" >/dev/null
new=$(incus image list "${ALIAS}" -f csv -c F | head -1)
if [[ -n "${old}" && "${old}" != "${new}" ]]; then
  incus image delete "${old}" >/dev/null 2>&1 || true # bản cũ, không còn alias
fi
incus image list "${ALIAS}" -f csv -c lfs
echo "Xong sau $(( $(date +%s) - started )) giây."
if ! grep -q '^MINICLOUD_APP_IMAGE=' /etc/minicloud/minicloud.env 2>/dev/null; then
  echo "Bật cho control plane:"
  echo "  echo MINICLOUD_APP_IMAGE=app-base | sudo tee -a /etc/minicloud/minicloud.env"
  echo "  sudo systemctl restart minicloud-api"
fi
