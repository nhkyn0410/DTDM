# Phần cứng của cụm

Vai trò 1 điền ở tuần 1, vai trò 6 điền số đo điện. Các số này dùng để tính bảng giá và đặt tham số Green cloud (`MINICLOUD_GREEN_IDLE_WATTS`, `MINICLOUD_GREEN_MAX_WATTS`).

Cách kiểm tra một máy và điền bảng, kể cả hướng dẫn để agent (Codex, Claude Code…) làm thay: [kiem-tra-laptop.md](kiem-tra-laptop.md). Trên Windows, `infra/check/check-laptop.ps1` in sẵn dòng cho bảng.

| Node | Model | CPU (nhân/luồng) | RAM | Ổ đĩa | Cổng LAN | MAC cổng LAN | IP | VT-x/AMD-V | RAPL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| laptop-1 | MSI Thin GF63 12VE | Intel Core i5-12450H, 8/12 | 32 GB DDR4-3200 | SSD NVMe 1 TB + HDD SATA 1 TB | Có, 1 Gbps (đang nối 100 Mbps) | D8:43:AE:06:78:D5 | 192.168.0.129 (DHCP) | Có, đã bật | Có |
| laptop-2 | ASUS TUF Gaming A15 FA506NCG | AMD Ryzen 7 8845HS, 8/16 | 16 GB DDR5-5600 | SSD NVMe 512 GB | Có, 1 Gbps (chưa cắm dây) | B0:82:E2:69:38:55 | 192.168.1.204 (Wi-Fi, DHCP) | Có, đã bật | Có |
| laptop-3 | Lenovo IdeaPad Gaming 3 15IAH7 (82S9) | Intel Core i5-12500H, 12/16 | 16 GB DDR4-3200 | SSD NVMe 512 GB | Có, 1 Gbps (chưa cắm dây) | E4:A8:DF:D4:1E:64 | 192.168.100.245 (Wi-Fi, DHCP) | Có, đã bật | Có |

Cả cụm có 28 nhân, 44 luồng và 64 GB RAM, cùng 2 TB SSD và 1 TB HDD. Cả 3 máy đều là laptop gaming chạy Windows 11, có cổng LAN 1 Gbps và card đồ hoạ rời. Trên Windows, cả 3 chỉ có Modern Standby (S0ix), không có S3. Ngày kiểm tra, 3 máy ở 3 mạng khác nhau, nên IP trong bảng sẽ đổi khi gom máy về chung một router.

## Số đo điện năng

Đo khi rút sạc: số từ pin là điện của cả máy. Trên Windows, `check-laptop.ps1 -MeasurePower` tự tạo tải và đọc pin; trên Ubuntu, lệnh tạo tải là `stress-ng --cpu 0 --timeout 120s`.

Hai cột cắm sạc để trống: khi cắm sạc pin không báo số, mà nhóm không có ổ cắm đo điện và không mua thêm phần cứng. Mô hình Green cloud vì vậy dùng số đo lúc rút sạc. Hạn chế: khi chạy pin, máy giới hạn công suất CPU, nên P_max thật lúc cắm sạc cao hơn. Ví dụ, gói CPU của laptop-1 lên tới 42 W khi cắm sạc nhưng chỉ khoảng 15 W khi chạy pin.

| Node | P_idle cắm sạc (W) | P_max cắm sạc (W) | P_idle rút sạc (W) | P_max rút sạc (W) | Thời gian ngủ (s) | Thời gian thức dậy (s) |
| --- | --- | --- | --- | --- | --- | --- |
| laptop-1 | | | 44.5 (xem ghi chú) | 45.8 (xem ghi chú) | Không ngủ (node ghim) | Không ngủ (node ghim) |
| laptop-2 | | | 19.1 | 38.3 | | |
| laptop-3 | | | 38.2 (xem ghi chú) | 40.7 (xem ghi chú) | | |

Cột ngủ và thức dậy là thời gian suspend và đánh thức bằng Wake-on-LAN khi máy chạy Ubuntu ([ADR-0004](adr/0004-green-cloud.md)), nên chưa đo được trên Windows. Số Modern Standby mà báo cáo của laptop-2 và laptop-3 gửi kèm được ghi trong phần ghi chú.

