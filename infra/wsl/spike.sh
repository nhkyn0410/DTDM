#!/usr/bin/env bash
# Các bước kiểm tra của buổi thử 2 laptop (ADR-0006, docs/buoi-thu-wsl.md). Chạy bằng root, trong
# WSL của một node đã vào cụm; chỉ dùng lệnh incus, không cần control plane:
#   sudo bash spike.sh status            # node của cụm, container thử đang ở đâu
#   sudo bash spike.sh apps              # mỗi node một container có web ở cổng công khai riêng
#   sudo bash spike.sh probe             # gọi từng app qua IP LAN của node đang chạy nó
#   sudo bash spike.sh evacuate <node>   # chuyển container khỏi node đó, rồi gọi lại các app
#   sudo bash spike.sh restore <node>    # đưa về, rồi gọi lại các app
#   sudo bash spike.sh clean             # xoá các container thử
set -uo pipefail
PREFIX=spike
PORT_BASE=20090 # tránh các cổng từ 20000 mà control plane cấp cho khách

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi

members() { # tên<TAB>địa chỉ<TAB>trạng thái của từng node
  incus cluster list --format json |
    jq -r '.[] | [.server_name, (.url | sub("^https://"; "") | sub(":[0-9]+$"; "") | gsub("[\\[\\]]"; "")), .status] | @tsv'
}

spikes() { incus list --format csv -c n | grep "^${PREFIX}-" || true; }

ms() { date +%s%3N; }

cmd_status() {
  echo "== Node"
  members | while IFS=$'\t' read -r node addr status; do printf '%-12s %-18s %s\n' "${node}" "${addr}" "${status}"; done
  echo "== Container thử"
  for name in $(spikes); do
    printf '%-22s %-12s %-9s cổng %s\n' "${name}" "$(incus list "${name}" --format csv -c L)" \
      "$(incus list "${name}" --format csv -c s)" \
      "$(incus config device get "${name}" public-http listen 2>/dev/null | sed 's/.*://')"
  done
}

cmd_apps() {
  local i=0 name port
  while IFS=$'\t' read -r node _ status; do
    name="${PREFIX}-${node}"
    port=$((PORT_BASE + i))
    i=$((i + 1))
    if [[ "${status,,}" != online ]]; then # JSON ghi "Online", bảng của incus ghi "ONLINE"
      echo "${node}: ${status}, bỏ qua"
      continue
    fi
    if ! incus info "${name}" >/dev/null 2>&1; then
      incus launch images:ubuntu/24.04 "${name}" --target "${node}" \
        -c cluster.evacuate=migrate -c limits.memory=512MiB </dev/null >/dev/null
      incus config device add "${name}" public-http proxy \
        listen="tcp:0.0.0.0:${port}" connect=tcp:127.0.0.1:80 </dev/null >/dev/null
    fi
    # Web server nhỏ do systemd giữ, để còn chạy sau khi container được chuyển và khởi động lại.
    incus exec -n "${name}" -- sh -c "
      mkdir -p /srv/www && echo 'hello from ${name}' > /srv/www/index.html &&
      printf '[Unit]\nDescription=hello\n[Service]\nExecStart=/usr/bin/python3 -m http.server 80 --directory /srv/www\nRestart=always\n[Install]\nWantedBy=multi-user.target\n' > /etc/systemd/system/hello.service &&
      systemctl daemon-reload && systemctl enable --now hello.service >/dev/null 2>&1"
    echo "${name}: chạy trên ${node}, cổng ${port}"
  done < <(members)
}

cmd_probe() {
  local failed=0 name node port addr body
  declare -A address
  while IFS=$'\t' read -r node addr _; do address["${node}"]="${addr}"; done < <(members)
  for name in $(spikes); do
    node="$(incus list "${name}" --format csv -c L)"
    port="$(incus config device get "${name}" public-http listen | sed 's/.*://')"
    addr="${address[${node}]:-?}"
    body=""
    for _ in $(seq 1 15); do # web server cần vài giây sau khi container khởi động
      body="$(curl -s --max-time 3 "http://${addr}:${port}/")" && [[ -n "${body}" ]] && break
      body=""
      sleep 1
    done
    if [[ -n "${body}" ]]; then
      echo "ĐẠT        ${name} trên ${node}: http://${addr}:${port} -> ${body}"
    else
      echo "KHÔNG ĐẠT  ${name} trên ${node}: http://${addr}:${port} không trả lời"
      failed=$((failed + 1))
    fi
  done
  return "${failed}"
}

cmd_move() { # evacuate|restore <node>
  local start
  start="$(ms)"
  incus cluster "$1" "$2" --force
  echo "incus cluster $1 $2: $((($(ms) - start) / 1000)) s"
  cmd_status
  cmd_probe
}

cmd_clean() {
  for name in $(spikes); do incus delete -f "${name}" && echo "đã xoá ${name}"; done
}

case "${1:-}" in
  status) cmd_status ;;
  apps) cmd_apps ;;
  probe) cmd_probe ;;
  evacuate | restore) cmd_move "$1" "${2:?Thiếu tên node}" ;;
  clean) cmd_clean ;;
  *) sed -n '2,10p' "$0" ;;
esac
