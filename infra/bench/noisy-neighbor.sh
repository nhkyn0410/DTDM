#!/usr/bin/env bash
# Thí nghiệm "Hàng xóm ồn ào": cgroups bảo vệ tenant B tốt đến đâu khi tenant A dùng hết CPU?
#
# B chạy sysbench cpu 1 luồng (số event mỗi giây càng cao càng tốt) trong 3 điều kiện, mỗi điều
# kiện lặp REPS lần:
#   1. alone     B chạy một mình
#   2. limited   A chạy stress-ng với nhiều luồng, A bị giới hạn limits.cpu=1 (như máy của khách)
#   3. unlimited như 2 nhưng A không có giới hạn CPU
# Chạy bằng root trên một node (staging hoặc laptop), dùng project default, tự dọn khi xong:
#   sudo bash infra/bench/noisy-neighbor.sh [REPS] [SECONDS]
set -euo pipefail

REPS="${1:-5}"
SECONDS_PER_RUN="${2:-20}"
IMAGE="images:ubuntu/24.04/cloud"
POOL="${POOL:-tenants}"
THREADS="$(nproc)" # A mở số luồng gấp đôi số CPU của node để chắc chắn tranh CPU với B
A_THREADS=$((THREADS * 2))

cleanup() { incus delete -f nb-a nb-b nb-free >/dev/null 2>&1 || true; }
trap cleanup EXIT

launch() { # name [limits.cpu]
  incus launch "$IMAGE" "$1" --storage "$POOL" -c limits.memory=1GiB ${2:+-c limits.cpu=$2} >/dev/null
}
echo "==> Tạo 3 container (node có ${THREADS} CPU)" >&2
launch nb-a 1
launch nb-b 1
launch nb-free
for c in nb-a nb-b nb-free; do
  incus exec "$c" -- cloud-init status --wait >/dev/null 2>&1 || true
  incus exec "$c" -- bash -c 'DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y -qq sysbench stress-ng >/dev/null'
done

bench_b() { # in on B: events per second of a 1-thread sysbench
  incus exec nb-b -- sysbench cpu --threads=1 --time="${SECONDS_PER_RUN}" run |
    awk '/events per second/ {print $4}'
}

run() { # condition noisy-container
  local cond="$1" noisy="${2:-}"
  for i in $(seq 1 "$REPS"); do
    if [[ -n "$noisy" ]]; then
      incus exec "$noisy" -- stress-ng --cpu "$A_THREADS" --timeout "$((SECONDS_PER_RUN + 10))s" >/dev/null 2>&1 &
      sleep 3 # để A chạy hết tốc lực trước khi đo B
    fi
    echo "${cond},${i},$(bench_b)"
    if [[ -n "$noisy" ]]; then wait; fi
  done
}

echo "condition,round,b_events_per_second"
run alone
run limited nb-a
run unlimited nb-free
