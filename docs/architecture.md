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
   1. Kiểm tra bridge của tenant có gắn ACL `minicloud-egress` (chặn cổng 25 chiều ra). Việc này chỉ gọi Incus ở lần tạo đầu tiên sau khi khởi động. Nếu lỗi, worker ghi log và vẫn tạo máy; lần tạo sau thử lại.
   2. Tạo Incus project kèm limits. Nếu project đã có, đọc config hiện tại, gộp limits vào rồi `PUT` lại; `PATCH` của Incus 6.0 coi các khoá `features.*` bị bỏ sót là tắt đi và báo lỗi. Profile `default` của project có NIC giới hạn băng thông mỗi chiều (`MINICLOUD_INCUS_NIC_LIMIT`, mặc định 20 Mbit/s).
   3. Hỏi danh sách node, rồi để `scheduler.choose_node` chọn laptop.
   4. Với container có URL công khai: cấp cổng và lưu ngay vào database trước khi gọi Incus.
   5. Gọi `POST /1.0/instances?project=…&target=…` kèm cloud-init chứa SSH key.
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

- **Giá:** container `s1.small` 20 credit/giờ, VM gấp 1,5 lần. Máy đang dừng chỉ trả tiền đĩa, 0,1 credit mỗi GiB mỗi giờ. Giá được đối chiếu với giá vốn và giá thị trường trong [bang-gia.md](bang-gia.md).
- **Công thức:** tiền = giá mỗi giờ × số giây ÷ 3600, tính bằng số nguyên millicredit nên không có sai số làm tròn.
- **Khi nào tính:** `metering.settle_instance` chạy trước mỗi lần đổi trạng thái, ở cả API lẫn worker, nên mỗi khoảng thời gian được tính đúng giá của nó. Ngoài ra worker chạy `run_metering_tick` mỗi 60 giây. Reconciler cũng tính tiền trạng thái cũ trước khi sửa theo Incus; vì không biết đúng lúc máy đổi, sai lệch tối đa là một lượt (60 giây).
- **Hết credit:** khi số dư ≤ 0, tổ chức bị khoá và mọi instance đang chạy được dừng. Admin nạp thêm credit thì tổ chức được mở khoá.
- **Sao kê tháng** (`services/statements.py`): tháng tính theo giờ Việt Nam (UTC+7). Một bản ghi usage thuộc tháng chứa thời điểm kết thúc của nó; bản ghi dài tối đa một lượt (60 giây), nên ranh giới tháng lệch không quá một phút. Số dư đầu kỳ = tổng credit nạp trước kỳ − tổng phí trước kỳ, nên số dư cuối kỳ của tháng hiện tại luôn bằng số dư của tổ chức (có test).
- **Đồng hồ chi phí:** `GET /v1/orgs/{id}/billing` trả thêm phí tạm tính của tháng, gồm cả phần chưa kịp ghi sổ từ lượt trước, mức tiêu mỗi giờ và số giờ còn đủ dùng. Console tự cộng dồn mỗi giây giữa hai lần hỏi API.
- **Ngân sách:** mỗi lượt tính tiền, worker so phí trong tháng với ngân sách của tổ chức và ghi `budget.alert` vào audit log khi lần đầu vượt 80% và 100%. Ngân sách chỉ cảnh báo, không dừng máy.
- **Điện năng trên hoá đơn:** mỗi bản ghi usage của máy đang chạy có thêm số Wh = phần công suất laptop chia cho máy đó × số giây ÷ 3600 (`instances.power_watts`, xem mục Green cloud). Sao kê cộng số Wh và quy ra gam CO₂ theo hệ số lưới điện (`MINICLOUD_GREEN_GRID_KG_CO2_PER_KWH`).

## Điện năng của từng máy (Green cloud)

Mỗi lượt 60 giây, sau khi reconciler đọc danh sách instance từ Incus, worker ước tính công suất (`services/green.py`, `green/energy.py`):

