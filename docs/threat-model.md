# Threat model (bản 1)

Phân loại theo STRIDE; vai trò 2 rà soát và bổ sung trước mốc 1.

Ranh giới tin cậy:

- **Internet ↔ Cloudflare:** TLS do Cloudflare cấp cho `*.vexenhanh.id.vn`.
- **Cloudflare ↔ laptop-1:** Cloudflare Tunnel; `cloudflared` chủ động kết nối ra, router không mở cổng nào.
- **Control plane ↔ Incus:** TLS hai chiều bằng chứng chỉ client.
- **Tenant ↔ tenant:** tách bằng Incus project và mạng.

| # | Loại | Mối đe doạ | Biện pháp | Trạng thái |
| --- | --- | --- | --- | --- |
| 1 | Spoofing | Dùng API key bị lộ | Chỉ lưu hash SHA-256, key hiện một lần; thu hồi key; đăng nhập Keycloak có MFA | Hash: có. Thu hồi, MFA: chưa |
| 2 | Spoofing | Người lạ đăng ký hàng loạt | Chỉ mời người quen; mã mời hoặc duyệt tay; rate limit | Mã mời: có (`MINICLOUD_SIGNUP_INVITE_CODE`, script 50 tự tạo). Rate limit: chưa |
| 3 | Tampering | Chèn lệnh cloud-init qua SSH key có xuống dòng | Kiểm tra key bằng `fullmatch`; ghi key dạng chuỗi JSON trong YAML | Có, có test |
| 4 | Tampering | Tự cộng credit | Chỉ admin nền tảng cộng được; mỗi lần cộng có ledger và audit | Có, có test |
| 5 | Repudiation | Khách chối đã xoá instance | Audit log mọi thao tác, kèm người thực hiện | Có |
| 6 | Information disclosure | Tenant B đoán id tài nguyên của A (IDOR/BOLA) | Kiểm tra membership mọi nơi; người ngoài nhận 404; ghi audit | Có, có test |
| 7 | Information disclosure | SSRF tới metadata service | Metadata service bắt buộc token kiểu IMDSv2; chỉ trả dữ liệu của chính instance | Chưa (chức năng nên có) |
| 8 | Information disclosure | Lộ chứng chỉ client của control plane (toàn quyền cụm) | Lưu trong `secrets/` ngoài git, quyền 600, chỉ nằm trên laptop-1 | Một phần |
| 9 | Denial of service | Một tenant chiếm hết CPU hoặc RAM | `limits.cpu`, `limits.memory` cho từng instance; `limits.*` cho project; quota ở API | Có; thí nghiệm "Hàng xóm ồn ào" sẽ đo |
| 10 | Denial of service | Đào coin, spam, quét mạng từ instance | Chặn cổng 25 chiều ra, giới hạn băng thông ra ngoài, cảnh báo CPU 100% kéo dài, chính sách AUP | Chưa |
| 11 | Denial of service | Mất điện hoặc rút mạng laptop-1, hỏng ổ đĩa | Status page; sao lưu database hằng ngày và chép ra ngoài cụm; chấp nhận ở mức demo | Sao lưu hằng ngày trên laptop-1: có (giữ 14 bản). Chép ra ngoài cụm, status page: chưa |
| 12 | Elevation of privilege | Thoát từ container ra laptop | Project `restricted` (cấm privileged, nesting, cấu hình raw); dùng VM khi cần cô lập mạnh | Đã cấu hình, chưa kiểm thử |
| 13 | Information disclosure | App của khách cùng site với website vexenhanh và portal, nhận được cookie có `Domain=vexenhanh.id.vn` hoặc ghi đè cookie | Website và portal không đặt cookie có `Domain`; portal dùng cookie `__Host-` hoặc token trong bộ nhớ; về sau tách app của khách sang tên miền riêng | Console: có (API key trong `localStorage`, CSP chặt, không chèn HTML, có test). Website vexenhanh: chưa kiểm tra |
| 14 | Denial of service | Một app bị báo cáo lừa đảo khiến Cloudflare khoá cả tên miền, website vexenhanh sập theo | Chỉ mời người quen, có AUP, gỡ tuyến của instance ngay khi có báo cáo | Gỡ tuyến: có (`POST /v1/admin/instances/{id}/unpublish`). Mời và AUP: chưa |
| 15 | Spoofing | Lộ file credentials của tunnel, kẻ khác chạy `cloudflared` giả để nhận lưu lượng của cloud | File chỉ nằm trên laptop-1, quyền 600, ngoài git; lộ thì xoá tunnel và tạo lại | Chưa |
| 16 | Repudiation | Instance bị đổi thẳng trên Incus, không qua API (thành viên nhóm dùng `incus`, control plane chết giữa chừng); database và hoá đơn sai mà không ai biết | Reconciler đối chiếu mỗi phút và ghi audit `instance.reconcile`; không xoá thứ gì; nhiều instance biến mất cùng lúc thì giữ nguyên và ghi log lỗi | Có, có test, đã thử trên staging |
| 17 | Information disclosure | Lộ bản sao lưu database (email, hash API key, số dư) | Thư mục 700, file 600, chủ là user `minicloud`; không để trong repo | Có |
| 18 | Denial of service | Một tenant ghi đầy ổ: pool `dir` trong WSL không áp giới hạn dung lượng (đã thử), mà đĩa ảo WSL lại nằm trên ổ C: của Windows | Pool btrfs (giới hạn có tác dụng, đã thử); theo dõi dung lượng còn trống | Chưa: staging vẫn dùng `dir` ([ADR-0006](adr/0006-node-trong-wsl2.md)) |
| 19 | Elevation of privilege | Container thoát ra WSL, rồi đọc file cá nhân của chủ laptop qua `/mnt/c` hoặc chạy `.exe` của Windows | Tắt `automount` và `interop` trong `/etc/wsl.conf` trên mọi node; repo nằm bên trong WSL | Chưa ([ADR-0006](adr/0006-node-trong-wsl2.md)) |
