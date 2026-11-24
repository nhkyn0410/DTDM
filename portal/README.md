# Portal (web console)

Vai trò 5 phụ trách; thư mục này chưa có code.

Gợi ý để bắt đầu:

- **Stack:** Next.js và TypeScript. Sinh client từ `http://127.0.0.1:8000/openapi.json`, ví dụ bằng `openapi-typescript`.
- **CORS:** API đã cho phép `http://localhost:3000`. Đổi bằng `MINICLOUD_CORS_ORIGINS` nếu portal chạy ở địa chỉ khác.
- **Thứ tự các màn hình:**
  1. Đăng ký
  2. Danh sách instance
  3. Tạo instance
  4. Chi tiết instance: trạng thái, IP, nút dừng và xoá
  5. Credit và lịch sử sử dụng
  6. Audit log
- **Đăng nhập:** giai đoạn đầu dùng API key nhận lúc đăng ký và chỉ giữ trong phiên trình duyệt; chuyển sang Keycloak khi vai trò 4 xong.
- **Sau tuần 6:** terminal trên web bằng xterm.js, nối qua control plane tới `exec` của Incus.
