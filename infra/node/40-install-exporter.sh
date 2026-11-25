#!/usr/bin/env bash
# Cài exporter đo điện năng (RAPL + pin) thành dịch vụ systemd ở cổng 9101.
# Chạy trên từng laptop, trong thư mục repo:   sudo ./infra/node/40-install-exporter.sh
# Dùng Python có sẵn của Ubuntu, không cần cài FastAPI hay uv trên node.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Cần chạy bằng sudo." >&2
  exit 1
fi
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

apt-get install -y python3 python3-prometheus-client
install -d /opt/minicloud
rm -rf /opt/minicloud/src
cp -r "${REPO_ROOT}/src" /opt/minicloud/src

cat >/etc/systemd/system/minicloud-node-exporter.service <<'EOF'
[Unit]
Description=minicloud: đo điện năng laptop (RAPL + pin) cho Prometheus
After=network-online.target

[Service]
Environment=PYTHONPATH=/opt/minicloud/src
ExecStart=/usr/bin/python3 -m minicloud.green.exporter --port 9101 --node %H
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now minicloud-node-exporter.service
sleep 12   # chờ hai lần đo để có số công suất
curl -s http://localhost:9101/metrics | grep '^minicloud_node' \
  || echo "Chưa có số đo; xem log: journalctl -u minicloud-node-exporter"
