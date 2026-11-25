# Kế hoạch tuần 1–2: tới mốc 1

Mục tiêu của mốc 1 (hết tuần 2): kiến trúc được chốt và mạng public chạy được. Tick vào ô khi xong; vướng ở đâu thì ghi ngay dưới việc đó.

Ràng buộc (2026-11-25): nhóm không mua thêm phần cứng (RAM, ổ đĩa, ổ cắm đo điện), nên không đo được điện lúc cắm sạc.

## Mốc 1: điều kiện để qua

Ngày 2026-11-25, hai điều kiện (tạo máy thật qua API; mở app qua HTTPS từ Internet) đã đạt trên laptop-1 tạm chạy trong WSL. Các ô dưới đây vẫn để trống vì phải đạt lại trên cụm 3 laptop.

- [ ] 3 laptop ở trạng thái Online trong cụm Incus (`incus cluster list`)
  - Cụm 3 node cần ít nhất 2 laptop chạy cùng lúc thì mới quản lý được (đã thử trên cụm ảo). Vì vậy chỗ đặt cụm phải giữ thường trực ít nhất 2 máy.
- [ ] Control plane tạo, dừng và xoá được instance thật qua API (smoke test bên dưới)
- [ ] Mở được một app qua tên miền HTTPS bằng điện thoại dùng 4G
- [ ] Cả nhóm duyệt ADR-0001 đến ADR-0006 và threat model bản 1
- [ ] Có số đo P_idle và P_max của cả 3 laptop trong `docs/hardware.md`
  - 2026-11-25: đã có số lúc rút sạc của cả 3, nhưng laptop-1 và laptop-3 đo lúc máy đang dùng. Còn thiếu số lúc cắm sạc.

## Vai trò 1: trưởng nhóm, hạ tầng

- [x] Ghi cấu hình 3 laptop vào `docs/hardware.md` (2026-11-25, theo [kiem-tra-laptop.md](../kiem-tra-laptop.md))
  - laptop-2 là máy ASUS, laptop-3 là máy Lenovo. Báo cáo của máy Lenovo tự ghi là laptop-1; trưởng nhóm cần chốt lại số của từng máy.
- [ ] Cài Ubuntu Server 24.04 lên 3 laptop (nếu được, chừa một phân vùng trống cho ZFS)
  - [ADR-0006](../adr/0006-node-trong-wsl2.md) đề xuất thay việc này bằng chạy node trong WSL2 với pool btrfs, và gắn SSD di động vào laptop-1. Chờ cả nhóm duyệt.
- [ ] Buổi thử 2 laptop cùng mạng theo mục "Thử trước khi chốt" của ADR-0006
- [ ] Đặt IP cố định bằng DHCP reservation trên router
- [ ] Chạy các script trong `infra/node` theo [infra/README.md](../../infra/README.md)
  - Đã chạy trọn trên cụm ảo 3 VM (`infra/lab/lab.sh up`) ngày 2026-11-25; còn chạy trên laptop thật.
- [ ] Mỗi laptop tạo thử được một container và một VM
- [ ] Họp đầu tuần, chốt các câu hỏi còn mở ở mục 14 của đề cương

## Vai trò 2: mạng và bảo mật

- [x] Tên miền: dùng `vexenhanh.id.vn` có sẵn, DNS đã nằm trên Cloudflare ([ADR-0005](../adr/0005-ten-mien-va-cong-vao.md))
- [x] Thử Cloudflare Tunnel từ máy dev: `https://cloud-api.vexenhanh.id.vn` chạy qua tunnel `minicloud-wsl` (laptop-1 tạm trên WSL)
- [ ] Khi cụm đã chạy: chuyển tunnel sang laptop-1, thêm bản ghi wildcard cho app của khách
- [x] Đưa một container thử nghiệm ra Internet qua tên miền con HTTPS (`hello-…vexenhanh.id.vn`, 2026-11-25)
- [ ] Rà soát và bổ sung [threat model](../threat-model.md); lên kế hoạch OVN cho tuần 3–5

## Vai trò 3: control plane lõi

