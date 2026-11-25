# Dựng cụm 3 laptop

Các script trong `infra/node/` chưa chạy trên laptop thật. Chạy lần lượt, đọc kỹ thông báo của từng bước, và ghi lại lỗi gặp phải vào `docs/plan/tuan-1-2.md`.

## Chuẩn bị

- USB cài Ubuntu Server 24.04 LTS.
- Trong BIOS của mỗi laptop: bật Virtualization (VT-x hoặc AMD-V) và Wake-on-LAN nếu có.
- Một switch và 3 dây mạng. Nếu laptop không có cổng LAN thì dùng adapter USB-LAN, hoặc tạm dùng Wi-Fi.
- Trên router: giữ IP cố định cho 3 laptop. Ví dụ dưới đây dùng 192.168.1.11 đến 192.168.1.13.

## Thứ tự chạy

Trên cả 3 laptop, sau khi cài Ubuntu và `git clone` repo này:

```bash
cd infra/node
chmod +x *.sh
sudo ./00-prepare-laptop.sh laptop-1        # laptop-2, laptop-3 trên hai máy kia
sudo ./10-install-incus.sh
```

Trên laptop-1, khởi tạo cụm. Có phân vùng trống thì thêm `POOL_SOURCE=/dev/...`:

```bash
sudo ./20-bootstrap-cluster.sh laptop-1 192.168.1.11
sudo incus cluster add laptop-2             # in ra token cho laptop-2
sudo incus cluster add laptop-3
```

Trên laptop-2 và laptop-3, dán token vừa lấy:

```bash
sudo ./21-join-cluster.sh laptop-2 192.168.1.12 <token-cho-laptop-2>
```

Trên laptop-1, cấp quyền cho control plane:

```bash
sudo ./infra/node/30-trust-control-plane.sh
```

Trên cả 3 laptop, cài exporter đo điện:

```bash
sudo ./infra/node/40-install-exporter.sh
```

Trên laptop-1, cài control plane, Traefik và Cloudflare Tunnel thành dịch vụ chạy nền. Các script này đã chạy thật trên WSL (laptop-1 tạm, xem [docs/dev-incus-wsl.md](../docs/dev-incus-wsl.md)). Lệnh 61 in ra một link để đăng nhập Cloudflare.

```bash
sudo ./infra/node/50-install-control-plane.sh vexenhanh.id.vn   # in ra mã mời đăng ký
sudo ./infra/node/60-install-ingress.sh
sudo ./infra/node/61-setup-tunnel.sh minicloud vexenhanh.id.vn
sudo minicloud-admin make-admin <email-của-bạn>                  # sau khi đăng ký
```

Muốn cập nhật code, `git pull` rồi chạy lại script 50; dữ liệu và cấu hình được giữ nguyên. Mỗi lần chạy, script dừng API, sao lưu database, nâng schema (Alembic), rồi bật lại API. Nó cũng cài timer sao lưu hằng ngày lúc 03:00 giờ Việt Nam.

## Sao lưu và khôi phục database

```bash
sudo ls -l /var/lib/minicloud/backups                          # 14 bản mới nhất
systemctl list-timers minicloud-backup.timer                   # lần sao lưu tới
sudo minicloud-admin backup --dir /var/lib/minicloud/backups   # sao lưu ngay
```

Khôi phục một bản:

```bash
sudo systemctl stop minicloud-api
sudo install -o minicloud -g minicloud -m 600 \
  /var/lib/minicloud/backups/minicloud-<thời-điểm>.db /var/lib/minicloud/minicloud.db
sudo systemctl start minicloud-api
```

Trong vòng một phút sau khi API chạy lại, reconciler đối chiếu với Incus. Instance bị xoá sau thời điểm sao lưu chuyển sang `error`. Instance tạo sau thời điểm đó hiện trong mục `orphans` của `POST /v1/admin/reconcile`, chờ admin xử lý. Các bản sao lưu nằm trên chính laptop-1: hãy chép định kỳ sang máy khác.

## Kiểm tra

```bash
incus cluster list                                  # 3 node, trạng thái ONLINE
incus launch images:ubuntu/24.04 thu-ct --target laptop-2
incus launch images:ubuntu/24.04 thu-vm --vm --target laptop-3
incus list
incus delete -f thu-ct thu-vm
```

Sau đó làm smoke test với control plane theo [kế hoạch tuần 1–2](../docs/plan/tuan-1-2.md).

## Lỗi hay gặp

| Triệu chứng | Nguyên nhân thường gặp |
| --- | --- |
| Gập máy thì node rớt khỏi cụm | Chưa chạy script 00, hoặc cài bản Ubuntu Desktop (có chế độ tiết kiệm điện riêng) |
| Script báo `$'\r': command not found` | File bị đổi xuống dòng sang CRLF trên Windows; clone lại repo (`.gitattributes` giữ LF) |
| Không tạo được VM | Chưa bật Virtualization trong BIOS (`/dev/kvm` không có) |
| Join cụm báo lỗi chứng chỉ hoặc token | Token đã dùng hoặc hết hạn: tạo token mới bằng `incus cluster add` |
| Đĩa chậm | Pool ZFS đang dùng file loop; chuyển sang phân vùng riêng khi cài lại được |

Cổng vào public (VPS, WireGuard, Traefik) xem ở [edge/README.md](edge/README.md).
