#!/usr/bin/env bash
# Cụm ảo 3 node trong WSL: 3 VM Ubuntu 24.04 chạy đúng các script infra/node như 3 laptop thật,
# cộng một control plane riêng ở cổng 8100 để làm smoke test. Không đụng tới staging: VM nằm
# trong project Incus "lab", control plane thử có database và file tuyến riêng.
# Chạy trong WSL bằng root, từ thư mục repo:
#   sudo bash infra/lab/lab.sh up             # tạo VM, chạy script 00, 10, 20, 21, 30 (vài phút)
#   sudo bash infra/lab/lab.sh cp             # chạy hoặc cập nhật control plane thử (cổng 8100)
#   sudo bash infra/lab/lab.sh stop           # dừng control plane thử và 3 VM, giữ lại để dùng tiếp
#   sudo bash infra/lab/lab.sh start          # bật lại 3 VM và control plane thử
#   sudo bash infra/lab/lab.sh destroy --yes  # xoá 3 VM, project lab và /root/minicloud-lab
#   POOL_DRIVER=btrfs ... lab.sh up             # pool btrfs như node chạy trong WSL (ADR-0006)
# Smoke test: infra/lab/smoke.sh. Hướng dẫn: docs/dev-incus-wsl.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LAB=/root/minicloud-lab # control plane thử: code, database, chứng chỉ, file tuyến, log
PROJECT=lab             # project Incus chứa 3 VM; khách của staging nằm trong các project mc-*
NODES=(1 2 3)

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi

vm() { local name="$1"; shift; incus exec -n --project "${PROJECT}" "${name}" -- "$@"; }
node_script() { local name="$1"; shift; incus exec -n --project "${PROJECT}" "${name}" --cwd /root/minicloud/infra/node -- "$@"; }

bridge_prefix() {
  # incusbr0 của WSL, ví dụ 10.250.187.1/24 cho ra 10.250.187; VM lab-N nhận IP <prefix>.1N.
  local cidr
  cidr="$(incus network get incusbr0 ipv4.address)"
  echo "${cidr%.*}"
}

need_qemu() {
  if incus info | grep -Eq '^\s+driver: .*qemu'; then
    return
  fi
  echo "==> Cài QEMU cho Incus của WSL (gói incus của Ubuntu chưa kèm), rồi khởi động lại Incus."
  echo "    Container của staging vẫn chạy trong lúc Incus khởi động lại."
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qemu-system-x86 qemu-utils ovmf >/dev/null
  systemctl restart incus
  for _ in $(seq 1 30); do incus info >/dev/null 2>&1 && break; sleep 1; done
}

