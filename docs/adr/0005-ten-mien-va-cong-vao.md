# ADR-0005: Tên miền vexenhanh.id.vn và cổng vào bằng Cloudflare Tunnel

- Trạng thái: Đã chốt
- Ngày: 2026-11-25

## Bối cảnh

- Trưởng nhóm có sẵn tên miền `vexenhanh.id.vn`, muốn dùng luôn cho môn này để khỏi tốn tiền.
- Tên miền đang phục vụ website vexenhanh trên Vercel (bản ghi `@` và `www`). DNS đã nằm trên Cloudflare (`nena.ns.cloudflare.com`, `nick.ns.cloudflare.com`) và không có bản ghi email.
- `id.vn` nằm trong Public Suffix List, nên với trình duyệt và Cloudflare, `vexenhanh.id.vn` là một tên miền riêng (một "site").
- Mạng nhà và ký túc xá không có IP public, nên cụm cần một đường vào từ bên ngoài.

## Quyết định

- **Cổng vào:** Cloudflare Tunnel, gói miễn phí. `cloudflared` chạy trên laptop-1 và chủ động kết nối ra Cloudflare, nên không cần IP public, không mở cổng router và không thuê VPS.
- **Tên máy chỉ ở cấp một** dưới `vexenhanh.id.vn`, vì chứng chỉ Universal SSL miễn phí chỉ phủ `vexenhanh.id.vn` và `*.vexenhanh.id.vn`:

| Tên | Dùng cho | Trên laptop-1 trỏ tới |
| --- | --- | --- |
| `cloud-api.vexenhanh.id.vn` | API của control plane | `http://localhost:8000` |
| `cloud.vexenhanh.id.vn` | Web console, hiện do control plane phục vụ ở `/console` | `http://localhost:8000` |
| `cloud-status.vexenhanh.id.vn` | Status page | `http://localhost:3001` |
| `<tên>-<6 ký tự>.vexenhanh.id.vn` | App của khách (chính là tên instance trong Incus) | Traefik `http://localhost:8080` |

- **App của khách:** dùng một bản ghi CNAME wildcard `*` (bật proxy) trỏ về tunnel. Bản ghi `@` và `www` của website vexenhanh giữ nguyên; DNS luôn ưu tiên bản ghi cụ thể hơn wildcard.

## Các phương án đã cân nhắc

| Phương án | Lý do không chọn |
| --- | --- |
| VPS + WireGuard + Traefik | Tốn tiền thuê hằng tháng; giữ làm dự phòng khi cần chuyển TCP thô (ví dụ SSH) |
| Tên nhiều cấp như `*.apps.cloud.vexenhanh.id.vn` | Cần Advanced Certificate Manager trả phí của Cloudflare |
| Tên miền riêng cho app của khách | Cô lập tốt nhất (như `*.vercel.app`), nhưng phải mua thêm hoặc nhờ một thành viên đăng ký thêm tên miền `.id.vn` |

## Hệ quả

- Cổng vào tốn 0 đồng.
- Với người dùng phổ thông, tunnel chỉ chuyển HTTP(S). Muốn SSH từ Internet vào instance thì dùng terminal trên web (vai trò 5) hoặc VPN.
- App của khách và website vexenhanh nằm chung một site. Có ba rủi ro:
  1. Cookie đặt với `Domain=vexenhanh.id.vn` sẽ bị gửi tới app của khách.
  2. App xấu có thể ghi đè cookie của site khác (cookie tossing).
  3. Nếu một app bị báo cáo lừa đảo, Cloudflare có thể khoá cả tên miền, kéo theo website vexenhanh.

  Cách giảm rủi ro: chỉ mời người quen; có chính sách sử dụng (AUP); gỡ được tuyến của một instance ngay khi có báo cáo; website và portal không đặt cookie có thuộc tính `Domain`; portal dùng cookie tiền tố `__Host-` hoặc giữ token trong bộ nhớ.
- File credentials của tunnel là bí mật, chỉ nằm trên laptop-1, không đưa vào git.
- Muốn tách app của khách ra tên miền riêng sau này thì chỉ cần đổi bản ghi wildcard và cấu hình Traefik.
