# minicloud

Nền tảng điện toán đám mây đa người thuê chạy trên cụm Incus gồm 3 laptop. Đây là đồ án môn Điện toán đám mây: nhóm 6 người đóng vai nhà cung cấp cloud, cho 10–20 người dùng thật thuê tài nguyên, và lấy Green cloud làm điểm nhấn.

Đề cương: [Đề cương đồ án – Nền tảng cloud đa người thuê](https://claude.ai/artifact/BmrqLGqGmD5cdaFGFSccQE). Link chỉ mở được sau khi trưởng nhóm chia sẻ.

## Trạng thái (v0.1, tuần 1)

| Phần | Trạng thái |
| --- | --- |
| Control plane: đăng ký, tổ chức, project, quota, API key, audit log | Chạy được, có test |
| Vòng đời instance qua operation bất đồng bộ, scheduler spread/binpack | Chạy được với cụm giả lập |
| Metering theo giây, credit, tự khoá khi hết credit | Chạy được, có test |
| Chặn truy cập chéo tenant (trả 404, ghi audit) | Chạy được, có test |
| Driver Incus (REST API qua TLS client cert) | Đã viết, test với API giả; **chưa chạy trên cụm thật** |
| Green cloud: planner gom tải, exporter đo điện (RAPL, pin), Wake-on-LAN | Đã viết, có test; chưa nối phần thực thi |
| Script dựng cụm 3 laptop | Đã viết; **chưa chạy trên laptop thật** |
| Portal web, Keycloak, OVN, cổng vào public, Alembic, autoscaling, object storage | Chưa làm (xem [kế hoạch tuần 1–2](docs/plan/tuan-1-2.md)) |

## Cấu trúc

```
src/minicloud/
  api/          REST API (FastAPI): account, orgs, instances, billing, catalog, admin
  services/     nghiệp vụ: tenancy, instances, metering
  drivers/      data plane: fake (giả lập trong bộ nhớ) và incus (cụm thật)
  green/        Green cloud: planner, đo điện, chia điện năng, Wake-on-LAN, exporter
  scheduler.py  chọn laptop cho instance mới
  worker.py     thực thi operation và tính tiền định kỳ
tests/          pytest, chạy hoàn toàn trên máy dev
infra/          script biến 3 laptop thành cụm Incus, cấu hình cổng vào public
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

Mở http://127.0.0.1:8000/docs để thử API trên Swagger UI. Mặc định dùng SQLite và cụm giả lập 3 laptop, nên không cần Incus.

Thử nhanh bằng PowerShell:

```powershell
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/signup -ContentType application/json -Body '{"email":"an@example.com","name":"An","org_name":"Nhom An"}'
$h = @{ Authorization = "Bearer $($r.api_key)" }
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/projects/$($r.project_id)/instances" -Headers $h -ContentType application/json -Body '{"name":"web-1"}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/projects/$($r.project_id)/instances" -Headers $h
```

Lệnh quản trị (cấp quyền admin để xem `/v1/admin/nodes` và `/v1/admin/green/plan`):

```powershell
uv run minicloud make-admin an@example.com
uv run minicloud grant-credits <org_id> 1000
```

## Nối với cụm Incus thật

1. Dựng cụm theo [infra/README.md](infra/README.md).
2. Chép `.env.example` thành `.env`, đặt `MINICLOUD_DRIVER=incus` và đường dẫn chứng chỉ.
3. Chạy smoke test trong [kế hoạch tuần 1–2](docs/plan/tuan-1-2.md) trước khi mở cho người dùng.

## Tài liệu

- [Kiến trúc](docs/architecture.md): mô hình tài nguyên, luồng tạo instance, cách tính tiền.
- [Quyết định kiến trúc (ADR)](docs/adr/): Incus, Python/FastAPI, hạ tầng 3 laptop, Green cloud.
- [Threat model](docs/threat-model.md): các mối đe doạ của môi trường đa người thuê.
- [Kế hoạch tuần 1–2](docs/plan/tuan-1-2.md): việc của từng vai trò để qua mốc 1.
- [Cách đóng góp](CONTRIBUTING.md): nhánh, review, quy ước commit.
