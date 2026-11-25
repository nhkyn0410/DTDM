# ADR-0003: Hạ tầng gồm 3 laptop chạy thẳng Ubuntu Server 24.04

- Trạng thái: Đề xuất, chờ cấu hình thật của 3 laptop. Phần hệ điều hành và lưu trữ được đề xuất thay bằng [ADR-0006](0006-node-trong-wsl2.md) (node chạy trong WSL2, pool btrfs).
- Ngày: 2026-11-25

## Bối cảnh

- Phần cứng của nhóm là 3 laptop.
- Windows 11 Home không có Hyper-V Manager; ảo hoá lồng nhau qua VirtualBox thì chậm và chập chờn.
- Laptop có những tật mà server không có: gập máy thì ngủ, Wi-Fi không bridge được, pin chai khi cắm sạc liên tục.
- Mạng nhà hoặc ký túc xá thường không có IP public (CGNAT).

## Quyết định

- **Hệ điều hành:** cài thẳng Ubuntu Server 24.04 LTS lên 3 laptop, đặt tên `laptop-1`, `laptop-2`, `laptop-3`. Nếu là máy cá nhân thì cài dual-boot, nhưng giai đoạn chạy thử và demo phải dành riêng cho cụm.
- **Chống tật laptop:** gập máy không ngủ, giới hạn mức sạc nếu máy hỗ trợ (`infra/node/00-prepare-laptop.sh`).
- **Mạng:** nối dây qua một switch, Wi-Fi chỉ dùng dự phòng. IP cố định bằng DHCP reservation trên router.
- **Lưu trữ:** pool ZFS tên `local` trên phân vùng riêng; nếu không cài lại được thì dùng file loop.
- **Không dùng Ceph:** quá nặng cho laptop. Màn rút điện ở buổi demo dùng app không trạng thái, tạo lại ở node khác.
- **laptop-1 là node ghim:** chạy control plane, PostgreSQL, Prometheus và Grafana, không bao giờ ngủ.
- **Cổng vào public:** một VPS nhỏ chạy WireGuard và Traefik, hoặc Cloudflare Tunnel (`infra/edge/README.md`).

## Hệ quả

- laptop-1 là điểm lỗi đơn của control plane. Chấp nhận ở mức demo, nhưng phải ghi rõ trong báo cáo và backup database ra ngoài cụm hằng ngày.
- Bộ nhớ là tài nguyên cạn trước: một laptop 16 GB chỉ cấp khoảng 15 GB cho instance (giữ 1 GB cho hệ điều hành).
- Cấu hình thật của từng máy ghi ở `docs/hardware.md`, để tính bảng giá và đặt tham số Green cloud.
