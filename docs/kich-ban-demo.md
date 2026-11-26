# Kịch bản demo sáng thứ Bảy 28/11/2026

Bảy màn theo mục 8 của đề cương, chạy trên staging (`https://cloud.vexenhanh.id.vn`). Mỗi màn ghi: ai làm, làm gì, cho người xem thấy gì. Màn nào hỏng giữa chừng thì chiếu video quay sẵn của màn đó và đi tiếp.

## Chuẩn bị (tối thứ Sáu, kiểm tra lại lúc 7:00 sáng thứ Bảy)

- [ ] laptop-1 cắm sạc, tắt chế độ ngủ khi gập máy, WSL đang chạy (tác vụ `minicloud-wsl` của `infra/wsl/setup-windows.ps1`). Đã thử ngày 26/11: khởi động lại Windows thì staging tự chạy lại sau khoảng 80 giây, không cần đăng nhập.
- [ ] `https://cloud-api.vexenhanh.id.vn/healthz` trả `{"status":"ok"}`; app `hello` mở được.
- [ ] Hai tài khoản đã đăng ký sẵn, mỗi tài khoản một trình duyệt (hoặc một cửa sổ ẩn danh):
  - **Khách A:** có một container `web-1` đang chạy nginx;
  - **Khách B:** có một container `b-1` đang chạy.
- [ ] Nạp đủ credit cho A và B: `sudo minicloud-admin grant-credits <org_id> 2000`.
- [ ] Tạo sẵn nhóm máy `web` (1–3 máy, app mẫu) cho màn 4, mở thử địa chỉ chung của nhóm một lần.
- [ ] Máy trình chiếu có repo và `uv`, để chạy `infra/bench/load.py`.
- [ ] Điện thoại dùng 4G (tắt Wi-Fi) đã mở `https://cloud.vexenhanh.id.vn`.
- [ ] Video quay sẵn từng màn nằm trong một thư mục trên máy trình chiếu.

## Màn 1. Khách hàng mới: tự phục vụ

1. Một bạn trong lớp đăng ký trên điện thoại với mã mời, lưu API key, nhận 500 credit.
2. Bạn đó tạo container `demo-1`. Chỉ cho người xem trạng thái đổi từ "Đang tạo" sang "Đang chạy" sau vài giây (đo trên staging: p50 6,7 giây, p95 7,4 giây).
3. Bấm **Chạy lệnh**, chạy lần lượt hai lệnh mẫu "Cài web server nginx" và "Tạo trang chào".
4. Bấm vào địa chỉ HTTPS của máy: trang "Xin chào từ minicloud".

Đạt khi: nhóm không phải làm gì giúp người dùng.

## Màn 2. Truy cập từ mọi nơi

Trên điện thoại 4G: mở app của màn 1, rồi mở console và chạy `uptime` trong máy.

## Màn 3. Hai tenant cạnh nhau

1. **Khách B gọi tài nguyên của A:** trên `/docs`, bấm Authorize bằng key của B, gọi `GET /v1/instances/{id máy của A}` → 404. Khách A mở audit log (`GET /v1/orgs/{org A}/audit`) và thấy lần bị chặn, kèm id người gọi.
2. **B quét máy của A:** trong máy của B, chạy `ping -c 2 <IP máy của A>`, không tới; `curl -sI https://example.com` vẫn chạy. Lý do: `security.port_isolation` trên card mạng của mọi máy khách.
3. **Hàng xóm ồn ào:** chiếu kết quả của `infra/bench/noisy-neighbor.sh` (chạy trực tiếp mất khoảng 10 phút). Khi A dùng hết CPU nhưng bị giới hạn 1 vCPU, B vẫn chạy như lúc một mình (3.418 so với 3.374 event/giây); bỏ giới hạn thì B chậm đi 23%.

## Màn 4. Tải tăng đột ngột: tự co giãn

1. Mở console của A ở mục **Nhóm máy tự co giãn**: nhóm `web` đang có 1 máy.
2. Trên máy trình chiếu, chạy:

   ```
   uv run python infra/bench/load.py https://<địa chỉ nhóm web> --workers 20 --seconds 300
   ```

