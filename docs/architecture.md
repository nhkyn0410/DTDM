# Kiến trúc minicloud (v0.1)

Sơ đồ tổng thể nằm ở mục 5 của đề cương. Tài liệu này ghi chi tiết để lập trình.

## Thành phần chạy ở đâu

| Máy | Chạy gì |
| --- | --- |
| laptop-1 (node ghim, không ngủ) | Thành viên cụm Incus, control plane (API + worker), PostgreSQL, Prometheus, Grafana, exporter điện năng |
| laptop-2, laptop-3 | Thành viên cụm Incus, exporter điện năng |
| VPS nhỏ (hoặc Cloudflare Tunnel) | WireGuard về laptop-1, Traefik, chứng chỉ TLS wildcard |
| Máy của thành viên | Phát triển với driver giả lập, không cần Incus |

## Mô hình tài nguyên

| Control plane | Ánh xạ sang Incus | Ghi chú |
| --- | --- | --- |
| Organization (tenant) | (không có) | Giữ số dư credit (đơn vị millicredit), có thể bị khoá |
| Membership | (không có) | Vai trò owner, member, viewer |
| Project | Incus project `mc-<id>` | `restricted=true`, `limits.*` bằng quota |
| Instance | Instance `<tên>-<6 ký tự hex>` trong project đó | Container hoặc VM |
| Operation | Operation của Incus | Mọi thay đổi trạng thái đều đi qua đây |
| UsageRecord, LedgerEntry | (không có) | Tiền ra (sử dụng) và tiền vào (dùng thử, nạp) |
| AuditLog | (không có) | Ghi cả các lần bị từ chối |

## Luồng tạo một instance

1. Khách gọi `POST /v1/projects/{id}/instances` với API key.
2. API kiểm tra lần lượt:
   - Khách có phải thành viên với vai trò từ member trở lên không. Người ngoài nhận 404 và lần thử bị ghi audit.
   - Tổ chức còn credit không.
   - Flavor và image có hợp lệ không.
   - Quota của project còn đủ không.
3. API ghi `Instance(pending)` và `Operation(pending)`, rồi trả 202 ngay.
4. Worker lấy operation và làm tiếp:
   1. Tạo hoặc cập nhật Incus project kèm limits.
   2. Hỏi danh sách node, rồi để `scheduler.choose_node` chọn laptop.
   3. Gọi `POST /1.0/instances?project=…&target=…` kèm cloud-init chứa SSH key.
5. Worker chờ Incus làm xong, ghi node và IP, chuyển trạng thái sang `running` và bắt đầu tính tiền.

## Trạng thái của instance

| Từ | Hành động | Trạng thái trung gian | Kết quả |
| --- | --- | --- | --- |
| (mới) | create | pending → provisioning | running |
| running | stop | stopping | stopped |
| stopped, error | start | starting | running |
| running, stopped, error | delete | deleting | deleted |
| bất kỳ thao tác nào lỗi | (không có) | (không có) | error, kèm thông báo lỗi trong operation |

Mỗi instance chỉ có một operation chạy cùng lúc; gửi thêm sẽ nhận 409.

## Tính tiền

- **Giá:** container `s1.small` 20 credit/giờ, VM gấp 1,5 lần. Máy đang dừng chỉ trả tiền đĩa, 0,1 credit mỗi GiB mỗi giờ.
- **Công thức:** tiền = giá mỗi giờ × số giây ÷ 3600, tính bằng số nguyên millicredit nên không có sai số làm tròn.
- **Khi nào tính:** `metering.settle_instance` chạy trước mỗi lần đổi trạng thái, ở cả API lẫn worker, nên mỗi khoảng thời gian được tính đúng giá của nó. Ngoài ra worker chạy `run_metering_tick` mỗi 60 giây.
- **Hết credit:** khi số dư ≤ 0, tổ chức bị khoá và mọi instance đang chạy được dừng. Admin nạp thêm credit thì tổ chức được mở khoá.

## Cô lập giữa các tenant

| Lớp | Đã có | Còn thiếu |
| --- | --- | --- |
| API | Kiểm tra membership ở mọi truy vấn; người ngoài nhận 404; ghi audit | Mã mời, rate limit |
| Compute | Incus project `restricted`, `limits.cpu` và `limits.memory` cho từng instance | Đo bằng thí nghiệm "Hàng xóm ồn ào" |
| Mạng | Dùng chung `incusbr0` có NAT | OVN: mỗi tenant một VPC, ACL làm security group |
| cloud-init | SSH key được kiểm tra chặt và ghi dạng chuỗi JSON | Metadata service có token |

## Đưa app của khách ra Internet (dự kiến cho tuần 3–5)

1. Instance cần URL công khai được gắn một `proxy` device trên node của nó: nghe ở một cổng của node, chuyển tiếp tới cổng 80 của instance.
2. laptop-1 định tuyến mạng LAN của cụm qua WireGuard, nên VPS tới được mọi laptop.
3. Traefik trên VPS ánh xạ `<instance>.apps.<tên-miền>` tới `<IP-LAN-của-node>:<cổng>`; control plane sinh file cấu hình động cho Traefik.
4. Về sau thay bằng network forward hoặc load balancer của OVN.
