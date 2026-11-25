# ADR-0004: Green cloud: đo điện, gom tải và cho laptop ngủ

- Trạng thái: Đề xuất
- Ngày: 2026-11-25

## Bối cảnh

Green cloud là điểm nhấn nhóm đã chọn. Laptop thuận lợi cho việc này:

- **Hai nguồn đo điện có sẵn:** RAPL đo gói CPU, còn pin đo cả máy khi rút sạc.
- **Ngủ gần như không tốn điện:** ở trạng thái suspend, laptop tiêu thụ rất ít so với lúc chạy không tải.

## Quyết định

1. **Đo**
   - Exporter riêng của nhóm (`minicloud.green.exporter`) đọc RAPL và pin, xuất cho Prometheus ở cổng 9101.
   - Scaphandre chỉ dùng để đối chiếu số đo.
2. **Mô hình công suất**
   - Dùng mô hình tuyến tính P(u) = P_idle + (P_max − P_idle) × u (Fan, Weber, Barroso, ISCA 2007).
   - P_idle và P_max đo trên từng laptop bằng stress-ng.
3. **Lập kế hoạch gom tải** (`minicloud.green.planner`)
   - Chọn ít laptop nhất chứa được mọi instance mà vẫn chừa 20% dư.
   - Laptop ghim luôn thức.
   - Cụm từ 3 laptop trở lên chỉ cho tối đa một laptop ngủ. Incus giữ database của cụm trên các node bỏ phiếu và cần quá nửa số node đó còn thức (đã thử trên cụm ảo ngày 2026-11-25).
   - Ưu tiên giữ laptop đang chạy nhiều instance (ít phải chuyển) và laptop tốn ít điện.
   - Xếp instance bằng best-fit decreasing.
   - Có hysteresis: chỉ gom khi mức dùng dưới 30% và chỉ đánh thức khi trên 80%, để laptop không bật tắt liên tục.
4. **Thực thi** (vai trò 6, tuần 9–10)
   - Chuyển instance đi: cold migration trước, live migration cho VM sau.
   - Sau đó chạy `incus cluster evacuate` rồi `systemctl suspend`.
   - Đánh thức bằng Wake-on-LAN qua cổng mạng dây; dự phòng bằng `rtcwake` hẹn giờ.
5. **Chia điện năng cho khách** (`minicloud.green.attribution`)
   - Phần điện lúc rảnh chia theo số vCPU được cấp, phần còn lại chia theo thời gian CPU thật sự dùng.
   - Hiện số Wh trên hoá đơn.

## Hệ quả

- Di chuyển instance gây gián đoạn từ vài giây đến vài phút; thí nghiệm "Năng lượng" phải đo cả mức gián đoạn này.
- Khi evacuate, Incus tự chọn laptop đích, không theo planner (thấy trên cụm ảo ngày 2026-11-25). Vì vậy ở bước 4, planner phải tự chuyển hết instance tới laptop nó chọn bằng `move_instance`, rồi mới evacuate laptop đã trống.
- Với 3 laptop, Green cloud chỉ tiết kiệm được điện lúc nhàn của một laptop, khoảng 20–40 W theo số đo lúc rút sạc. Trên cụm ảo, khi 2 trong 3 node ngủ:
  - Mọi lệnh Incus báo `no available cowsql leader server found`, nên không tạo, dừng hay chuyển được instance.
  - App trên laptop còn thức vẫn chạy.
  - Đánh thức một laptop thì sau khoảng 7 giây cụm quản lý lại được.
- `GET /v1/admin/green/plan` không thấy laptop đang ngủ, vì Incus báo laptop đó có 0 vCPU. Vì vậy planner chưa bao giờ đề xuất đánh thức. Phần thực thi (vai trò 6) phải tự nhớ dung lượng của laptop đang ngủ.
- Cần MAC của cổng mạng dây và bật Wake-on-LAN trong BIOS của từng laptop. Laptop không có cổng LAN thì dùng `rtcwake` hoặc chỉ gom tải mà không cho ngủ.
- Khi bật Green cloud, chính sách xếp instance mới nên chuyển từ `spread` sang `binpack`, để laptop rảnh được lâu hơn.