## Ghi chú từng máy

### laptop-1

Máy của trưởng nhóm. Đang chạy Windows 11, với laptop-1 tạm (staging) trong WSL2 ([dev-incus-wsl.md](dev-incus-wsl.md)).

- RAM: 2 thanh 16 GB, dùng 2/2 khe, tối đa 64 GB.
- Ổ đĩa: Samsung SSD 970 EVO Plus 1TB (SSD NVMe), WDC WD10SPZX-24Z10 (HDD SATA). Không còn vùng chưa phân vùng; phân vùng trống nhiều nhất là D: trên HDD (trống 464 GB).
- Card đồ hoạ: NVIDIA GeForce RTX 4050 Laptop GPU (card rời), Intel UHD Graphics. Nếu sau này cài Ubuntu Server, cần kiểm tra card rời tự tắt khi nhàn, nếu không P_idle sẽ cao.
- Mạng: Realtek PCIe GbE Family Controller; Wi-Fi Intel Wi-Fi 6 AX201. Cổng LAN đang chỉ nối ở 100 Mbps dù card chạy được 1 Gbps: kiểm tra dây (cần Cat5e trở lên) hoặc cổng router.
- Wake-on-LAN trong driver Windows: Wake on Magic Packet bật; Shutdown Wake-On-Lan bật; magic packet lúc Modern Standby tắt.
- Pin: còn 88% dung lượng thiết kế (45.3/51.3 Wh).
- Ngủ: Windows dùng Modern Standby (S0ix), không dùng S3. Laptop-1 là node ghim nên không ngủ.
- RAPL: Windows đọc được điện của gói CPU qua bộ đếm Energy Meter. Lúc cắm sạc, gói CPU tốn 11–17 W khi gần nhàn (staging và các app đang mở) và khoảng 42 W khi chạy hết 12 luồng.
- Điện khi rút sạc, đo ngày 2026-11-25 bằng `check-laptop.ps1 -MeasurePower`:
  - Lúc nhàn: 44.5 W, khớp với lượng pin tụt (44.7 W); gói CPU chỉ 8.9 W, CPU dùng 6%. Chạy hết 12 luồng: 45.8 W, cao nhất 47.5 W; gói CPU 15.0 W.
  - Đây là số của máy đang dùng hằng ngày, chưa phải của một node rảnh. Lúc đo, Windows xuất hình ra 3 màn hình (màn laptop 144 Hz độ sáng 100%, một màn DisplayPort, một màn HDMI) và mở nhiều app (trình duyệt, VS Code, Steam, Zalo…), nên card rời RTX 4050 không ngủ (trạng thái P3, khoảng 10 W). Staging trong WSL vẫn chạy.
  - Khi chạy pin, máy giữ gói CPU quanh 15 W, nên P_max rút sạc gần bằng P_idle. Lúc cắm sạc, riêng gói CPU đã lên khoảng 42 W, nên P_max cắm sạc sẽ cao hơn nhiều.
  - Muốn có số gần với một node: rút 2 màn hình ngoài, đóng app, giảm độ sáng rồi đo lại, hoặc đo khi máy chạy Ubuntu Server. Chưa dùng các số này cho `MINICLOUD_GREEN_IDLE_WATTS` và `MINICLOUD_GREEN_MAX_WATTS`.
- IP là IP ở mạng nhà trưởng nhóm, chưa giữ cố định trên router.
- Firmware UEFI, Secure Boot bật, BitLocker tắt.
- Kiểm tra ngày 2026-11-25 trên Windows 11 Home Single Language (10.0.26200) bằng `infra/check/check-laptop.ps1`.

### laptop-2

Máy ASUS của một thành viên, chạy Windows 11 Home Single Language (10.0.26300). Chủ máy kiểm tra ngày 2026-11-25 bằng `check-laptop.ps1`, nhờ agent Codex làm theo [kiem-tra-laptop.md](kiem-tra-laptop.md).

