#!/usr/bin/env bash
# Smoke test với cụm ảo (infra/lab/lab.sh) qua control plane thử ở cổng 8100.
# Chạy trong WSL bằng root, sau "lab.sh up" và "lab.sh cp":
#   sudo bash infra/lab/smoke.sh all            # bước 6–8 của smoke test: dàn 3 node, dừng và
#                                               # chạy lại, evacuate, restore, xoá
# Hoặc từng bước:
#   setup | nodes | create [tên...] | list | incus | web | routes | probe
#   op <stop|start|delete> <tên> | evacuate <node> | restore <node> | watch [giây]
#   reconcile | plan | audit [số dòng]
set -uo pipefail
API=http://127.0.0.1:8100
LAB=/root/minicloud-lab
STATE="${LAB}/smoke.env" # API key của tài khoản thử; chỉ root đọc được, không in ra
PROJECT_VM=lab

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi

admin_cli() { (set -a; . "${LAB}/lab.env"; set +a; cd "${LAB}/app" && .venv/bin/minicloud "$@"); }
lab1() { incus exec -n --project "${PROJECT_VM}" lab-1 -- "$@"; }

load_state() {
  if [[ ! -f "${STATE}" ]]; then
    echo "Chưa có tài khoản thử: chạy 'smoke.sh setup' trước." >&2
    exit 1
  fi
  # shellcheck disable=SC1090
  . "${STATE}"
}

api() { # api METHOD PATH [JSON]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "${body}" ]]; then
    curl -s -X "${method}" "${API}${path}" -H "Authorization: Bearer ${KEY}" \
      -H 'content-type: application/json' -d "${body}"
  else
    curl -s -X "${method}" "${API}${path}" -H "Authorization: Bearer ${KEY}"
  fi
}

instances() { api GET "/v1/projects/${PROJECT}/instances"; }
instance_id() { instances | jq -r --arg n "$1" '.[] | select(.name == $n) | .id'; }
node_of() { instances | jq -r --arg n "$1" '.[] | select(.name == $n) | .node'; }

wait_op() {
  local op="$1" status=""
  for _ in $(seq 1 300); do
    status="$(api GET "/v1/operations/${op}" | jq -r .status)"
    case "${status}" in succeeded | failed) break ;; esac
    sleep 2
  done
  api GET "/v1/operations/${op}" | jq -c '{action, status, error}'
  [[ "${status}" == succeeded ]]
}

show() {
  instances | jq -r '.[] | [.name, .status, (.node // "-"), (.ip // "-"), (.public_url // "-")] | @tsv' | column -t
}

do_setup() {
  local r
  r="$(curl -s -X POST "${API}/v1/signup" -H 'content-type: application/json' \
    -d '{"email":"lab@example.com","name":"Lab","org_name":"Lab org","invite_code":"lab"}')"
  if [[ "$(jq -r '.api_key // empty' <<<"${r}")" == "" ]]; then
    echo "Đăng ký không được: ${r}" >&2
    exit 1
  fi
  printf 'KEY=%s\nORG=%s\nPROJECT=%s\n' "$(jq -r .api_key <<<"${r}")" "$(jq -r .org_id <<<"${r}")" \
    "$(jq -r .project_id <<<"${r}")" >"${STATE}"
  chmod 600 "${STATE}"
  . "${STATE}"
  admin_cli make-admin lab@example.com
  admin_cli grant-credits "${ORG}" 5000
}

do_create() {
  local ops=() r ok=0
  for n in "$@"; do
    r="$(api POST "/v1/projects/${PROJECT}/instances" "{\"name\":\"${n}\"}")"
    ops+=("$(jq -r '.operation.id' <<<"${r}")")
  done
  for op in "${ops[@]}"; do wait_op "${op}" || ok=1; done
  show
  return "${ok}"
}

do_web() {
  # Một web server nhỏ ở cổng 80 trong mỗi container, do systemd giữ qua các lần khởi động và di chuyển.
  lab1 sh -c '
    incus list --all-projects --format json |
      jq -r ".[] | select(.project | startswith(\"mc-\")) | select(.status == \"Running\") | \"\(.project) \(.name)\"" |
      while read -r project name; do
        incus exec -n --project "$project" "$name" -- sh -c "
          mkdir -p /srv/www && echo \"hello from $name\" > /srv/www/index.html &&
          printf \"[Unit]\nDescription=hello\n[Service]\nExecStart=/usr/bin/python3 -m http.server 80 --directory /srv/www\nRestart=always\n[Install]\nWantedBy=multi-user.target\n\" > /etc/systemd/system/hello.service &&
          systemctl daemon-reload && systemctl enable --now hello.service >/dev/null 2>&1 && echo \"$name: web chạy\""
      done'
}

do_probe() {
  # Gọi từng app qua đúng địa chỉ node mà Traefik sẽ dùng; mã thoát = số app không trả lời.
  "${LAB}/app/.venv/bin/python" - "${LAB}/routes.yml" <<'PY'
import sys
import time
import urllib.request

import yaml

routes = yaml.safe_load(open(sys.argv[1])) or {}
services = (routes.get("http") or {}).get("services") or {}
if not services:
    print("không còn tuyến nào")
failed = 0
for name, svc in sorted(services.items()):
    url = svc["loadBalancer"]["servers"][0]["url"]
    for attempt in range(10):  # web server trong container cần vài giây sau khi khởi động
        try:
            body = urllib.request.urlopen(url, timeout=5).read().decode().strip()
            break
        except Exception as exc:
            body = f"LỖI {exc}"
            time.sleep(1)
    failed += body.startswith("LỖI")
    print(f"{name:20} {url:30} {body}")
sys.exit(failed)
PY
}

