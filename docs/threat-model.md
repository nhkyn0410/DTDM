# Threat model (bản 1)

Phân loại theo STRIDE; vai trò 2 rà soát và bổ sung trước mốc 1.

Ranh giới tin cậy:

- **Internet ↔ VPS:** Traefik làm cổng vào.
- **VPS ↔ cụm:** đi qua WireGuard.
- **Control plane ↔ Incus:** TLS hai chiều bằng chứng chỉ client.
- **Tenant ↔ tenant:** tách bằng Incus project và mạng.

| # | Loại | Mối đe doạ | Biện pháp | Trạng thái |
| --- | --- | --- | --- | --- |
| 1 | Spoofing | Dùng API key bị lộ | Chỉ lưu hash SHA-256, key hiện một lần; thu hồi key; đăng nhập Keycloak có MFA | Hash: có. Thu hồi, MFA: chưa |
| 2 | Spoofing | Người lạ đăng ký hàng loạt | Chỉ mời người quen; mã mời hoặc duyệt tay; rate limit | Chưa |
| 3 | Tampering | Chèn lệnh cloud-init qua SSH key có xuống dòng | Kiểm tra key bằng `fullmatch`; ghi key dạng chuỗi JSON trong YAML | Có, có test |
| 4 | Tampering | Tự cộng credit | Chỉ admin nền tảng cộng được; mỗi lần cộng có ledger và audit | Có, có test |
| 5 | Repudiation | Khách chối đã xoá instance | Audit log mọi thao tác, kèm người thực hiện | Có |
| 6 | Information disclosure | Tenant B đoán id tài nguyên của A (IDOR/BOLA) | Kiểm tra membership mọi nơi; người ngoài nhận 404; ghi audit | Có, có test |
| 7 | Information disclosure | SSRF tới metadata service | Metadata service bắt buộc token kiểu IMDSv2; chỉ trả dữ liệu của chính instance | Chưa (chức năng nên có) |
| 8 | Information disclosure | Lộ chứng chỉ client của control plane (toàn quyền cụm) | Lưu trong `secrets/` ngoài git, quyền 600, chỉ nằm trên laptop-1 | Một phần |
| 9 | Denial of service | Một tenant chiếm hết CPU hoặc RAM | `limits.cpu`, `limits.memory` cho từng instance; `limits.*` cho project; quota ở API | Có; thí nghiệm "Hàng xóm ồn ào" sẽ đo |
| 10 | Denial of service | Đào coin, spam, quét mạng từ instance | Chặn cổng 25 chiều ra, giới hạn băng thông ra ngoài, cảnh báo CPU 100% kéo dài, chính sách AUP | Chưa |
| 11 | Denial of service | Mất điện hoặc rút mạng laptop-1 | Status page, backup database ra ngoài cụm; chấp nhận ở mức demo | Chưa |
| 12 | Elevation of privilege | Thoát từ container ra laptop | Project `restricted` (cấm privileged, nesting, cấu hình raw); dùng VM khi cần cô lập mạnh | Đã cấu hình, chưa kiểm thử |
