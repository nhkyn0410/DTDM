# Chạy thử với người dùng thật

Theo đề cương, nhóm mời 10–20 người dùng thật trong 2–3 tuần. Số liệu của đợt này là chương "báo cáo vận hành thử": sự cố và postmortem, SLA đạt được, khảo sát người dùng.

Người dùng đọc [hướng dẫn](https://cloud.vexenhanh.id.vn/console/huong-dan.html) và [điều khoản](https://cloud.vexenhanh.id.vn/console/dieu-khoan.html) ngay trên console. Mã nguồn của hai trang nằm ở `src/minicloud/console/`.

## Trước khi mời

- [ ] Triển khai bản mới nhất lên laptop-1: `sudo ./infra/node/50-install-control-plane.sh` (tự sao lưu database rồi mới nâng schema).
- [ ] Tự đi hết một vòng như người dùng mới, bằng điện thoại dùng 4G:
  1. Đăng ký.
  2. Tạo container, chạy hai lệnh mẫu "Cài web server nginx" và "Tạo trang chào".
  3. Mở địa chỉ HTTPS của máy.
  4. Xem sao kê, tải CSV.
  5. Dừng rồi xoá máy.
- [ ] Lập kênh hỗ trợ (nhóm chat) và lịch trực luân phiên trong nhóm.
- [ ] Dựng Uptime Kuma trên một máy **ngoài cụm**, kiểm tra `https://cloud-api.vexenhanh.id.vn/healthz` mỗi phút. Đây là số đo độ sẵn sàng cho SLA 99%. Trên máy có Docker (ví dụ laptop-2 đã có Docker Desktop):

  ```
  docker run -d --restart=always -p 3001:3001 -v uptime-kuma:/app/data --name uptime-kuma louislam/uptime-kuma:1
  ```

  Mở `http://localhost:3001`, tạo tài khoản quản trị, thêm monitor kiểu HTTP(s) tới địa chỉ trên với chu kỳ 60 giây, và thêm một monitor nữa cho app `hello`. Máy này phải luôn bật trong suốt đợt chạy thử; ngắt máy thì số liệu có khoảng trống.
- [ ] Lấy mã mời: `sudo grep INVITE_CODE /etc/minicloud/minicloud.env`. Chỉ gửi riêng cho người được mời.
- [ ] Cấp quyền admin cho người trực: `sudo minicloud-admin make-admin <email>` (người đó đăng ký trước).

## Tin mời mẫu

> Chào bạn, nhóm mình đang làm đồ án môn Điện toán đám mây: một cloud nhỏ chạy trên 3 laptop. Bạn dùng thử giúp nhóm trong [2 tuần, tới ngày …] nhé, hoàn toàn miễn phí.
>
> 1. Mở https://cloud.vexenhanh.id.vn và đăng ký với mã mời: [mã mời]
> 2. Làm theo hướng dẫn: https://cloud.vexenhanh.id.vn/console/huong-dan.html
>
> Bạn được 500 credit dùng thử; hết thì nhắn nhóm để nạp thêm. Cuối đợt nhóm gửi một khảo sát ngắn khoảng 3 phút. Gặp lỗi hay thắc mắc, nhắn vào [kênh hỗ trợ].

## Việc của người trực

| Khi nào | Việc | Lệnh hoặc API |
| --- | --- | --- |
| Mỗi sáng | Xem API, worker và tunnel còn chạy | `systemctl status minicloud-api traefik cloudflared` |
| Mỗi sáng | Xem lỗi trong 24 giờ qua | `journalctl -u minicloud-api --since yesterday \| grep -E "ERROR\|WARNING"` |
| Mỗi sáng | Xem laptop và tải | `GET /v1/admin/nodes`, `GET /v1/admin/green/plan` (trên `/docs`, bằng key admin) |
| Khi có người xin | Nạp thêm credit | `sudo minicloud-admin grant-credits <org_id> 500` |
| Khi có báo cáo lạm dụng | Gỡ địa chỉ công khai ngay, rồi mới tìm hiểu | `POST /v1/admin/instances/{id}/unpublish` |
| Khi có sự cố | Ghi vào nhật ký sự cố (mục dưới) | |
| Cuối mỗi tuần | Lưu số liệu | `sudo minicloud-admin stats > stats-$(date +%F).json` |

Dấu hiệu lạm dụng cần để ý: CPU của một máy ở 100% nhiều giờ liền, hoặc thẻ Điện năng cho thấy một tổ chức dùng nhiều W bất thường; hãy hỏi chủ máy trước khi dừng.

Lấy `org_id` của một người: trong audit log, hoặc nhờ họ mở `https://cloud-api.vexenhanh.id.vn/v1/orgs` sau khi bấm Authorize trên `/docs`.

## Nhật ký sự cố

Mỗi sự cố ghi một mục vào `docs/su-co/YYYY-MM-DD-ten-ngan.md`, viết ngay khi xử lý xong:

```markdown
# <Tên ngắn>

- Thời gian: bắt đầu …, phát hiện …, hết ảnh hưởng … (giờ Việt Nam)
- Ảnh hưởng: ai, chức năng nào, bao lâu
- Phát hiện nhờ: Uptime Kuma / người dùng báo / người trực
- Nguyên nhân gốc:
- Đã làm gì để khắc phục:
- Việc cần làm để không lặp lại (kèm người phụ trách):
```

Không đổ lỗi cho cá nhân; mục đích là sửa hệ thống.

## Số liệu cho báo cáo

`sudo minicloud-admin stats` in ra JSON gồm:

| Khoá | Ý nghĩa | Dùng cho |
| --- | --- | --- |
| `users`, `organizations`, `organizations_running_now` | Số người dùng, số tổ chức, số tổ chức đang có máy chạy | Chỉ tiêu "10–20 người dùng thật" |
| `create_seconds` | Thời gian từ lúc gọi API tạo máy tới khi máy chạy, p50 và p95, theo loại máy | Chỉ tiêu thời gian cấp phát (p95 dưới 15 giây với container, dưới 60 giây với VM) |
| `create_failed` | Số lần tạo máy thất bại | Độ tin cậy |
| `compute_hours`, `storage_hours`, `credits_charged` | Tổng giờ chạy, giờ lưu trữ, credit đã trừ | Doanh thu mô phỏng |
| `energy_wh`, `co2_grams` | Điện năng ước tính và CO₂ tương ứng | Green cloud |
| `commands_run` | Số lệnh người dùng chạy qua console | Mức độ dùng thật |

Độ sẵn sàng lấy từ Uptime Kuma. Thời gian cấp phát trong `stats` tính tới lúc Incus báo máy chạy, chưa phải tới lúc SSH được như chỉ tiêu ở mục 2 của đề cương; báo cáo cần ghi rõ điểm này.

## Khảo sát cuối đợt

Gửi bằng Google Form, khoảng 3 phút:

1. Bạn đã làm được những gì? (nhiều lựa chọn: đăng ký, tạo máy, chạy lệnh, đưa web lên Internet, xem sao kê, dùng API)
2. Dễ dùng tới mức nào? (1–5)
3. Tạo máy có nhanh như bạn mong đợi không? (1–5)
4. Bạn có gặp lỗi không? Mô tả ngắn.
5. So với cloud bạn từng dùng (AWS, Azure, DigitalOcean, VPS…), minicloud thiếu gì nhất?
6. Thông tin điện năng và CO₂ có làm bạn để ý hơn tới việc dừng máy không dùng không? (có / không / không để ý)
7. Bạn có muốn dùng tiếp nếu nhóm còn chạy không? (có / không)
8. Góp ý khác.
