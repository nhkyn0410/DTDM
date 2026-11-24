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
