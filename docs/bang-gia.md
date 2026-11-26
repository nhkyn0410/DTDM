# Bảng giá và giá vốn

Tài liệu này trả lời hai câu hỏi: khách trả bao nhiêu (bảng giá), và nhóm tốn bao nhiêu để cung cấp (giá vốn). Phần giá vốn là bản đầu của thí nghiệm "Chi phí" ở mục 9 của đề cương. Mọi giả định đều ghi rõ trong bảng giả định; khi có số thật, nhóm thay vào và tính lại theo công thức ở cuối tài liệu.

## Bảng giá cho khách

Credit là đơn vị mô phỏng, không ai trả tiền thật. Cột "≈ đồng mỗi tháng" dùng quy đổi tham khảo 1 credit ≈ 10 đồng (`MINICLOUD_VND_PER_CREDIT`), chỉ để so với giá của nhà cung cấp thật.

| Cấu hình | vCPU | RAM | Ổ đĩa | Container (credit/giờ) | Máy ảo (credit/giờ) | Khi dừng (credit/giờ) | Container cả tháng (credit) | ≈ đồng mỗi tháng |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s1.small | 1 | 1 GB | 10 GB | 20 | 30 | 1 | 14.600 | 146.000 |
| s1.medium | 2 | 2 GB | 20 GB | 40 | 60 | 2 | 29.200 | 292.000 |
| s1.large | 4 | 4 GB | 40 GB | 80 | 120 | 4 | 58.400 | 584.000 |

Quy tắc tính tiền:

- **Theo giây:** máy chạy bao nhiêu giây trả bấy nhiêu, không làm tròn lên giờ. Worker ghi sổ mỗi phút. Tiền bắt đầu tính khi Incus báo máy đã chạy, không tính mấy giây tạo máy.
- **Lúc hệ thống ngừng:** nếu worker im lặng quá 5 phút (`MINICLOUD_METERING_MAX_GAP_SECONDS`), tức control plane không chạy vì laptop tắt hoặc ngủ, thì máy đang chạy không bị tính tiền cho khoảng đó; audit log ghi `metering.gap` với số giây bỏ qua. Máy đang dừng vẫn trả tiền ổ đĩa, vì dữ liệu vẫn nằm đó. Ngừng ngắn hơn (khởi động lại, cập nhật code) vẫn tính như thường, vì container vẫn chạy.
- **Máy ảo:** giá gấp 1,5 lần container, vì máy ảo giữ trọn RAM của nó và chạy kernel riêng.
- **Máy đang dừng:** chỉ trả tiền ổ đĩa, 0,1 credit mỗi GB mỗi giờ.
- **Dùng thử:** mỗi tổ chức mới nhận 500 credit (`MINICLOUD_TRIAL_CREDITS`), đủ chạy một container s1.small 25 giờ. Nhóm vận hành nạp thêm cho người dùng thử khi cần.
- **Hết credit:** tổ chức bị khoá và mọi máy đang chạy bị dừng. Nạp thêm thì tổ chức được mở khoá; khách tự bật lại máy.
- **Ngân sách tháng:** owner đặt được ngân sách. Khi phí trong tháng tới 80% và 100% ngân sách, console hiện cảnh báo và audit log ghi `budget.alert`. Ngân sách chỉ để cảnh báo, không dừng máy.
- **Sao kê:** theo tháng dương lịch, giờ Việt Nam. Mỗi sao kê có số dư đầu kỳ, tiền nạp, phí từng máy, số dư cuối kỳ và điện năng ước tính. Xuất được CSV và in ra PDF.

Khách xem bảng giá ở trang đăng ký của console, hoặc qua `GET /v1/catalog/flavors` và `GET /v1/catalog/pricing`. Sao kê nằm ở `GET /v1/orgs/{org_id}/statements/{YYYY-MM}`, bản CSV ở cùng đường dẫn thêm đuôi `.csv`.

## Giá vốn

### Giả định

