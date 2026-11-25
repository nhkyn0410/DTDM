# Kiến trúc minicloud (v0.1)

Sơ đồ tổng thể nằm ở mục 5 của đề cương. Tài liệu này ghi chi tiết để lập trình.

## Thành phần chạy ở đâu

| Máy | Chạy gì |
| --- | --- |
| laptop-1 (node ghim, không ngủ) | Thành viên cụm Incus, control plane (API + worker), PostgreSQL, Prometheus, Grafana, exporter điện năng, `cloudflared`, Traefik |
| laptop-2, laptop-3 | Thành viên cụm Incus, exporter điện năng |
| Cloudflare (gói miễn phí) | DNS của `vexenhanh.id.vn`, TLS, Cloudflare Tunnel về laptop-1 |
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
   1. Tạo Incus project kèm limits. Nếu project đã có, đọc config hiện tại, gộp limits vào rồi `PUT` lại; `PATCH` của Incus 6.0 coi các khoá `features.*` bị bỏ sót là tắt đi và báo lỗi.
   2. Hỏi danh sách node, rồi để `scheduler.choose_node` chọn laptop.
   3. Với container có URL công khai: cấp cổng và lưu ngay vào database trước khi gọi Incus.
   4. Gọi `POST /1.0/instances?project=…&target=…` kèm cloud-init chứa SSH key.
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

## Khi database và Incus lệch nhau (reconciler)

Database chỉ ghi lại những gì worker làm. Instance vẫn có thể đổi ngoài minicloud: ai đó chạy `incus stop` trên laptop, control plane chết giữa một operation, Green cloud sơ tán một laptop, hoặc database được khôi phục từ bản sao lưu cũ. Vì vậy mỗi 60 giây worker chạy `reconciler.reconcile`, rồi mới tính tiền và ghi lại tuyến Traefik.

| Incus báo | Database đang ghi | Reconciler làm |
| --- | --- | --- |
| Running hoặc Stopped | Trạng thái khác | Tính tiền trạng thái cũ tới thời điểm này, rồi ghi theo Incus |
| Running hoặc Stopped | `deleting` (lệnh xoá bị ngắt) | Tạo lại operation xoá |
| Running, tổ chức đang bị khoá | Bất kỳ | Chuyển `stopping` và tạo operation dừng |
| Không còn instance | `deleting` | Ghi `deleted` |
| Không còn instance | Trạng thái khác | Ghi `error` và ngừng tính tiền |
| Node hoặc IP khác | Bất kỳ | Ghi node và IP mới; tuyến Traefik đi theo node mới |
| Frozen, Error, laptop offline | Bất kỳ | Chờ lượt sau |

Mỗi lần sửa đều ghi audit `instance.reconcile`, nên khách thấy lý do trong audit log của tổ chức. Instance nằm trong project `mc-*` của Incus mà database không biết chỉ được báo trong danh sách orphans, không bị xoá.

Ba quy tắc tránh làm hại:

- Incus không trả lời thì không sửa gì.
- Instance đang có operation, hoặc có dòng database đổi sau lúc đọc Incus, được để lại cho lượt sau.
- Từ 3 instance trở lên, và quá nửa số instance, cùng biến mất một lúc thì giữ nguyên và ghi log lỗi. Trường hợp này giống một câu trả lời sai (nhầm server, nhầm quyền) hơn là bị xoá thật.

Khi khởi động, worker đánh dấu `failed` mọi operation còn `running` từ lần chạy trước. Chỉ có một worker, nên operation đó chắc chắn đã bị ngắt; reconciler sau đó đặt trạng thái instance theo Incus. Cổng công khai được giữ trước khi gọi Incus, nên instance tạo dở vẫn giữ cổng của nó và có lại URL khi chạy. Admin chạy được một lượt ngay bằng `POST /v1/admin/reconcile`.

Đã thử trên staging (2026-11-25): `incus stop` rồi `incus start` ngoài minicloud được phát hiện trong vòng một phút. Kill API bằng `SIGKILL` giữa lúc tạo container thì sau khi tự khởi động lại, operation thành `failed`, instance thành `running` với đúng node, IP và URL.

