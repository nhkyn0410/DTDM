# Phần cứng của cụm

Vai trò 1 điền ở tuần 1, vai trò 6 điền số đo điện. Các số này dùng để tính bảng giá và đặt tham số Green cloud (`MINICLOUD_GREEN_IDLE_WATTS`, `MINICLOUD_GREEN_MAX_WATTS`).

| Node | Model | CPU (nhân/luồng) | RAM | Ổ đĩa | Cổng LAN | MAC cổng LAN | IP | VT-x/AMD-V | RAPL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| laptop-1 | | | | | | | | | |
| laptop-2 | | | | | | | | | |
| laptop-3 | | | | | | | | | |

## Số đo điện năng

Đo khi rút sạc: số từ pin là điện của cả máy. Lệnh tạo tải: `stress-ng --cpu 0 --timeout 120s`.

| Node | P_idle cắm sạc (W) | P_max cắm sạc (W) | P_idle rút sạc (W) | P_max rút sạc (W) | Thời gian ngủ (s) | Thời gian thức dậy (s) |
| --- | --- | --- | --- | --- | --- | --- |
| laptop-1 | | | | | | |
| laptop-2 | | | | | | |
| laptop-3 | | | | | | |
