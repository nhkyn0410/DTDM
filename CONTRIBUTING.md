# Cách đóng góp

## Nhánh và pull request

- `main` lúc nào cũng phải chạy được. Không push thẳng lên `main`.
- Mỗi việc làm trên một nhánh riêng, đặt tên `<mảng>/<mô-tả-ngắn>`. Các mảng: `infra/`, `net/`, `cp/`, `billing/`, `portal/`, `green/`, `docs/`. Ví dụ: `cp/alembic`, `green/wake-on-lan`.
- Mỗi pull request cần ít nhất 1 người ở vai trò khác review.
- Trước khi mở PR, ba lệnh sau phải qua (CI cũng tự chạy):

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Commit

Theo [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`. Viết tiếng Việt hay tiếng Anh đều được, miễn ngắn và rõ.

## Quy ước code

- Logic nghiệp vụ nằm trong `services/`; route trong `api/` chỉ đọc request rồi gọi service.
- Mọi thay đổi trạng thái instance phải đi qua `Operation`, worker mới gọi data plane.
- Gọi `metering.settle_instance` trước khi đổi trạng thái instance, để khoảng thời gian trước đó được tính đúng giá.
- Truy cập tài nguyên của tenant phải qua `tenancy.check_access`. Mỗi endpoint mới cần một test chứng minh tenant khác nhận 404.
- Không commit bí mật: chứng chỉ và khoá để trong `secrets/` (đã có trong `.gitignore`), cấu hình để trong `.env`.
- Script shell giữ xuống dòng kiểu LF (`.gitattributes` đã ép); trên Windows đừng mở và lưu lại bằng trình soạn thảo đổi sang CRLF.

## Quyết định lớn

Ghi thành ADR mới trong `docs/adr/`. Không sửa ADR đã duyệt; nếu đổi ý thì viết ADR mới và đánh dấu ADR cũ là "Thay thế bởi ADR-xxxx".

## Ai phụ trách thư mục nào

| Thư mục | Vai trò |
| --- | --- |
| `infra/node/`, `docs/hardware.md` | 1. Trưởng nhóm, hạ tầng |
| `infra/edge/`, `docs/threat-model.md` | 2. Mạng và bảo mật |
| `src/minicloud/` (trừ `green/`), `drivers/` | 3. Control plane lõi |
| `services/metering.py`, `api/billing.py`, xác thực | 4. IAM và billing |
| `portal/` | 5. Portal và khách hàng |
| `src/minicloud/green/`, `deploy/prometheus/` | 6. Green cloud và giám sát |
