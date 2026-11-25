# Cổng vào public

Đã chốt ở [ADR-0005](../../docs/adr/0005-ten-mien-va-cong-vao.md): dùng Cloudflare Tunnel với tên miền `vexenhanh.id.vn` có sẵn. Chi phí 0 đồng, không cần IP public, không thuê VPS. Vai trò 2 phụ trách.

## Tên máy

| Tên | Dùng cho | Trên laptop-1 trỏ tới |
| --- | --- | --- |
| `cloud-api.vexenhanh.id.vn` | API của control plane | `http://localhost:8000` |
| `cloud.vexenhanh.id.vn` | Web console, hiện do control plane phục vụ ở `/console` | `http://localhost:8000` |
| `cloud-status.vexenhanh.id.vn` | Status page (Uptime Kuma) | `http://localhost:3001` |
| `<tên>-<6 ký tự>.vexenhanh.id.vn` | App của khách | Traefik `http://localhost:8080`, rồi tới laptop và cổng của instance |

Hai điều cần tránh:

- **Tên nhiều cấp** như `x.apps.vexenhanh.id.vn`: chứng chỉ miễn phí của Cloudflare chỉ phủ `vexenhanh.id.vn` và `*.vexenhanh.id.vn`.
- **Sửa bản ghi `@` và `www`:** đó là website vexenhanh đang chạy trên Vercel. Bản ghi wildcard `*` của cloud không ảnh hưởng tới chúng, vì DNS luôn ưu tiên bản ghi cụ thể hơn.

## Thử ngay từ máy dev

Dùng PowerShell trên Windows, mất khoảng 10 phút. Cài xong `cloudflared` thì mở cửa sổ PowerShell mới. Lệnh `login` mở trình duyệt để đăng nhập Cloudflare và chọn `vexenhanh.id.vn`.

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel login
cloudflared tunnel create minicloud-dev
cloudflared tunnel route dns minicloud-dev cloud-api.vexenhanh.id.vn
```

Chạy API ở một cửa sổ, tunnel ở cửa sổ khác:

```powershell
uv run uvicorn minicloud.main:create_app --factory --port 8000
```

```powershell
cloudflared tunnel run --url http://localhost:8000 minicloud-dev
```

Mở `https://cloud-api.vexenhanh.id.vn/docs` bằng điện thoại dùng 4G. Thử xong thì tắt tunnel (Ctrl+C), vì khi tunnel chạy thì ai cũng đăng ký được.

## Chạy thật trên laptop-1

1. Cài `cloudflared` từ kho apt của Cloudflare:

   ```bash
   sudo mkdir -p --mode=0755 /usr/share/keyrings
   curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
   echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
   sudo apt-get update && sudo apt-get install -y cloudflared
   ```

