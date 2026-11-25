#!/usr/bin/env bash
# Thí nghiệm "Overcommit": bán vCPU gấp mấy lần số CPU thật thì còn chấp nhận được?
#
# Mọi container bị ghim vào cùng CPUS CPU thật (mặc định 2: "0-1"), mỗi container chạy sysbench
# cpu 1 luồng cùng lúc. Số container = CPUS x tỷ lệ (1:1, 2:1, 4:1). In ra số event mỗi giây và độ
# trễ p95 của từng container; so với 1:1 để thấy mỗi khách còn được bao nhiêu.
#   sudo bash infra/bench/overcommit.sh [SECONDS] [CPUS]
set -euo pipefail

SECONDS_PER_RUN="${1:-30}"
CPUS="${2:-2}"
PIN="0-$((CPUS - 1))"
IMAGE="images:ubuntu/24.04/cloud"
POOL="${POOL:-tenants}"
MAX=$((CPUS * 4))

names() { for i in $(seq 1 "$1"); do echo "oc-$i"; done; }
cleanup() { for c in $(names "$MAX"); do incus delete -f "$c" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT

echo "==> Tạo ${MAX} container ghim vào CPU ${PIN}" >&2
for c in $(names "$MAX"); do
  incus launch "$IMAGE" "$c" --storage "$POOL" -c limits.cpu="$PIN" -c limits.memory=512MiB >/dev/null
done
for c in $(names "$MAX"); do
  incus exec "$c" -- cloud-init status --wait >/dev/null 2>&1 || true
  incus exec "$c" -- bash -c 'DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y -qq sysbench >/dev/null'
done

echo "ratio,container,events_per_second,latency_p95_ms"
for ratio in 1 2 4; do
  n=$((CPUS * ratio))
  out="$(mktemp -d)"
  for c in $(names "$n"); do
    incus exec "$c" -- sysbench cpu --threads=1 --time="${SECONDS_PER_RUN}" --percentile=95 run >"${out}/${c}" &
  done
  wait
  for c in $(names "$n"); do
    eps="$(awk '/events per second/ {print $4}' "${out}/${c}")"
    p95="$(awk '/95th percentile/ {print $3}' "${out}/${c}")"
    echo "${ratio}:1,${c},${eps},${p95}"
  done
  rm -rf "${out}"
done
