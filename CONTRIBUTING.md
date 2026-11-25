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
- Đừng giả định database luôn khớp với Incus: reconciler (`reconciler.py`) sửa chỗ lệch mỗi phút và có thể chạy lại thao tác bị ngắt, như lệnh xoá. Vì vậy mỗi thao tác mới với Incus phải chạy lại được nhiều lần mà không hỏng.
- Truy cập tài nguyên của tenant phải qua `tenancy.check_access`. Mỗi endpoint mới cần một test chứng minh tenant khác nhận 404.
- Không commit bí mật: chứng chỉ và khoá để trong `secrets/` (đã có trong `.gitignore`), cấu hình để trong `.env`.
- Script shell giữ xuống dòng kiểu LF (`.gitattributes` đã ép); trên Windows đừng mở và lưu lại bằng trình soạn thảo đổi sang CRLF.

## Đổi schema database

Schema do Alembic quản lý. App tự chạy migration khi khởi động; trên laptop-1, script 50 sao lưu database rồi mới migrate.

1. Sửa model trong `src/minicloud/models.py`.
2. Sinh migration, lấy số kế tiếp làm mã (`0002`, `0003`, …). Lệnh đầu đưa `minicloud.db` của bạn lên bản mới nhất, vì Alembic chỉ so sánh với một database đã ở bản mới nhất:

   ```powershell
   uv run minicloud migrate
   uv run alembic revision --autogenerate -m "them cot x" --rev-id 0002
   ```

3. Đọc file vừa sinh trong `src/minicloud/migrations/versions/` và sửa nếu cần (ruff đã tự định dạng nó). Autogenerate hiểu đổi tên cột thành xoá rồi thêm, làm mất dữ liệu. Cột `NOT NULL` mới cần `server_default` để các dòng đã có không lỗi.
4. Chạy `uv run pytest`. `tests/test_migrations.py` báo đỏ nếu model và migration lệch nhau, và thử hạ cấp rồi nâng lại.

Không sửa migration đã vào `main`, vì database của staging đã chạy nó; cần đổi gì thì thêm migration mới.

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
