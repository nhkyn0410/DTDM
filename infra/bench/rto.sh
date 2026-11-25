#!/usr/bin/env bash
# Thí nghiệm "Chịu lỗi": control plane chết đột ngột thì bao lâu sống lại, và app của khách có bị
# ảnh hưởng không?
#
# Mỗi vòng giết tiến trình minicloud-api bằng SIGKILL (như khi tiến trình crash hoặc bị OOM giết),
# rồi đo:
#   health_s   bao lâu sau /healthz trả 200 lại (systemd tự chạy lại nhờ Restart=on-failure)
#   api_s      bao lâu sau một lệnh API thật chạy được: /v1/admin/overview đọc database và hỏi Incus
#   app_ok, app_fail  số request tới app của khách qua Traefik (5 request mỗi giây, từ 2 giây trước
#              khi giết tới 5 giây sau khi API sống lại) trả 200 và không trả 200
#   sudo MINICLOUD_API_KEY=mc_... bash infra/bench/rto.sh APP_HOST [ROUNDS] > rto.csv
# APP_HOST là tên miền một app đang chạy, ví dụ hello-0027e5.vexenhanh.id.vn. Key phải là key của
# operator; không có key thì cột api_s để trống. Script không in key ra.
set -euo pipefail

APP_HOST="$1"
ROUNDS="${2:-5}"
API="${API:-http://127.0.0.1:8000}"
TRAEFIK="${TRAEFIK:-http://127.0.0.1:8080}"
UNIT="${UNIT:-minicloud-api}"

now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN {printf "%.2f", b-a}'; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$@" || true; }
api_ok() { [[ "$(code -H "Authorization: Bearer ${MINICLOUD_API_KEY}" "${API}/v1/admin/overview")" == 200 ]]; }
health_ok() { [[ "$(code "${API}/healthz")" == 200 ]]; }

probe() {  # mỗi dòng: thời điểm, mã HTTP của app khách
  while :; do
    echo "$(now),$(code -H "Host: ${APP_HOST}" "${TRAEFIK}/")"
    sleep 0.2
  done
}

if [[ "$(code -H "Host: ${APP_HOST}" "${TRAEFIK}/")" != 200 ]]; then
  echo "App ${APP_HOST} không trả 200 qua Traefik: chọn app khác" >&2
  exit 1
fi

echo "round,health_s,api_s,app_ok,app_fail"
for r in $(seq 1 "$ROUNDS"); do
  until health_ok; do sleep 0.5; done
  sleep 15  # để worker chạy xong vòng đầu sau lần khởi động trước, và systemd không coi là khởi động dồn dập
  log="$(mktemp)"
  probe >"$log" &
  prober=$!
  sleep 2
  t0="$(now)"
  systemctl kill -s KILL "$UNIT"
  for _ in $(seq 1 30); do health_ok || break; sleep 0.1; done  # chờ tiến trình cũ chết hẳn
  until health_ok; do sleep 0.1; done
  health_s="$(since "$t0")"
  api_s=""
  if [[ -n "${MINICLOUD_API_KEY:-}" ]]; then
    until api_ok; do sleep 0.1; done
    api_s="$(since "$t0")"
  fi
  sleep 5
  kill "$prober" 2>/dev/null || true
  wait "$prober" 2>/dev/null || true
  ok="$(awk -F, '$2 == 200' "$log" | wc -l)"
  fail="$(awk -F, '$2 != 200' "$log" | wc -l)"
  rm -f "$log"
  echo "${r},${health_s},${api_s},${ok},${fail}"
done
