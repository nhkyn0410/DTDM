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
- Cần MAC của cổng mạng dây và bật Wake-on-LAN trong BIOS của từng laptop. Laptop không có cổng LAN thì dùng `rtcwake` hoặc chỉ gom tải mà không cho ngủ.
- Khi bật Green cloud, chính sách xếp instance mới nên chuyển từ `spread` sang `binpack`, để laptop rảnh được lâu hơn.
