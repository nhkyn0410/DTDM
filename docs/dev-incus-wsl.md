# Chạy Incus thật trong WSL2 để phát triển

Ai có Windows 11 với WSL2 (Ubuntu 24.04) đều dựng được một Incus một node, để chạy control plane với driver thật mà không phải chờ cụm 3 laptop. Ngày 2026-11-25, cách này đã giúp phát hiện và sửa hai lỗi mà test giả lập không thấy:

- **Project `restricted` cần profile riêng:** lỗi Incus trả về là "Projects without their own profiles cannot be restricted".
- **Máy đơn không nhận `target`:** lỗi Incus trả về là "Target only allowed when clustered".

Phần chỉ có ở cụm nhiều node (chọn laptop, danh sách thành viên, chuyển instance) vẫn phải thử trên laptop thật.

## Cài một lần

Trong WSL (`wsl -d Ubuntu`):

```bash
sudo apt-get update && sudo apt-get install -y incus
# Nếu báo "dpkg was interrupted": sudo dpkg --configure -a, rồi cài lại.
sudo incus admin init --preseed <<'EOF'
config:
  core.https_address: "[::]:8443"
networks:
- name: incusbr0
  type: bridge
  config:
    ipv4.address: auto
    ipv4.nat: "true"
    ipv6.address: none
storage_pools:
- name: local
  driver: dir
profiles:
- name: default
  devices:
    root: {path: /, pool: local, type: disk}
    eth0: {name: eth0, network: incusbr0, type: nic}
EOF
```

Kernel của WSL không có ZFS nên pool dùng kiểu `dir`; tên pool vẫn là `local` để khớp cấu hình mặc định.

Tạo chứng chỉ cho control plane, lưu vào thư mục `secrets/` của repo (đã có trong `.gitignore`):

```bash
cd /mnt/c/Code/DTDM   # đổi theo chỗ bạn clone repo
mkdir -p secrets
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -sha384 -nodes \
  -keyout secrets/client.key -out secrets/client.crt -days 3650 -subj "/CN=minicloud-dev"
sudo incus config trust add-certificate secrets/client.crt --name minicloud-dev
sudo cp /var/lib/incus/server.crt secrets/server.crt
```

## Mỗi lần dùng

WSL tự tắt khi không còn lệnh nào chạy, kéo theo Incus dừng. Mở một cửa sổ `wsl -d Ubuntu` và để nguyên trong lúc làm việc; chạy `incus list` một lần để đánh thức Incus.

Tạo file `.env` ở gốc repo trên Windows:

```
MINICLOUD_DRIVER=incus
MINICLOUD_INCUS_URL=https://[::1]:8443
MINICLOUD_INCUS_CLIENT_CERT=secrets/client.crt
MINICLOUD_INCUS_CLIENT_KEY=secrets/client.key
MINICLOUD_INCUS_CA_CERT=secrets/server.crt
MINICLOUD_PUBLIC_DOMAIN=vexenhanh.id.vn
MINICLOUD_INGRESS_ROUTES_FILE=dev-routes/minicloud.yml
```

Phải dùng `[::1]` chứ không dùng `127.0.0.1`: WSL chỉ chuyển cổng của Incus sang Windows qua địa chỉ IPv6 loopback. Vì lý do đó, cổng proxy của instance cũng đi qua `[::1]`; control plane tự lấy địa chỉ này từ `MINICLOUD_INCUS_URL`.

Sau đó chạy `uv run uvicorn minicloud.main:create_app --factory --reload` như bình thường.

## Kiểm tra nhanh

1. Mở http://127.0.0.1:8000/docs, đăng ký rồi tạo một instance.
2. Chờ instance chạy. Lần đầu mất khoảng một phút để tải image; các lần sau chỉ vài giây.
3. Trong WSL, chạy `incus list --all-projects` để thấy instance nằm trong project `mc-…`.
4. Đọc tuyến trong `dev-routes/minicloud.yml`. Nếu trong container có web server ở cổng 80, mở địa chỉ `http://[::1]:<cổng>` ghi trong file đó sẽ thấy trang của nó.

## Dùng WSL làm laptop-1 tạm (staging trên Internet)

Phần này dựng y như laptop-1 thật, bằng chính các script trong `infra/node`. Cả nhóm vì vậy dùng được `https://cloud-api.vexenhanh.id.vn` trước khi có cụm 3 laptop.

Giữ WSL chạy nền. Máy tính cũng phải bật và không được ngủ:

```powershell
powershell -ExecutionPolicy Bypass -File infra\wsl\keep-wsl-alive.ps1
```

Cài các dịch vụ, chạy trong WSL với quyền root, thư mục repo trên ổ C:

```bash
cd /mnt/c/Code/DTDM
sudo ./infra/node/50-install-control-plane.sh vexenhanh.id.vn   # control plane + mã mời
sudo ./infra/node/60-install-ingress.sh                         # Traefik + cloudflared
sudo ./infra/node/61-setup-tunnel.sh minicloud-wsl vexenhanh.id.vn
```

Đặt tên tunnel là `minicloud-wsl`; laptop-1 thật sẽ dùng `minicloud`. Khi chạy script 61 trên laptop-1, `--overwrite-dns` tự chuyển các tên máy từ tunnel WSL sang tunnel của laptop-1.

Các lệnh hay dùng:

| Việc | Lệnh |
| --- | --- |
| Xem mã mời | `sudo grep INVITE /etc/minicloud/minicloud.env` |
| Cấp quyền admin | `sudo minicloud-admin make-admin <email>` |
| Cập nhật code sau khi sửa | chạy lại script 50 (tự sao lưu database rồi migrate) |
| Sao lưu ngay | `sudo minicloud-admin backup --dir /var/lib/minicloud/backups` |
| Đối chiếu ngay database với Incus | `POST /v1/admin/reconcile` với API key admin (worker vẫn tự làm mỗi phút) |
| Xem log | `journalctl -u minicloud-api -u traefik -u cloudflared -f` |
| Tắt hết | `wsl --terminate Ubuntu` (chạy trên Windows) |
| Gỡ tunnel thử | `cloudflared tunnel delete minicloud-wsl`, rồi xoá các bản ghi `cloud-api` và `*` trong dashboard Cloudflare |