do_reconcile() { api POST /v1/admin/reconcile | jq -c '{checked, changes: [.changes[] | {incus_name, field, before, after}], held_back}'; }

check() { # check <mô tả> <lệnh...>: in ĐẠT/KHÔNG ĐẠT, đếm lỗi
  local what="$1"
  shift
  if "$@"; then
    echo "ĐẠT        ${what}"
  else
    echo "KHÔNG ĐẠT  ${what}"
    FAILURES=$((FAILURES + 1))
  fi
}

spread_over_three() {
  [[ "$(instances | jq -r '[.[] | select(.name | startswith("smoke-")) | .node] | unique | length')" == 3 ]]
}

moved_off() { # moved_off <tên> <node>: instance đang chạy ở node khác
  local node
  node="$(node_of "$1")"
  [[ -n "${node}" && "${node}" != "null" && "${node}" != "$2" ]] &&
    [[ "$(instances | jq -r --arg n "$1" '.[] | select(.name == $n) | .status')" == running ]]
}

no_routes() { ! grep -q loadBalancer "${LAB}/routes.yml" 2>/dev/null; }

no_route_for() { ! grep -q "Host(\`$1-" "${LAB}/routes.yml" 2>/dev/null; }

op_and_wait() { # op_and_wait <stop|start> <tên>
  local r
  r="$(api POST "/v1/instances/$(instance_id "$2")/$1")"
  wait_op "$(jq -r '.id // .operation.id' <<<"${r}")" >/dev/null
}

do_all() {
  FAILURES=0
  [[ -f "${STATE}" ]] || do_setup
  load_state
  echo "== Node"
  api GET /v1/admin/nodes | jq -c '.[] | {name, online, vcpus, memory_mib}'
  echo "== Bước 7: tạo 3 instance"
  check "tạo smoke-1..3 thành công" do_create smoke-1 smoke-2 smoke-3
  check "3 instance nằm trên 3 node khác nhau" spread_over_three
  do_web >/dev/null
  check "cả 3 app trả lời qua địa chỉ node" do_probe
  echo "== Bước 6: dừng rồi chạy lại smoke-3 qua API"
  check "dừng smoke-3" op_and_wait stop smoke-3
  check "tuyến của smoke-3 đã gỡ" no_route_for smoke-3
  check "chạy lại smoke-3" op_and_wait start smoke-3
  check "app của smoke-3 trả lời lại" do_probe
  local victim node
  victim="smoke-2"
  node="$(node_of "${victim}")"
  echo "== Bước 8: evacuate ${node} (đang chạy ${victim})"
  lab1 incus cluster evacuate "${node}" --force >/dev/null
  do_reconcile
  check "${victim} đã sang node khác và vẫn chạy" moved_off "${victim}" "${node}"
  check "app vẫn trả lời sau khi chuyển" do_probe
  echo "== Restore ${node}"
  lab1 incus cluster restore "${node}" --force >/dev/null
  do_reconcile
  check "${victim} về lại ${node}" test "$(node_of "${victim}")" == "${node}"
  check "app trả lời sau khi restore" do_probe
  echo "== Xoá"
  for n in smoke-1 smoke-2 smoke-3; do
    wait_op "$(api DELETE "/v1/instances/$(instance_id "${n}")" | jq -r '.id // .operation.id')" >/dev/null
  done
  check "đã xoá cả 3 instance" test "$(instances | jq '[.[] | select(.name | startswith("smoke-"))] | length')" == 0
  check "không còn tuyến nào" no_routes
  echo
  if [[ ${FAILURES} -eq 0 ]]; then echo "Smoke test: ĐẠT"; else echo "Smoke test: ${FAILURES} mục KHÔNG ĐẠT"; fi
  return "${FAILURES}"
}

case "${1:-}" in
  all) do_all ;;
  setup) do_setup ;;
  nodes) load_state; api GET /v1/admin/nodes | jq -c '.[]' ;;
  create) load_state; shift; do_create "${@:-web-1}" ;;
  list) load_state; show ;;
  incus) lab1 incus list --all-projects --format csv -c ensL4 ;;
  web) do_web ;;
  routes) cat "${LAB}/routes.yml" ;;
  probe) do_probe ;;
  op)
    load_state
    id="$(instance_id "$3")"
    if [[ "$2" == delete ]]; then r="$(api DELETE "/v1/instances/${id}")"; else r="$(api POST "/v1/instances/${id}/$2")"; fi
    wait_op "$(jq -r '.id // .operation.id' <<<"${r}")"
    show
    ;;
  evacuate | restore)
    lab1 incus cluster "$1" "$2" --force
    lab1 incus cluster list --format csv -c nsm
    lab1 incus list --all-projects --format csv -c ensL4
    ;;
  watch)
    load_state
    for _ in $(seq 1 $((${2:-90} / 10))); do
      echo "--- $(date +%T)"
      show
      sleep 10
    done
    ;;
  reconcile) load_state; do_reconcile ;;
  plan) load_state; api GET /v1/admin/green/plan | jq -c . ;;
  audit) load_state; api GET "/v1/orgs/${ORG}/audit" | jq -c --argjson n "${2:-15}" '.[:$n][] | del(.id, .org_id)' ;;
  *) sed -n '2,9p' "$0" ;;
esac
