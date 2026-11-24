# ADR-0001: Dùng Incus làm data plane

- Trạng thái: Đề xuất, chờ cả nhóm duyệt ở mốc 1
- Ngày: 2026-11-25

## Bối cảnh

Nhóm cần một lớp ảo hoá chạy trên 3 laptop để cấp container và VM cho khách. Giảng viên không bắt buộc OpenStack. Giá trị của đồ án nằm ở control plane (API, đa người thuê, tính phí, co giãn, Green cloud). Vì vậy lớp dưới phải nhẹ, có API tốt và tốn ít công vận hành.

## Các phương án

| Phương án | Ưu điểm | Nhược điểm |
| --- | --- | --- |
| OpenStack | Có đủ dịch vụ (Keystone, Neutron, Heat, CloudKitty) | Nặng (bản all-in-one nên có từ 16 GB RAM), cài mất nhiều tuần, nhóm chỉ còn vai người vận hành |
| Proxmox VE | Phổ biến, giao diện tốt, có HA | Đa người thuê yếu (chỉ có pool và phân quyền), API thiên về quản trị nội bộ |
| **Incus** | Nhẹ, REST API rõ ràng; `project` có `restricted` và `limits.*`; chạy cụm; chạy cả container lẫn VM; có live migration | Ít tài liệu tiếng Việt; mạng riêng theo tenant cần học OVN |
| k3s + KubeVirt | Hợp với PaaS | VM là công dân hạng hai, phức tạp nếu bán IaaS |

## Quyết định

- Dùng cụm Incus 3 node, mỗi laptop một node.
- Mỗi project của khách ánh xạ 1:1 sang một Incus project tên `mc-<id>`, có `restricted=true` và `limits.*` bằng quota của project.
- Quota vì thế được kiểm tra hai lần: ở API (`services/instances.py`) và ở cụm.
- Control plane chỉ nói chuyện với REST API của Incus qua chứng chỉ client, không SSH vào laptop.

## Hệ quả

- Bán được hai hạng máy: system container (rẻ, khởi động vài giây) và VM KVM (cô lập mạnh, giá gấp 1,5 lần).
- Trước khi có OVN (vai trò 2, tuần 3–5), mọi instance dùng chung mạng `incusbr0` có NAT, nên tenant chưa có VPC riêng.
- Muốn đổi data plane sau này chỉ cần viết driver mới theo `src/minicloud/drivers/base.py`.