- [ ] Đọc toàn bộ `src/minicloud`, chạy được `uv run pytest`
- [ ] Nối `IncusDriver` với cụm thật và làm smoke test bên dưới; sửa lỗi phát hiện được
- [x] Thêm Alembic thay cho `create_all` (2026-11-25; database của staging đã được nhận vào mà không mất dữ liệu)
- [x] Thêm reconciler: định kỳ đối chiếu trạng thái trong database với Incus (2026-11-25; đã thử trên staging, xem [kiến trúc](../architecture.md#khi-database-và-incus-lệch-nhau-reconciler))
- [x] Sao lưu database hằng ngày bằng systemd timer, giữ 14 bản (2026-11-25)
- [ ] Chép bản sao lưu ra ngoài laptop-1 (ví dụ sang laptop-2 bằng `rsync` theo lịch)
- [ ] Chuyển staging từ pool `dir` sang btrfs: pool `dir` trong WSL không giới hạn được dung lượng ổ của khách (đã thử, xem ADR-0006)

## Vai trò 4: IAM và billing

- [ ] Dựng Keycloak bằng docker compose; thiết kế đăng nhập OIDC cho portal, giữ API key cho CLI
- [ ] Cho phép tạo nhiều API key có tên và thu hồi key
- [ ] Tính bảng giá từ chi phí thật (khấu hao laptop, tiền điện theo số đo của vai trò 6, tiền mạng) ra giá vốn mỗi vCPU-giờ
- [ ] Thiết kế hoá đơn tháng

## Vai trò 5: portal và khách hàng

- [x] Dựng khung web console: bản tối thiểu đã chạy ở `/console` (2026-11-25, xem [portal/README.md](../../portal/README.md)); quyết định làm tiếp trên nền này hay làm lại bằng Next.js
- [ ] Sinh client TypeScript từ `/openapi.json`
- [ ] Vẽ wireframe các màn: đăng ký, danh sách instance, tạo instance, chi tiết instance và hoá đơn
- [ ] Viết trang hướng dẫn đầu tiên cho người dùng thử

## Vai trò 6: Green cloud và giám sát

- [ ] Cài exporter (`infra/node/40-install-exporter.sh`) trên 3 laptop; kiểm tra RAPL có số
- [ ] Đo P_idle và P_max của từng laptop bằng `stress-ng`, cả lúc cắm sạc lẫn lúc rút sạc; ghi vào `docs/hardware.md` và `.env`
  - Máy còn chạy Windows thì đo lúc rút sạc bằng `infra/check/check-laptop.ps1 -MeasurePower`. Không có ổ cắm đo điện nên bỏ số lúc cắm sạc; mô hình dùng số lúc rút sạc (hạn chế ghi trong `docs/hardware.md`).
- [ ] Dựng Prometheus và Grafana (`docker compose -f deploy/docker-compose.dev.yml --profile monitoring up -d`); làm dashboard công suất 3 laptop
- [ ] Thử suspend và Wake-on-LAN bằng tay trên từng laptop; ghi lại thời gian ngủ và thời gian thức dậy
  - Trên Windows, cả 3 máy chỉ có Modern Standby. Nên thử thêm cách tắt hẳn rồi đánh thức bằng Wake-on-LAN: driver của laptop-1 và laptop-3 đã bật Shutdown Wake-On-Lan.

## Smoke test với cụm thật (vai trò 1 và 3)

Đã chạy trên Incus một node trong WSL2 ngày 2026-11-25 ([cách dựng](../dev-incus-wsl.md)). Lần chạy đầu tìm và sửa hai lỗi, và xác nhận URL công khai tới được cổng 80 của container. Lần thử reconciler trên staging tìm thêm hai lỗi, đều đã sửa và có test:

- Một tổ chức không tạo được máy thứ hai, vì `PATCH` project của Incus 6.0 tắt các khoá `features.*`.
- Khi không còn tuyến nào, Traefik từ chối file tuyến rỗng và tiếp tục chuyển request tới app đã dừng (502).

Phần riêng của cụm (bước 7 và 8) đã đạt trên cụm ảo 3 VM trong WSL ngày 2026-11-25. Cụm ảo dựng bằng chính các script 00 đến 30, với Incus 7.6 ([cách dựng](../dev-incus-wsl.md#cụm-ảo-3-node)). Lần thử này tìm thêm ba lỗi, đều đã sửa và có test:

- Các script `infra/node` không có quyền chạy trong git.
- Evacuate chỉ dừng instance nằm trên ổ cục bộ. Control plane giờ đặt `cluster.evacuate=migrate` cho mỗi instance mới.
- `move_instance` không chờ IP ở laptop mới.

Ghi nhận thêm: khi evacuate, Incus tự chọn laptop đích và có thể vượt mức RAM mà control plane cho phép.

Vẫn phải chạy lại cả 8 bước trên 3 laptop. Sửa script dựng cụm thì chạy `sudo bash infra/lab/smoke.sh all` trên cụm ảo trước.

Chạy trên laptop-1 sau khi đã có cụm và chạy `infra/node/30-trust-control-plane.sh`:

1. Tạo file `.env` từ `.env.example`, đặt `MINICLOUD_DRIVER=incus` và đường dẫn tới ba chứng chỉ.
2. Chạy `uv run uvicorn minicloud.main:create_app --factory --host 0.0.0.0 --port 8000`.
3. Từ máy khác, mở `http://<IP-laptop-1>:8000/docs`, đăng ký rồi tạo một instance.
4. Theo dõi operation tới khi `succeeded`.
5. Kiểm tra trên laptop-1:
   - `incus list --all-projects` có instance mới.
   - `incus project show mc-…` có `restricted: "true"` và đủ `limits.*`.
6. Dừng, chạy lại rồi xoá instance qua API. Sau mỗi bước, so trạng thái trên API với `incus list`.
7. Tạo 3 instance trong cùng một tổ chức và kiểm tra chúng được dàn lên 3 laptop.
8. Chạy `incus cluster evacuate laptop-2`. Trong vòng một phút, reconciler phải ghi node mới cho các instance bị chuyển (`GET /v1/instances/{id}`), và URL công khai vẫn mở được. Sau đó chạy `incus cluster restore laptop-2`.

Nếu gặp `CERTIFICATE_VERIFY_FAILED`, tạm đặt `MINICLOUD_INCUS_VERIFY=false` và bỏ trống `MINICLOUD_INCUS_CA_CERT` để thử tiếp, rồi báo vai trò 2 xử lý.