cmd_up() {
  need_qemu
  local prefix
  prefix="$(bridge_prefix)"
  mkdir -p "${LAB}/logs"
  incus project show "${PROJECT}" >/dev/null 2>&1 ||
    incus project create "${PROJECT}" -c features.images=false -c features.profiles=false

  echo "==> 3 VM lab-1..3: 2 vCPU, 3 GiB RAM, ổ 12 GiB, IP cố định ${prefix}.11-13"
  for i in "${NODES[@]}"; do
    if ! incus info --project "${PROJECT}" "lab-${i}" >/dev/null 2>&1; then
      incus init images:ubuntu/24.04 "lab-${i}" --vm --project "${PROJECT}" \
        -c limits.cpu=2 -c limits.memory=3GiB -d root,size=12GiB -d "eth0,ipv4.address=${prefix}.1${i}"
    fi
    incus start --project "${PROJECT}" "lab-${i}" 2>/dev/null || true
  done
  for i in "${NODES[@]}"; do
    for _ in $(seq 1 90); do vm "lab-${i}" true 2>/dev/null && break; sleep 2; done
  done

  echo "==> Chép repo (bản đang có trên máy, kể cả phần chưa commit) vào từng VM"
  tar -C "${REPO}" --exclude=.git --exclude=.venv --exclude=secrets --exclude=dev-routes \
    --exclude=.env --exclude=backups --exclude='*.db' --exclude=__pycache__ \
    -czf "${LAB}/minicloud-src.tar.gz" .
  for i in "${NODES[@]}"; do
    incus file push --quiet --project "${PROJECT}" "${LAB}/minicloud-src.tar.gz" "lab-${i}/root/minicloud-src.tar.gz"
    vm "lab-${i}" sh -c 'rm -rf /root/minicloud && mkdir -p /root/minicloud &&
      tar -xzf /root/minicloud-src.tar.gz -C /root/minicloud && chmod +x /root/minicloud/infra/node/*.sh'
  done

  echo "==> Script 00 và 10 trên cả 3 VM cùng lúc (log: ${LAB}/logs/lab-N.log)"
  local pids=()
  for i in "${NODES[@]}"; do
    (
      if vm "lab-${i}" sh -c 'command -v incus' >/dev/null 2>&1; then
        echo "Incus đã cài từ lần trước, bỏ qua."
      else
        node_script "lab-${i}" ./00-prepare-laptop.sh "laptop-${i}" &&
          node_script "lab-${i}" ./10-install-incus.sh
      fi
    ) >"${LAB}/logs/lab-${i}.log" 2>&1 &
    pids+=("$!")
  done
  local failed=0
  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      echo "  lab-${NODES[$i]}: lỗi, xem ${LAB}/logs/lab-${NODES[$i]}.log" >&2
      failed=1
    fi
  done
  [[ ${failed} -eq 0 ]] || exit 1
  grep -h -E 'VT-x|/dev/kvm|RAPL' "${LAB}/logs/lab-1.log" || true
  echo "  Incus trong VM: $(vm lab-1 incus --version)"

  echo "==> Script 20 trên laptop-1, script 21 trên laptop-2 và laptop-3"
  # Máy thật có ổ riêng nên dùng POOL_SOURCE; ổ của VM chỉ 12 GiB nên pool loop để nhỏ.
  local log="${LAB}/logs/cluster.log"
  local driver="${POOL_DRIVER:-zfs}"
  echo "    pool: ${driver}"
  if ! vm lab-1 incus cluster list >/dev/null 2>&1; then
    node_script lab-1 env POOL_SIZE=8GiB POOL_DRIVER="${driver}" \
      ./20-bootstrap-cluster.sh laptop-1 "${prefix}.11" >>"${log}" 2>&1
  fi
  for i in 2 3; do
    if ! vm lab-1 incus cluster list --format csv | grep -q "^laptop-${i},"; then
      local token
      token="$(vm lab-1 incus cluster add "laptop-${i}" --quiet | tail -n 1)"
      node_script "lab-${i}" env POOL_SIZE=8GiB POOL_DRIVER="${driver}" \
        ./21-join-cluster.sh "laptop-${i}" "${prefix}.1${i}" "${token}" >>"${log}" 2>&1
    fi
  done

  echo "==> Script 30: chứng chỉ cho control plane, chép sang ${LAB}/secrets"
  node_script lab-1 ./30-trust-control-plane.sh >>"${log}" 2>&1
  install -d -m 700 "${LAB}/secrets"
  for f in client.crt client.key cluster.crt; do
    incus file pull --project "${PROJECT}" "lab-1/root/minicloud/secrets/${f}" "${LAB}/secrets/${f}"
  done
  chmod 600 "${LAB}/secrets/"*
  vm lab-1 incus cluster list
  echo "Tiếp theo: sudo bash infra/lab/lab.sh cp"
}

cmd_cp() {
  local prefix
  prefix="$(bridge_prefix)"
  mkdir -p "${LAB}/app"
  rsync -a --delete \
    --exclude .git --exclude .venv --exclude secrets --exclude dev-routes --exclude .env \
    --exclude backups --exclude '*.db' --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
    "${REPO}/" "${LAB}/app/"
  (cd "${LAB}/app" && uv sync --frozen --no-dev --python /usr/bin/python3 --quiet)
  if [[ ! -f "${LAB}/lab.env" ]]; then
    cat >"${LAB}/lab.env" <<EOF
MINICLOUD_DATABASE_URL=sqlite:///${LAB}/lab.db
MINICLOUD_DRIVER=incus
MINICLOUD_INCUS_URL=https://${prefix}.11:8443
MINICLOUD_INCUS_CLIENT_CERT=${LAB}/secrets/client.crt
MINICLOUD_INCUS_CLIENT_KEY=${LAB}/secrets/client.key
MINICLOUD_INCUS_CA_CERT=${LAB}/secrets/cluster.crt
MINICLOUD_PUBLIC_DOMAIN=lab.test
MINICLOUD_INGRESS_ROUTES_FILE=${LAB}/routes.yml
MINICLOUD_SIGNUP_INVITE_CODE=lab
MINICLOUD_GREEN_PINNED_NODES=laptop-1
EOF
    chmod 600 "${LAB}/lab.env"
  fi
  systemctl stop minicloud-lab 2>/dev/null || true
  systemctl reset-failed minicloud-lab 2>/dev/null || true
  systemd-run --quiet --unit=minicloud-lab \
    --property=EnvironmentFile="${LAB}/lab.env" --property=WorkingDirectory="${LAB}/app" \
    "${LAB}/app/.venv/bin/uvicorn" minicloud.main:create_app --factory --host 127.0.0.1 --port 8100
  for _ in $(seq 1 30); do curl -sf http://127.0.0.1:8100/healthz >/dev/null && break; sleep 1; done
  curl -s http://127.0.0.1:8100/healthz
  echo
  echo "Control plane thử: http://127.0.0.1:8100 (trong WSL), log: journalctl -u minicloud-lab"
}

cmd_stop() {
  systemctl stop minicloud-lab 2>/dev/null || true
  incus stop --project "${PROJECT}" --all 2>/dev/null || true
  incus list --project "${PROJECT}" --format csv -c ns
}

cmd_start() {
  for i in "${NODES[@]}"; do incus start --project "${PROJECT}" "lab-${i}" 2>/dev/null || true; done
  for _ in $(seq 1 90); do vm lab-1 incus cluster list >/dev/null 2>&1 && break; sleep 2; done
  cmd_cp
}

cmd_destroy() {
  if [[ "${1:-}" != "--yes" ]]; then
    echo "Xoá 3 VM, project ${PROJECT} và ${LAB}: chạy lại với --yes" >&2
    exit 1
  fi
  systemctl stop minicloud-lab 2>/dev/null || true
  for i in "${NODES[@]}"; do incus delete --project "${PROJECT}" --force "lab-${i}" 2>/dev/null || true; done
  incus project delete "${PROJECT}" 2>/dev/null || true
  rm -rf "${LAB}"
  echo "Đã xoá cụm ảo."
}

case "${1:-}" in
  up) cmd_up ;;
  cp) cmd_cp ;;
  stop) cmd_stop ;;
  start) cmd_start ;;
  destroy) cmd_destroy "${2:-}" ;;
  *) sed -n '2,12p' "$0" ;;
esac
