# Portal (web console)

Một console tối thiểu đã chạy, do chính control plane phục vụ ở `/console`; trên staging nó nằm ở https://cloud.vexenhanh.id.vn. Mã nguồn ở [src/minicloud/console](../src/minicloud/console): ba file HTML, JS, CSS, không dùng framework và không cần bước build.

Console hiện làm được:

- đăng ký (có mã mời) và đăng nhập bằng API key;
- xem danh sách máy, tạo máy (container hoặc VM, chọn cấu hình và hệ điều hành, thêm SSH key);
- dừng, bật và xoá máy; mở URL công khai của container;
- xem số dư, số credit đã dùng trong 24 giờ và lịch sử tính tiền.

Vai trò 5 làm tiếp trên nền này, hoặc làm lại bằng Next.js trong thư mục `portal/`. Dù chọn cách nào cũng phải giữ ba quy tắc, vì app của khách nằm chung tên miền:

1. Không đưa dữ liệu từ API vào trang dưới dạng HTML (`innerHTML`, `insertAdjacentHTML`); chỉ dùng `textContent`. Test `tests/test_console.py` sẽ báo lỗi nếu vi phạm.
2. Không có script inline: header CSP chỉ cho chạy file script của chính console.
3. Không cất API key trong cookie. App của khách cùng site có thể đọc cookie đặt cho cả tên miền; `localStorage` thì chỉ trang console đọc được.

Việc tiếp theo nên làm:

- đăng nhập bằng Keycloak khi vai trò 4 xong;
- chọn giữa nhiều project;
- terminal trên web (xterm.js nối tới `exec` của Incus qua control plane);
- trang trạng thái của cụm và bảng giá.