| Khoản | Giá trị dùng để tính | Nguồn, ghi chú |
| --- | --- | --- |
| Giá mua mỗi laptop | 20.000.000 đ | **Giả định**, theo giá bán lẻ của dòng laptop gaming tầm trung 2022–2024. Thay bằng giá mua thật của từng máy. |
| Khấu hao | 36 tháng, chia đều, không tính giá bán lại | Thời gian dùng thường gặp của một laptop |
| Công suất mỗi laptop | P_idle và P_max đo lúc rút sạc ([hardware.md](hardware.md)); CPU dùng trung bình 30% | Mô hình P(u) = P_idle + (P_max − P_idle) × u, như [ADR-0004](adr/0004-green-cloud.md) |
| Hao phí bộ sạc | Điện ở ổ cắm = điện đo trên pin ÷ 0,88 | Bộ sạc laptop đạt hiệu suất khoảng 88% |
| Giá điện | 2.894 đ/kWh chưa VAT, cộng VAT 10%: 3.183 đ/kWh | Bậc 3 (201–400 kWh) của biểu giá điện sinh hoạt 5 bậc, áp dụng từ 1/11/2026 theo Quyết định 2892/QĐ-BCT ([Báo Pháp luật](https://baophapluat.vn/tu-1-11-gia-dien-sinh-hoat-con-5-bac-cao-nhat-3-844-dong-kwh.html), [SGGP](https://www.sggp.org.vn/chi-tiet-gia-ban-le-dien-ap-dung-tu-ngay-1-11-2026-post880092.html)). Lấy bậc 3 vì cụm cộng thêm khoảng 90 kWh vào điện của một hộ gia đình. VAT lấy mức 10% cho an toàn. |
| Mạng | 220.000 đ/tháng | **Giả định**: một gói cáp quang gia đình, tính trọn cho cụm |
| Cổng vào public, tên miền | 0 đ | Cloudflare Tunnel gói miễn phí; tên miền `vexenhanh.id.vn` nhóm đã có |
| Sức chứa | 27 suất s1.small (1 vCPU, 1 GiB) | RAM là giới hạn. WSL mặc định nhận 50% RAM của mỗi máy: 16 + 8 + 8 = 32 GiB. Trừ 1 GiB mỗi node (`MINICLOUD_RESERVED_MEMORY_MIB`) và khoảng 2 GiB cho control plane trên laptop-1, còn 27 GiB. CPU không phải giới hạn: 44 luồng, bán gấp 4 lần (`MINICLOUD_CPU_OVERCOMMIT`). |
| Mức lấp đầy | 25%, 50%, 75%, 100% | Phần sức chứa có khách thuê, trung bình cả tháng |
| Tỷ giá | 1 USD = 26.300 đ | **Giả định**; cập nhật theo tỷ giá ngày tra |

### Chi phí mỗi tháng

| Laptop | Công suất trên pin, CPU 30% (W) | Công suất ở ổ cắm (W) |
| --- | --- | --- |
| laptop-1 | 44,9 | 51,0 |
| laptop-2 | 24,9 | 28,2 |
| laptop-3 | 39,0 | 44,3 |
| Cả cụm | 108,7 | 123,5 |

Cả cụm chạy liên tục 730 giờ tốn 90,2 kWh mỗi tháng.

| Khoản | Đồng mỗi tháng | Tỷ lệ |
| --- | --- | --- |
| Khấu hao 3 laptop | 1.666.667 | 77% |
| Điện (90,2 kWh) | 287.052 | 13% |
| Mạng | 220.000 | 10% |
| **Tổng** | **2.173.719** | 100% |

Nếu coi 3 laptop là máy có sẵn, không phải mua cho cụm, thì chi phí biên chỉ gồm điện và mạng: 507.052 đ mỗi tháng.

### Giá vốn mỗi vCPU-giờ

Một suất s1.small là 1 vCPU kèm 1 GiB RAM, nên giá vốn mỗi suất-giờ cũng là giá vốn mỗi vCPU-giờ:

> giá vốn = chi phí tháng ÷ (số suất × 730 giờ × mức lấp đầy)

| Mức lấp đầy | Đầy đủ, 27 suất (WSL mặc định) | Đầy đủ, 43 suất (WSL được 24/12/12 GB) | Chi phí biên, 27 suất |
| --- | --- | --- | --- |
| 25% | 441 đ | 277 đ | 103 đ |
| 50% | 221 đ | 139 đ | 51 đ |
| 75% | 147 đ | 92 đ | 34 đ |
| 100% | 110 đ | 69 đ | 26 đ |

Với giá bán 200 đ/giờ, cụm hoà vốn đầy đủ khi lấp đầy 55% (27 suất) hoặc 35% (43 suất).

### So với thị trường

Gói nhỏ nhất có 1 vCPU và 1 GB RAM của mỗi nhà cung cấp, quy về 730 giờ mỗi tháng.

| Nhà cung cấp | Gói | Đồng mỗi tháng | Đồng mỗi giờ | Nguồn |
| --- | --- | --- | --- | --- |
| minicloud | s1.small container: 1 vCPU, 1 GB, 10 GB | 146.000 | 200 | Bảng giá ở trên |
| DigitalOcean | Basic: 1 vCPU, 1 GB, 25 GB SSD, 1 TB truyền tải | 157.800 (6 USD) | 216 | [Trang giá Droplet](https://www.digitalocean.com/pricing/droplets) |
| AWS EC2 | t3.micro ở Singapore: 2 vCPU chỉ được chạy nền 10%, 1 GiB, chưa có ổ đĩa | 253.400 (0,0132 USD/giờ) | 347 | [DoiT, tổng hợp từ bảng giá AWS](https://www.doit.com/compute/spot/ap-southeast-1/t3.micro) |
| Viettel IDC | T1.Base 01: 1 vCPU, 1 GB, 20 GB, chưa VAT | 129.000 | 177 | [whtop.com](https://www.whtop.com/plans/viettelidc.com.vn/147273) |

Cả bốn trang đã mở lại để kiểm tra ngày 2026-11-25. DigitalOcean là trang chính thức; AWS và Viettel IDC là trang tổng hợp, nên trước khi nộp báo cáo, nhóm nên đối chiếu với trang giá của chính hãng. Trang giá Cloud VPS của BizFly báo 404 nên đã bỏ khỏi bảng.

### Nhận xét

1. **Hoà vốn:** ở mức lấp đầy 50%, giá vốn đầy đủ là 221 đ/giờ, cao hơn giá bán 200 đ/giờ một chút. Muốn hoà vốn với WSL mặc định, cụm cần lấp đầy 55%.
2. **Khấu hao chiếm 77% chi phí.** Laptop là cách cung cấp compute đắt: mỗi máy chỉ 16–32 GB RAM, và WSL mặc định chỉ nhận một nửa. Cách giảm giá vốn rẻ nhất là cho WSL nhiều RAM hơn (`infra/wsl/setup-windows.ps1 -WslMemoryGB`): từ 27 lên 43 suất, giá vốn giảm 37%.
3. **Máy có sẵn thì rất rẻ:** tính theo chi phí biên, giá vốn chỉ 26–103 đ/giờ, thấp hơn mọi nhà cung cấp trong bảng. Dựng cloud cho sinh viên trên máy có sẵn là hợp lý; mua laptop mới để làm cloud thì không.
4. **Green cloud tiết kiệm điện nhiều hơn tiết kiệm tiền:** cho laptop-3 ngủ nửa thời gian tiết kiệm 16,2 kWh mỗi tháng, khoảng 51.000 đ và 11 kg CO₂. Hệ số lưới điện 0,681 kg CO₂/kWh là số năm 2024 do Đại học Bách khoa Hà Nội cùng Bộ Nông nghiệp và Môi trường ước tính ([Vietnam News, 18/4/2025](https://vietnamnews.vn/environment/1716087/study-shows-coal-power-dominant-as-viet-nam-s-grid-emissions-climbed-in-2024.html)). Con số này bằng 18% tiền điện nhưng chỉ 2,4% tổng chi phí.
5. **Quyết định giá:** giữ 20 credit/giờ cho s1.small. Mức này ngang DigitalOcean, cao hơn Viettel IDC, và hoà vốn ở mức lấp đầy dự kiến nếu tăng RAM cho WSL.

### Hạn chế

- Giá laptop, tiền mạng và tỷ giá là giả định.
- Công suất đo lúc chạy pin, khi máy còn bật màn hình và mở nhiều app. Vì vậy P_idle cao hơn của một node thật. Ngược lại, khi chạy pin máy giới hạn công suất CPU, nên P_max lúc cắm sạc cao hơn (xem [hardware.md](hardware.md)).
- Mức CPU 30% là giả định. Khi exporter chạy trên cả 3 laptop, thay bằng số đo thật.
- Tiền mạng tính trọn cho cụm, dù đường mạng dùng chung với cả nhà.

## Cách tính lại

1. Điện mỗi tháng (kWh) = Σ [P_idle + u × (P_max − P_idle)] ÷ 0,88 × 730 ÷ 1000.
2. Chi phí đầy đủ = Σ giá laptop ÷ 36 + kWh × giá điện + tiền mạng.
3. Số suất = Σ (RAM của WSL − 1 GiB) − 2 GiB, tính bằng GiB.
4. Giá vốn mỗi vCPU-giờ = chi phí ÷ (số suất × 730 × mức lấp đầy).
5. Mức lấp đầy hoà vốn = chi phí ÷ (số suất × 730 × giá bán mỗi giờ tính bằng đồng).
