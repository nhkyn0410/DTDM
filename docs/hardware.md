# Phần cứng của cụm

Vai trò 1 điền ở tuần 1, vai trò 6 điền số đo điện. Các số này dùng để tính bảng giá và đặt tham số Green cloud (`MINICLOUD_GREEN_IDLE_WATTS`, `MINICLOUD_GREEN_MAX_WATTS`).

Cách kiểm tra một máy và điền bảng, kể cả hướng dẫn để agent (Codex, Claude Code…) làm thay: [kiem-tra-laptop.md](kiem-tra-laptop.md). Trên Windows, `infra/check/check-laptop.ps1` in sẵn dòng cho bảng.

| Node | Model | CPU (nhân/luồng) | RAM | Ổ đĩa | Cổng LAN | MAC cổng LAN | IP | VT-x/AMD-V | RAPL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| laptop-1 | MSI Thin GF63 12VE | Intel Core i5-12450H, 8/12 | 32 GB DDR4-3200 | SSD NVMe 1 TB + HDD SATA 1 TB | Có, 1 Gbps (đang nối 100 Mbps) | D8:43:AE:06:78:D5 | 192.168.0.129 (DHCP) | Có, đã bật | Có |
| laptop-2 | | | | | | | | | |
| laptop-3 | | | | | | | | | |

## Số đo điện năng

Đo khi rút sạc: số từ pin là điện của cả máy. Trên Windows, `check-laptop.ps1 -MeasurePower` tự tạo tải và đọc pin; trên Ubuntu, lệnh tạo tải là `stress-ng --cpu 0 --timeout 120s`. Hai cột cắm sạc cần ổ cắm đo điện, vì khi cắm sạc pin không báo số. Số đo ở ổ cắm gồm cả phần hao trong cục sạc.

| Node | P_idle cắm sạc (W) | P_max cắm sạc (W) | P_idle rút sạc (W) | P_max rút sạc (W) | Thời gian ngủ (s) | Thời gian thức dậy (s) |
| --- | --- | --- | --- | --- | --- | --- |
| laptop-1 | | | 44.5 (xem ghi chú) | 45.8 (xem ghi chú) | Không ngủ (node ghim) | Không ngủ (node ghim) |
| laptop-2 | | | | | | |
| laptop-3 | | | | | | |

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

Chưa kiểm tra.

### laptop-3

Chưa kiểm tra.