1. Lấy thời gian CPU mà mỗi instance đã dùng (`state.cpu.usage` của Incus, đọc chung trong lần gọi của reconciler), trừ cho số của lượt trước. Kết quả là số luồng CPU mà instance giữ bận trung bình.
2. Mức dùng của laptop u = tổng số luồng bận ÷ số luồng của laptop. Công suất laptop P = P_idle + (P_max − P_idle) × u, với P_idle và P_max của từng laptop trong `MINICLOUD_GREEN_NODE_WATTS`.
3. Chia P cho các instance trên laptop đó (`attribution.attribute_power`): P_idle chia theo số vCPU được cấp, phần còn lại chia theo thời gian CPU thật sự dùng. Một laptop đang thức mà chỉ chạy một máy thì máy đó gánh cả P_idle; gom máy về ít laptop hơn làm phần này giảm, đúng điều Green cloud nhắm tới.
4. Ghi kết quả vào `instances.power_watts`. Khi tính tiền, mỗi khoảng máy chạy được ghi thêm số Wh = W × giây ÷ 3600.

Laptop đang ngủ coi như 0 W. `GET /v1/green` cho mọi người dùng xem công suất ước tính của từng laptop và phần điện tiết kiệm nhờ laptop ngủ; console hiện thêm công suất và số Wh, gam CO₂ trong tháng của chính tổ chức.

Hạn chế: đây là ước tính theo mô hình tuyến tính, chưa phải số đo. Khi exporter (RAPL, pin) chạy trên cả 3 laptop, thay P ở bước 2 bằng số đo của laptop đó. Lượt đầu tiên sau khi control plane khởi động chưa có số của lượt trước, nên chỉ chia P_idle.

## Cho một laptop ngủ và đánh thức (Green cloud, màn 7)

Ba API cho admin, dùng khi planner (`GET /v1/admin/green/plan`) báo một laptop có thể ngủ:

1. `POST /v1/admin/nodes/{tên}/evacuate`: Incus chuyển mọi máy của laptop đó sang laptop khác (máy mới tạo đều có `cluster.evacuate=migrate`). Ngay sau đó control plane đối chiếu một lượt, nên database và tuyến Traefik theo node mới. API từ chối laptop ghim, và từ chối khi số laptop còn thức sẽ dưới mức giữ quorum (cụm 3 laptop chỉ cho 1 laptop ngủ).
2. Cho laptop đó ngủ hoặc tắt hẳn: việc của hệ điều hành, làm bằng tay hoặc bằng script trên laptop.
3. `POST /v1/admin/nodes/{tên}/wake`: gửi gói Wake-on-LAN tới MAC trong `MINICLOUD_GREEN_NODE_MACS`, qua địa chỉ broadcast `MINICLOUD_GREEN_WOL_BROADCAST`. Khi laptop vào lại cụm, `POST /v1/admin/nodes/{tên}/restore` nhận nó lại.

Mỗi bước được ghi audit (`node.evacuate`, `node.wake`, `node.restore`). Khi evacuate, Incus tự chọn laptop đích, không theo planner.

## Nhóm máy tự co giãn (autoscaling)

Một nhóm máy (`instance_groups`) là các container giống nhau, cùng cấu hình và cùng script khởi động, sau một địa chỉ HTTPS chung (`<tên nhóm>-<6 ký tự>.vexenhanh.id.vn`). Traefik chia request lần lượt cho các máy đang chạy của nhóm.

Mỗi lượt 60 giây, sau khi ghi mức dùng CPU của từng máy (`instances.cpu_util`, lấy từ thời gian CPU của Incus như phần điện năng), worker chạy `services/groups.autoscale`:

