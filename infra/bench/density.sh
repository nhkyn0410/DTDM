#!/usr/bin/env bash
# Thí nghiệm "Mật độ": một laptop chứa được bao nhiêu container nhỏ đang nghỉ?
#
# Tạo container Ubuntu cấu hình như s1.small (1 vCPU, 1 GiB) theo từng đợt BATCH cái chạy song song,
# đến khi đủ MAX cái, RAM còn trống của node xuống dưới FLOOR_MB, hoặc pool đầy quá 85%. Sau mỗi đợt,
# chờ cloud-init xong và SETTLE giây cho RAM ổn định, rồi in một dòng CSV:
#   containers         số container đang chạy
#   launch_s, ready_s  thời gian từ đầu đợt tới lúc Incus báo chạy, và tới lúc cloud-init xong
#   node_used_mib      RAM node đang dùng (MemTotal - MemAvailable)
#   node_mib_per_ct    RAM node tăng thêm so với lúc chưa có container, chia cho số container
#   incus_mib_per_ct   RAM trung bình Incus đo trong mỗi container (cgroup, gồm cả page cache)
#   load1              tải CPU trung bình 1 phút
# Container tự xoá khi script kết thúc, kể cả khi bị ngắt giữa chừng.
#   sudo bash infra/bench/density.sh [BATCH] [MAX] [FLOOR_MB] > density.csv
set -euo pipefail

BATCH="${1:-10}"
MAX="${2:-100}"
FLOOR_MB="${3:-3072}"
IMAGE="images:ubuntu/24.04/cloud"
POOL="${POOL:-tenants}"
SETTLE="${SETTLE:-20}"
PREFIX="density-"

used_mib() { awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {printf "%d", (t-a)/1024}' /proc/meminfo; }
avail_mib() { awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo; }
pool_pct() {
  incus storage info "$POOL" --bytes \
    | awk -F'"' '/space used/ {u=$2} /total space/ {t=$2} END {printf "%d", u*100/t}'
}
now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN {printf "%.1f", b-a}'; }
name() { printf '%s%03d' "$PREFIX" "$1"; }
running() { incus list -f csv -c ns "$PREFIX" | grep -c ',RUNNING$' || true; }

cleanup() {
  echo "==> Xoá các container ${PREFIX}*" >&2
  incus list -f csv -c n "$PREFIX" | xargs -r -P 8 -n 1 incus delete -f >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Làm nóng: đưa image lên pool ${POOL} để đợt đầu không phải tải" >&2
incus launch "$IMAGE" "${PREFIX}warmup" --storage "$POOL" >/dev/null
incus delete -f "${PREFIX}warmup"
sleep 5
base="$(used_mib)"
echo "==> RAM node lúc chưa có container: ${base} MiB đã dùng, $(avail_mib) MiB trống" >&2

echo "containers,launch_s,ready_s,node_used_mib,node_available_mib,node_mib_per_ct,incus_mib_per_ct,load1"
n=0
while ((n < MAX)); do
  if (($(avail_mib) < FLOOR_MB)); then
    echo "==> Dừng: RAM trống dưới ${FLOOR_MB} MiB" >&2
    break
  fi
  if (($(pool_pct) > 85)); then
    echo "==> Dừng: pool ${POOL} đã dùng quá 85%" >&2
    break
  fi
  first=$((n + 1))
  last=$((n + BATCH > MAX ? MAX : n + BATCH))
  t0="$(now)"
  for i in $(seq "$first" "$last"); do
    incus launch "$IMAGE" "$(name "$i")" --storage "$POOL" \
      -c limits.cpu=1 -c limits.memory=1024MiB >/dev/null &
  done
  wait
  launch_s="$(since "$t0")"
  for i in $(seq "$first" "$last"); do
    incus exec "$(name "$i")" -- cloud-init status --wait >/dev/null 2>&1 &
  done
  wait
  ready_s="$(since "$t0")"
  n="$last"
  sleep "$SETTLE"
  count="$(running)"
  used="$(used_mib)"
  incus_avg="$(incus query "/1.0/instances?recursion=2&project=default" | jq --arg p "$PREFIX" \
    '[.[] | select(.name | startswith($p)) | .state.memory.usage] | if length > 0 then (add / length / 1048576 | floor) else 0 end')"
  per=$(((used - base) / (count > 0 ? count : 1)))
  load1="$(cut -d' ' -f1 /proc/loadavg)"
  echo "${count},${launch_s},${ready_s},${used},$(avail_mib),${per},${incus_avg},${load1}"
done
