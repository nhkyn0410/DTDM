#!/bin/bash
# App mẫu cho nhóm máy tự co giãn: mỗi request tốn khoảng 0,1 giây CPU, trang cho biết máy nào
# trả lời, nên khi bắn tải sẽ thấy request được chia sang máy mới.
set -e
cat > /usr/local/bin/minicloud-demo.py <<'PY'
import hashlib, http.server, socket


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        digest = b'minicloud'
        for _ in range(300000):  # about 0.1 s of CPU
            digest = hashlib.sha256(digest).digest()
        body = ('<h1>minicloud autoscaling</h1><p>Máy trả lời: %s</p>' % socket.gethostname()).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 256  # the default 5 drops connections under load (retries take 1-3 s)
    daemon_threads = True


Server(('', 80), Handler).serve_forever()
PY
cat > /etc/systemd/system/minicloud-demo.service <<'UNIT'
[Unit]
Description=minicloud demo app
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/minicloud-demo.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now minicloud-demo.service