| Điều kiện | Làm gì |
| --- | --- |
| Ít máy hơn `min_size` | Tạo thêm cho đủ |
| CPU trung bình của các máy đang chạy trên 70%, chưa tới `max_size`, lần đổi trước đã qua 60 giây | Thêm một máy |
| CPU trung bình dưới 20% liên tục 3 phút, còn hơn `min_size` | Xoá máy mới nhất |
| Một máy bị lỗi | Xoá và tạo máy thay |
| Có máy đang được tạo | Chờ, không quyết định gì thêm |

Mỗi lần đổi số máy được ghi audit `group.scale`, kèm số máy trước, sau và mức CPU. Máy mới vẫn phải qua quota của project và tổ chức phải còn credit; tới quota thì nhóm thôi lớn, không báo lỗi. Script khởi động được ghi vào máy qua cloud-init (`write_files` rồi `runcmd`), dạng chuỗi JSON nên không chèn được lệnh cloud-init khác.

App mẫu để thử (`console/demo-app.sh`): mỗi request tốn khoảng 0,1 giây CPU và trang cho biết máy nào trả lời. Bắn tải bằng `infra/bench/load.py` (hoặc k6) để thấy request được chia sang máy mới.

## Cô lập giữa các tenant

| Lớp | Đã có | Còn thiếu |
| --- | --- | --- |
| API | Kiểm tra membership ở mọi truy vấn; người ngoài nhận 404; ghi audit; mã mời; giới hạn số lần đăng ký và số lần sai key theo IP | Đăng nhập OIDC có MFA |
| Compute | Incus project `restricted`, `limits.cpu` và `limits.memory` cho từng instance | Đo bằng thí nghiệm "Hàng xóm ồn ào" |
| Mạng | Dùng chung `incusbr0` có NAT; port isolation: các máy không gửi gói tin thẳng cho nhau; ACL `minicloud-egress` chặn cổng 25 chiều ra; mỗi instance tối đa 20 Mbit/s mỗi chiều | OVN: mỗi tenant một VPC (máy cùng tenant nói chuyện được với nhau), ACL làm security group |
| cloud-init | SSH key được kiểm tra chặt và ghi dạng chuỗi JSON | Metadata service có token |

ACL nằm trên bridge chứ không trên NIC của từng instance, vì Incus 6.0 không nhận `security.acls` trên NIC kiểu bridged. Bridge phải có thêm `security.acls.default.egress.action=allow` và `security.acls.default.ingress.action=allow`; thiếu hai khoá này thì bridge chặn mọi lưu lượng mà ACL không cho phép. Control plane tự đặt cả ba khoá, và giữ nguyên các khoá khác của bridge.

## Đưa app của khách ra Internet (đã làm cho container)

Theo [ADR-0005](adr/0005-ten-mien-va-cong-vao.md), mỗi instance có địa chỉ `https://<tên>-<6 ký tự>.vexenhanh.id.vn`, trùng với tên của instance trong Incus.

1. Lúc tạo container, worker cấp một cổng trong khoảng 20000–29999, duy nhất trong cả cụm để instance giữ nguyên cổng khi bị chuyển sang laptop khác. Worker gắn kèm một `proxy` device: nghe ở cổng đó trên laptop chứa container, chuyển tiếp tới cổng 80 của container. Project của tenant cần thêm `restricted.devices.proxy=allow`. Khách không gọi thẳng Incus, nên việc này không cho họ thêm quyền nào.
2. Control plane ghi file cấu hình động cho Traefik trên laptop-1, ánh xạ `Host(<tên>-<6 ký tự>.vexenhanh.id.vn)` tới `http://<IP LAN của laptop>:<cổng>`. Khi Green cloud chuyển instance sang laptop khác, file được ghi lại.
3. `cloudflared` chuyển mọi `*.vexenhanh.id.vn` tới Traefik ở cổng 8080. TLS kết thúc ở Cloudflare; đoạn từ Cloudflare về laptop-1 do tunnel mã hoá.
4. VM cần proxy device ở chế độ NAT kèm IP tĩnh, nên làm sau container. Về sau có thể thay bằng network forward của OVN.