3. Cứ 10 giây, script in số request mỗi giây và máy nào trả lời. Lần thử trên staging (26/11): CPU lên 100%, nhóm lên 2 máy sau 88 giây và 3 máy sau 149 giây; lưu lượng tăng từ 11 lên 26–28 request mỗi giây, chia đều cho các máy, và không request nào lỗi lúc máy mới vào nhóm.
4. Ngừng tải. CPU xuống thấp liên tục 3 phút thì nhóm bớt máy; trên staging, nhóm về 2 máy sau 4,5 phút và về 1 máy sau 3 phút nữa. Nói trước điều này rồi chuyển sang màn sau.

## Màn 5. Hoá đơn cuối buổi

1. Trên console của A: đồng hồ "Tháng này (tạm tính)" chạy theo giây; "Còn đủ dùng" cho biết credit còn đủ bao lâu.
2. Mục **Sao kê tháng**: bấm **In hoặc lưu PDF**, rồi **Tải CSV** và mở bằng Excel.
3. **Một tài khoản hết credit:** trước màn này khoảng 2 phút, rút credit của B còn khoảng 1 credit:

   ```
   sudo minicloud-admin grant-credits <org_id của B> -<số credit cần trừ> --reason "demo: hết credit"
   ```

   Ở lượt tính tiền kế tiếp (tối đa 1 phút), tổ chức B bị khoá và máy của B bị dừng. Console của B hiện "Tổ chức đã hết credit".

## Màn 6. Rút điện một node

Nhóm không dựng cụm 3 laptop thật ([ADR-0006](adr/0006-node-trong-wsl2.md)), nên màn này chiếu video của cụm ảo 3 VM (`infra/lab`), trong đó `incus cluster evacuate laptop-2` chuyển máy đi và reconciler ghi node mới trong vòng 1 phút.

Nói rõ giới hạn: chưa có cơ chế tự tạo lại máy ở laptop khác khi một laptop chết hẳn.

## Màn 7. Đêm vắng khách: Green cloud

1. Thẻ **Điện năng** trên console: công suất ước tính từng laptop, máy của khách đang dùng bao nhiêu W. Đăng nhập bằng tài khoản admin để thấy thêm mục **Nhà cung cấp**: doanh thu mô phỏng trong tháng, công suất cả cụm, vCPU và RAM đã cấp trên từng laptop (giám sát phía nhà cung cấp). Bên dưới là bảng khách hàng (số dư, đã tiêu, nút nạp credit), bảng máy của mọi khách (nút gỡ địa chỉ công khai), số liệu chạy thử, và 50 sự kiện gần nhất của cả nền tảng, trong đó có lần B bị chặn ở màn 3.
2. Sao kê tháng: số Wh và gam CO₂ của từng máy.
3. Admin gọi `GET /v1/admin/green/plan`: laptop nào nên ngủ, máy nào phải chuyển, tiết kiệm bao nhiêu W.
4. **Không có cụm thật**, nên bước này chiếu video quay trên cụm ảo. Ở đó node "ngủ" bằng `incus pause` VM của nó và thức bằng `incus resume`, thay cho Wake-on-LAN. Chưa có video thì dừng ở bước 3. Các lệnh gọi trên `/docs` bằng key admin:
   1. `POST /v1/admin/nodes/laptop-3/evacuate`: Incus chuyển máy của laptop-3 sang laptop khác, database và tuyến Traefik theo ngay. API từ chối laptop-1 (node ghim) và mọi yêu cầu làm cụm mất quorum.
   2. Cho laptop-3 ngủ, hoặc tắt hẳn (laptop-3 bật được Wake-on-LAN khi đã tắt). Thẻ Điện năng cho thấy công suất cả cụm giảm.
   3. `POST /v1/admin/nodes/laptop-3/wake` gửi gói Wake-on-LAN (MAC trong `MINICLOUD_GREEN_NODE_MACS`). Khi laptop-3 vào lại cụm, gọi `POST /v1/admin/nodes/laptop-3/restore`.
5. Kết luận bằng con số của Chương 4: cho laptop-3 ngủ nửa thời gian tiết kiệm khoảng 16 kWh và 11 kg CO₂ mỗi tháng.