2. Tạo tunnel chính rồi trỏ các tên máy về nó. Tên nào đã trỏ về `minicloud-dev` thì thêm `--overwrite-dns`:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create minicloud
   cloudflared tunnel route dns --overwrite-dns minicloud cloud-api.vexenhanh.id.vn
   cloudflared tunnel route dns minicloud cloud.vexenhanh.id.vn
   cloudflared tunnel route dns minicloud cloud-status.vexenhanh.id.vn
   cloudflared tunnel route dns minicloud '*.vexenhanh.id.vn'
   ```

   Nếu lệnh cuối báo lỗi, vào dashboard tạo tay một bản ghi CNAME tên `*`, đích `<UUID>.cfargotunnel.com`, bật proxy (đám mây màu cam).

3. Cài tunnel thành dịch vụ chạy nền:
   1. Chép [cloudflared/config.yml.example](cloudflared/config.yml.example) thành `/etc/cloudflared/config.yml` và điền UUID (xem bằng `cloudflared tunnel list`).
   2. Chép file `~/.cloudflared/<UUID>.json` vào `/etc/cloudflared/`, đặt quyền 600.
   3. Chạy:

   ```bash
   sudo cloudflared service install
   sudo systemctl status cloudflared
   ```

4. Cài Traefik v3 (bản binary từ trang release chính thức, hoặc chạy bằng Docker), chép [traefik/traefik.yml](traefik/traefik.yml) vào `/etc/traefik/` và tạo thư mục `/etc/traefik/dynamic`. Trong `.env` của control plane đặt:

   ```
   MINICLOUD_PUBLIC_DOMAIN=vexenhanh.id.vn
   MINICLOUD_INGRESS_ROUTES_FILE=/etc/traefik/dynamic/minicloud.yml
   ```

   Từ đó, mỗi khi một container chạy, dừng hay bị xoá, control plane ghi lại file tuyến và Traefik tự nạp, không cần khởi động lại. Admin gỡ URL của một instance bằng `POST /v1/admin/instances/{id}/unpublish`.

## Kiểm tra

- Mở `https://cloud-api.vexenhanh.id.vn/healthz` bằng 4G, thấy `"status": "ok"`.
- `https://www.vexenhanh.id.vn` vẫn mở website như cũ.
- `cloudflared tunnel info minicloud` thấy kết nối từ laptop-1.

## An toàn khi dùng chung tên miền

App của khách và website vexenhanh nằm chung một "site" trong mắt trình duyệt (`id.vn` thuộc Public Suffix List). Vì vậy:

- **Cookie dùng chung:** cookie đặt với `Domain=vexenhanh.id.vn` bị gửi tới mọi app của khách. Website vexenhanh và portal chỉ dùng cookie không có thuộc tính `Domain`. Portal dùng cookie tiền tố `__Host-` hoặc giữ token trong bộ nhớ.
- **Một app xấu ảnh hưởng cả tên miền:** nếu một app bị báo cáo lừa đảo, Cloudflare có thể khoá cả tên miền và website vexenhanh sập theo. Chỉ mời người quen, có chính sách sử dụng (AUP), và gỡ tuyến của instance ngay khi có báo cáo.
- **Tách sau khi có điều kiện:** chuyển app của khách sang một tên miền riêng, giống `*.vercel.app` của Vercel. Chỉ cần đổi bản ghi wildcard và cấu hình Traefik.

## Dự phòng: VPS + WireGuard + Traefik

Dùng khi cần chuyển TCP thô, ví dụ cho khách SSH thẳng vào instance. Tunnel chỉ chuyển HTTP(S) cho người dùng phổ thông. Đổi lại phải trả tiền thuê VPS hằng tháng.

`/etc/wireguard/wg0.conf` trên VPS. AllowedIPs kèm mạng LAN của cụm để VPS tới được cả 3 laptop:

```ini
[Interface]
Address = 10.99.0.1/24
ListenPort = 51820
PrivateKey = <khoá riêng của VPS>

[Peer]
PublicKey = <khoá công khai của laptop-1>
AllowedIPs = 10.99.0.11/32, 192.168.1.0/24
```

`/etc/wireguard/wg0.conf` trên laptop-1. `PersistentKeepalive` giữ đường hầm sống khi laptop nằm sau CGNAT:

```ini
[Interface]
Address = 10.99.0.11/24
PrivateKey = <khoá riêng của laptop-1>

[Peer]
PublicKey = <khoá công khai của VPS>
Endpoint = <IP public của VPS>:51820
AllowedIPs = 10.99.0.1/32
PersistentKeepalive = 25
```

Trên laptop-1, bật chuyển tiếp gói (`net.ipv4.ip_forward=1`) và NAT từ `10.99.0.0/24` ra cổng mạng dây. Traefik trên VPS lấy chứng chỉ Let's Encrypt qua thử thách DNS-01 bằng API token của Cloudflare (zone đã ở Cloudflare). Các bản ghi DNS khi đó để chế độ "DNS only", trỏ về IP của VPS.
