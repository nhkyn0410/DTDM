# ADR-0002: Viết control plane bằng Python và FastAPI

- Trạng thái: Đề xuất, nhóm có thể đổi trong tuần 1
- Ngày: 2026-11-25

## Bối cảnh

Nhóm có 6 người với trình độ lập trình khác nhau và 12 tuần. Đề cương hứa có REST API kèm tài liệu OpenAPI. Máy phát triển của nhóm chạy Windows và đã có Python, chưa cài Go.

## Các phương án

| Phương án | Ưu điểm | Nhược điểm |
| --- | --- | --- |
| Go | Có client Incus chính thức, chạy nhanh, một file binary | Không phải ai cũng biết Go; học thêm tốn tuần đầu |
| **Python 3.12 + FastAPI** | Cả nhóm dùng được; OpenAPI và Swagger UI tự sinh; viết test nhanh | Phải tự gọi REST API của Incus; chậm hơn Go (không đáng kể ở quy mô vài chục người dùng) |
| Node.js + NestJS | Cùng ngôn ngữ với portal | Hệ sinh thái ORM và test kém quen thuộc với nhóm |

## Quyết định

- Python 3.12, FastAPI, SQLAlchemy 2, Pydantic 2; gọi Incus bằng httpx qua TLS client certificate.
- uv quản lý môi trường; ruff để lint và format; pytest để test.
- SQLite khi phát triển; PostgreSQL trên laptop-1 khi chạy thật.
- v0.1 chạy worker trong cùng tiến trình với API (một thread). Khi cần chạy nhiều worker thì tách ra và thêm hàng đợi NATS.

## Hệ quả

- Client Incus tự viết nằm ở `drivers/incus.py`, có test với API giả; phải chạy smoke test trên cụm thật trước khi tin.
- Cần thêm Alembic trước khi có dữ liệu thật của người dùng thử (vai trò 3). Đã làm ngày 2026-11-25: app tự migrate khi khởi động, script 50 sao lưu rồi mới migrate.
- Mọi logic có `now` truyền vào được, nên test tính tiền không phụ thuộc đồng hồ thật.
