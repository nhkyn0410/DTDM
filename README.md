# minicloud

Nền tảng điện toán đám mây đa người thuê chạy trên cụm Incus gồm 3 laptop. Đây là đồ án môn Điện toán đám mây: nhóm 6 người đóng vai nhà cung cấp cloud, cho 10–20 người dùng thật thuê tài nguyên, và lấy Green cloud làm điểm nhấn.

Đề cương: [Đề cương đồ án – Nền tảng cloud đa người thuê](https://claude.ai/artifact/BmrqLGqGmD5cdaFGFSccQE). Link chỉ mở được sau khi trưởng nhóm chia sẻ.

## Trạng thái (v0.1, tuần 1)

| Phần | Trạng thái |
| --- | --- |
| Control plane: đăng ký (có mã mời), tổ chức, project, quota, API key, audit log | Chạy được, có test |
| Web console tối thiểu ở `/console` (https://cloud.vexenhanh.id.vn) | Chạy được, đã thử trên trình duyệt ở cả cỡ máy tính lẫn điện thoại; xem [portal/README.md](portal/README.md) |
| Laptop-1 tạm trên WSL: control plane, Traefik, cloudflared chạy thành dịch vụ (script 50, 60, 61) | Đang chạy trên máy dev của trưởng nhóm tại `https://cloud-api.vexenhanh.id.vn` (đăng ký cần mã mời); đã thử trọn chuỗi tới trang web trong container. Xem [docs/dev-incus-wsl.md](docs/dev-incus-wsl.md) |
| Vòng đời instance qua operation bất đồng bộ, scheduler spread/binpack | Chạy được với cụm giả lập |
| Metering theo giây, credit, tự khoá khi hết credit; không tính tiền máy đang chạy khi control plane ngừng quá 5 phút (laptop tắt hoặc ngủ) | Chạy được, có test. Đo trên staging: sổ ghi liền mạch (32 máy, 98.217 giây); đã thử tắt control plane 7 phút |
| Sao kê tháng (CSV, in PDF), đồng hồ chi phí, ngân sách có cảnh báo, bảng giá công khai, giá vốn so với thị trường ([docs/bang-gia.md](docs/bang-gia.md)) | Chạy được, có test |
| Chạy lệnh trong máy từ console (`POST /v1/instances/{id}/exec`), vì SSH từ Internet không tới được máy của khách | Chạy được, có test; cách gọi exec của Incus 6.0 đã thử trên staging |
| Nhóm máy tự co giãn: thêm hoặc bớt máy theo CPU, Traefik chia tải qua một địa chỉ chung (`/v1/projects/{id}/groups`) | Chạy được, có test. Thử trên staging: 1 → 3 máy trong 149 giây khi bắn tải, tự về 1 máy khi hết tải |
| Triển khai ứng dụng từ file zip (lớp PaaS): tự nhận ra web tĩnh, Node.js, Python (Flask, FastAPI, Django), PHP hoặc Procfile; buildpack cài và chạy ứng dụng ngay trong container của khách, sau địa chỉ HTTPS của container (`/v1/projects/{id}/apps`, `src/minicloud/buildpack.py`, ứng dụng mẫu trong `examples/apps`) | Chạy được, có test. Thử trên staging với 6 ứng dụng mẫu: chạy sau 40–130 giây |
| Green cloud: API admin dọn laptop (evacuate), đánh thức bằng Wake-on-LAN, nhận lại (restore), có rào chống mất quorum | Chạy được, có test; chưa thử trên cụm thật |
| Giám sát hai phía: khách thấy CPU từng máy, W và Wh. Admin có mục Nhà cung cấp trên console: doanh thu mô phỏng, công suất cụm, mức cấp phát từng laptop, mọi khách hàng và máy của họ, số liệu chạy thử, 50 sự kiện gần nhất của cả nền tảng; nạp credit và gỡ địa chỉ công khai bằng một nút (`/v1/admin/overview`, `orgs`, `instances`, `audit`, `stats`) | Chạy được, có test |
| Cách ly mạng: máy của khách không gửi gói tin thẳng cho nhau (`security.port_isolation`) | Chạy được, có test, đã thử trên staging |
| Script thí nghiệm trong `infra/bench`: thời gian cấp phát, mật độ, hàng xóm ồn ào, overcommit, bắn tải, khôi phục control plane | Đã đo trên staging: container p50 6,7 giây, p95 7,4 giây; một node chứa 200 container nghỉ với 8,2 GB RAM (sau khi nâng giới hạn inotify của kernel); giới hạn CPU giữ tenant B không chậm đi (bỏ giới hạn thì chậm 23%); overcommit 4:1 còn một phần tư CPU mỗi khách; API bị kill tự chạy lại sau khoảng 6 giây mà app của khách không lỗi request nào. Xem [infra/bench/README.md](infra/bench/README.md) |
| Chặn truy cập chéo tenant (trả 404, ghi audit) | Chạy được, có test |
| Driver Incus (REST API qua TLS client cert) | Đã chạy trên Incus 6.0 một node trong WSL2, và trên cụm ảo 3 VM với Incus 7.6 (dàn 3 node, evacuate, restore, chuyển instance); **chưa thử trên laptop thật** |
| URL công khai cho container: `https://<tên>-<6 ký tự>.vexenhanh.id.vn` (proxy device + Traefik) | Chạy được trên staging qua Cloudflare → Traefik → proxy device → container |
| Alembic migration, reconciler đối chiếu database với Incus, sao lưu database hằng ngày | Chạy được, có test. Đã thử trên staging: nhận database cũ mà không mất dữ liệu, phát hiện `incus stop`/`start` làm ngoài minicloud, tự phục hồi khi API bị kill giữa lúc tạo máy |
| Green cloud: planner gom tải, exporter đo điện (RAPL, pin), Wake-on-LAN | Đã viết, có test. Planner không để quá 1 trong 3 laptop ngủ, để giữ quorum của cụm (đã thử trên cụm ảo); chưa nối phần thực thi |
| Green cloud: điện năng của từng máy (Wh và gam CO₂ trên sao kê), công suất từng laptop trên console (`GET /v1/green`) | Chạy được, có test; công suất laptop đang tính theo mô hình P(u) với thời gian CPU thật từ Incus |
| Script dựng cụm 3 laptop | Đã chạy trọn trên cụm ảo 3 VM trong WSL (`infra/lab`, smoke test đạt); **chưa chạy trên laptop thật** |
| Cổng vào public: Cloudflare Tunnel với `vexenhanh.id.vn` ([ADR-0005](docs/adr/0005-ten-mien-va-cong-vao.md)) | Đã dựng trên laptop-1 tạm (tunnel `minicloud-wsl`); có [hướng dẫn](infra/edge/README.md) |
| Portal web, Keycloak, OVN, autoscaling, object storage | Chưa làm (xem [kế hoạch tuần 1–2](docs/plan/tuan-1-2.md)) |

## Cấu trúc

```
src/minicloud/
  api/          REST API (FastAPI): account, orgs, instances, billing, catalog, admin
  services/     nghiệp vụ: tenancy, instances, metering
  drivers/      data plane: fake (giả lập trong bộ nhớ) và incus (cụm thật)
  green/        Green cloud: planner, đo điện, chia điện năng, Wake-on-LAN, exporter
  migrations/   migration Alembic của database
  scheduler.py  chọn laptop cho instance mới
  worker.py     thực thi operation; mỗi phút đối chiếu, tính tiền, ghi tuyến
  reconciler.py sửa database theo những gì Incus thật sự đang chạy
  migrate.py    nâng schema khi khởi động; backup.py sao lưu SQLite
tests/          pytest, chạy hoàn toàn trên máy dev
infra/          script biến 3 laptop thành cụm Incus, cụm ảo để thử script (lab/), cổng vào public
deploy/         docker compose cho PostgreSQL, Prometheus, Grafana khi phát triển
docs/           ADR, kiến trúc, threat model, kế hoạch
portal/         web console (vai trò 5 bắt đầu từ đây)
```

## Chạy thử trên máy dev (Windows, macOS, Linux)

Cần [uv](https://docs.astral.sh/uv/) và Python 3.12.

```powershell
uv sync
uv run pytest
uv run uvicorn minicloud.main:create_app --factory --reload
```

Mở http://127.0.0.1:8000 để dùng web console, hoặc http://127.0.0.1:8000/docs để thử API trên Swagger UI. Mặc định dùng SQLite và cụm giả lập 3 laptop, nên không cần Incus.

Thử nhanh bằng PowerShell:

```powershell
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/signup -ContentType application/json -Body '{"email":"an@example.com","name":"An","org_name":"Nhom An"}'
$h = @{ Authorization = "Bearer $($r.api_key)" }
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/projects/$($r.project_id)/instances" -Headers $h -ContentType application/json -Body '{"name":"web-1"}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/projects/$($r.project_id)/instances" -Headers $h
```

Lệnh quản trị (tài khoản admin thấy mục Nhà cung cấp trên console và gọi được `/v1/admin/...`):

```powershell
uv run minicloud make-admin an@example.com
uv run minicloud orgs             # mọi tổ chức: mã, email chủ, số dư, số máy đang chạy
uv run minicloud grant-credits <org_id> 1000
uv run minicloud migrate          # nâng database lên bản schema mới nhất
uv run minicloud backup           # chép minicloud.db vào thư mục backups/
```

Mỗi lần khởi động, app tự nâng `minicloud.db` lên bản schema mới nhất, không cần xoá file nữa. Sửa model thì tạo migration theo [CONTRIBUTING.md](CONTRIBUTING.md#đổi-schema-database).

## Chạy với Incus thật trên máy dev

Xem [docs/dev-incus-wsl.md](docs/dev-incus-wsl.md): dựng Incus một node trong WSL2 trong khoảng 15 phút, rồi chạy control plane với driver thật.

## Nối với cụm Incus thật

1. Dựng cụm theo [infra/README.md](infra/README.md).
2. Chép `.env.example` thành `.env`, đặt `MINICLOUD_DRIVER=incus` và đường dẫn chứng chỉ.
3. Chạy smoke test trong [kế hoạch tuần 1–2](docs/plan/tuan-1-2.md) trước khi mở cho người dùng.

## Tài liệu

- [Kiến trúc](docs/architecture.md): mô hình tài nguyên, luồng tạo instance, cách tính tiền.
- [Quyết định kiến trúc (ADR)](docs/adr/): Incus, Python/FastAPI, hạ tầng 3 laptop, Green cloud, tên miền và cổng vào, node chạy trong WSL2 (ADR-0006, đang đề xuất).
- [Threat model](docs/threat-model.md): các mối đe doạ của môi trường đa người thuê.
- [Bảng giá và giá vốn](docs/bang-gia.md): giá cho khách, giá vốn mỗi vCPU-giờ, so với DigitalOcean, AWS và nhà cung cấp trong nước.
- [Chạy thử với người dùng thật](docs/chay-thu.md): tin mời mẫu, việc của người trực, nhật ký sự cố, số liệu cho báo cáo, khảo sát.
- [Kịch bản demo](docs/kich-ban-demo.md): việc chuẩn bị và từng bước của 7 màn demo.
- Thí nghiệm: `infra/bench/provision.py` (thời gian cấp phát), `density.sh` (mật độ), `noisy-neighbor.sh`, `overcommit.sh`, `load.py` (bắn tải cho autoscaling), `rto.sh` (khôi phục control plane).
- Cho người dùng: [hướng dẫn](src/minicloud/console/huong-dan.html) và [điều khoản sử dụng](src/minicloud/console/dieu-khoan.html), phục vụ ở `/console/`.
- [Kế hoạch tuần 1–2](docs/plan/tuan-1-2.md): việc của từng vai trò để qua mốc 1.
- [Cách đóng góp](CONTRIBUTING.md): nhánh, review, quy ước commit.