- RAM: 1 thanh 16 GB DDR5-5600, dùng 1/2 khe, tối đa 64 GB; RAM đang chạy một kênh.
- Ổ đĩa: MTFDKBA512QGN-1BN1AABGA (SSD NVMe 512 GB), sức khoẻ tốt. Không còn vùng chưa phân vùng; C: trống khoảng 25 GB, D: khoảng 53 GB.
- Card đồ hoạ: AMD Radeon 780M (tích hợp), NVIDIA GeForce RTX 3050 Laptop GPU (card rời).
- Mạng: Realtek PCIe GbE, chạy được 1 Gbps nhưng chưa cắm dây; Wi-Fi Realtek 8852BE. Driver Windows bật Wake on Magic Packet; báo cáo không có tuỳ chọn Wake-on-LAN khi tắt máy.
- Pin: còn 98% dung lượng thiết kế (47.1/48.0 Wh).
- Ngủ: Modern Standby (S0ix), không có S3; chế độ giữ mạng khi ngủ bị policy tắt.
- Điện khi rút sạc:
  - Lúc nhàn: 19.1 W, CPU dùng khoảng 10%; tính theo lượng pin tụt là 19.6 W.
  - Chạy hết 16 luồng: 38.3 W, cao nhất 39.3 W; tính theo lượng pin tụt là 42.0 W.
  - Gói CPU (RAPL): 4.9 W lúc nhàn, 20.4 W lúc chạy tải.
  - Điều kiện: chế độ Silent của ASUS, độ sáng 89%, nhiều app đang mở, WSL đang chạy, pin 95% → 90%. Chế độ Silent giới hạn CPU, nên P_max ở chế độ khác sẽ cao hơn.
- Số Modern Standby lấy từ nhật ký Windows (sự kiện Kernel-Power 507): 24.6 giây tới lần nghỉ sâu đầu tiên, 1.66 giây để thoát. Đây không phải thời gian suspend và Wake-on-LAN trên Ubuntu mà bảng cần.
- Firmware UEFI, Secure Boot bật, BitLocker tắt. WSL: Ubuntu-24.04 và docker-desktop (WSL2).

### laptop-3

Máy Lenovo của một thành viên, chạy Windows 11 Home Single Language (10.0.26200). Chủ máy kiểm tra ngày 2026-11-25 bằng `check-laptop.ps1`. Báo cáo gốc ghi máy này là laptop-1; vì laptop-1 đã là máy MSI ở trên, bảng ghi tạm là laptop-3 cho tới khi trưởng nhóm chốt.

- RAM: 2 thanh 8 GB DDR4-3200, dùng 2/2 khe, tối đa 32 GB; muốn thêm RAM phải thay thanh.
- Ổ đĩa: KIOXIA KBG50ZNT512G (SSD NVMe 512 GB), sức khoẻ tốt. Không còn vùng chưa phân vùng; D: trống 76 GB. **C: còn trống chưa tới 1 GB**: phải dọn C: trước khi cài thêm gì, vì WSL, Docker và bản cập nhật Windows đều cần chỗ trên C:.
- Card đồ hoạ: NVIDIA GeForce RTX 3050 Laptop GPU (card rời), Intel Iris Xe Graphics.
- Mạng: Realtek PCIe GbE, chạy được 1 Gbps nhưng chưa cắm dây; Wi-Fi Realtek RTL8852BE. Driver Windows bật Wake on Magic Packet và Shutdown Wake-On-Lan.
- Pin: còn 90% dung lượng thiết kế (54.0/60.0 Wh).
- Ngủ: Modern Standby (S0ix), giữ mạng khi ngủ, không có S3.
- Điện khi rút sạc:
  - Lúc nhàn: 38.2 W, nhưng CPU vẫn dùng 27% nên chưa phải số nhàn; tính theo lượng pin tụt là 44.4 W.
  - Chạy hết 16 luồng: 40.7 W, cao nhất 42.9 W; tính theo lượng pin tụt là 45.2 W.
  - Điều kiện: gói điện Balanced, độ sáng 84%, pin 52% → 46%.
  - Cần đo lại sau khi đóng app.
- Báo cáo ghi thêm thời gian ngủ 88.07 giây và thức dậy 1.638 giây, do chủ máy cung cấp và không rõ cách đo. Đây cũng là số của Windows, không phải suspend và Wake-on-LAN trên Ubuntu.
- Firmware UEFI, Secure Boot tắt, BitLocker tắt. WSL: Ubuntu và docker-desktop (WSL2).