## Schema database và sao lưu

- Schema do Alembic quản lý (`src/minicloud/migrations/`). App tự nâng database lên bản mới nhất khi khởi động. Database tạo bằng `create_all` từ trước khi có migration được nhận là bản `0001` nếu đủ bảng và cột; nếu thiếu, app dừng lại và báo rõ thiếu gì.
- `minicloud backup` chép database bằng online backup API của SQLite, nên an toàn cả khi API đang ghi. Bản chép được kiểm tra bằng `PRAGMA integrity_check`, đặt quyền 600, và chỉ 14 bản mới nhất được giữ.
- Trên laptop-1, systemd timer sao lưu lúc 03:00 giờ Việt Nam. Script 50 dừng API, sao lưu, rồi mới migrate. Các bản sao lưu vẫn nằm trên chính laptop-1, nên chưa chống được hỏng ổ (dòng 11 của threat model).

## Tính tiền

- **Giá:** container `s1.small` 20 credit/giờ, VM gấp 1,5 lần. Máy đang dừng chỉ trả tiền đĩa, 0,1 credit mỗi GiB mỗi giờ.
- **Công thức:** tiền = giá mỗi giờ × số giây ÷ 3600, tính bằng số nguyên millicredit nên không có sai số làm tròn.
- **Khi nào tính:** `metering.settle_instance` chạy trước mỗi lần đổi trạng thái, ở cả API lẫn worker, nên mỗi khoảng thời gian được tính đúng giá của nó. Ngoài ra worker chạy `run_metering_tick` mỗi 60 giây. Reconciler cũng tính tiền trạng thái cũ trước khi sửa theo Incus; vì không biết đúng lúc máy đổi, sai lệch tối đa là một lượt (60 giây).
- **Hết credit:** khi số dư ≤ 0, tổ chức bị khoá và mọi instance đang chạy được dừng. Admin nạp thêm credit thì tổ chức được mở khoá.

## Cô lập giữa các tenant

| Lớp | Đã có | Còn thiếu |
| --- | --- | --- |
| API | Kiểm tra membership ở mọi truy vấn; người ngoài nhận 404; ghi audit | Mã mời, rate limit |
| Compute | Incus project `restricted`, `limits.cpu` và `limits.memory` cho từng instance | Đo bằng thí nghiệm "Hàng xóm ồn ào" |
| Mạng | Dùng chung `incusbr0` có NAT | OVN: mỗi tenant một VPC, ACL làm security group |
| cloud-init | SSH key được kiểm tra chặt và ghi dạng chuỗi JSON | Metadata service có token |

## Đưa app của khách ra Internet (đã làm cho container)

Theo [ADR-0005](adr/0005-ten-mien-va-cong-vao.md), mỗi instance có địa chỉ `https://<tên>-<6 ký tự>.vexenhanh.id.vn`, trùng với tên của instance trong Incus.

1. Lúc tạo container, worker cấp một cổng trong khoảng 20000–29999, duy nhất trong cả cụm để instance giữ nguyên cổng khi bị chuyển sang laptop khác. Worker gắn kèm một `proxy` device: nghe ở cổng đó trên laptop chứa container, chuyển tiếp tới cổng 80 của container. Project của tenant cần thêm `restricted.devices.proxy=allow`. Khách không gọi thẳng Incus, nên việc này không cho họ thêm quyền nào.
2. Control plane ghi file cấu hình động cho Traefik trên laptop-1, ánh xạ `Host(<tên>-<6 ký tự>.vexenhanh.id.vn)` tới `http://<IP LAN của laptop>:<cổng>`. Khi Green cloud chuyển instance sang laptop khác, file được ghi lại.
3. `cloudflared` chuyển mọi `*.vexenhanh.id.vn` tới Traefik ở cổng 8080. TLS kết thúc ở Cloudflare; đoạn từ Cloudflare về laptop-1 do tunnel mã hoá.
4. VM cần proxy device ở chế độ NAT kèm IP tĩnh, nên làm sau container. Về sau có thể thay bằng network forward của OVN.
