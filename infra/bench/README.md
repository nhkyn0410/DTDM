# Thí nghiệm

Script cho các thí nghiệm ở mục 9 của đề cương. Kết quả đưa vào Chương 4 của báo cáo.

| Script | Thí nghiệm | Chạy ở đâu | Kết quả đã có |
| --- | --- | --- | --- |
| `provision.py` | Thời gian cấp phát: tạo rồi xoá N máy qua API, in p50 và p95 | Máy bất kỳ, gọi API công khai | Staging 26/11, 20 container: p50 6,7 giây, p95 7,4 giây, lâu nhất 7,6 giây. Máy ảo chưa chạy được vì WSL của laptop-1 thiếu `/dev/kvm` |
| `load.py` | Autoscaling: bắn tải vào địa chỉ của một nhóm máy | Máy bất kỳ | Staging 26/11, 20 luồng qua Cloudflare: nhóm lên 2 máy sau 88 giây, 3 máy sau 149 giây; 11 → 26 request mỗi giây. Hết tải: về 2 máy sau 4,5 phút, về 1 máy sau 3 phút nữa. Có bước chờ app trước khi chia tải: 0 request lỗi suốt 6 phút, 11 → 28 request mỗi giây |
| `density.sh` | Mật độ: tạo container cấu hình s1.small theo từng đợt, tới khi hết RAM hoặc chạm giới hạn khác | Một node, bằng root | Staging 26/11 (WSL 15,8 GB RAM, 12 luồng, container Ubuntu chưa chạy app). Lần 1 dừng ở 87 container dù RAM còn trống 11 GB: các container chia nhau giới hạn 1024 inotify instance, nên systemd trong container thứ 88 không chạy được. Đã nâng giới hạn theo hướng dẫn production của Incus, đưa vào `10-install-incus.sh`. Lần 2 chạy đủ 200 container với 8,2 GB RAM, khoảng 35 MiB mỗi container. Một đợt 20 container khởi động xong trong 32 giây khi node trống, 144 giây khi đã có 180 container. Từ khoảng container thứ 130, `sync()` trong WSL bắt đầu treo, nên 67 container không dừng được và phải khởi động lại WSL (xem [docs/dev-incus-wsl.md](../../docs/dev-incus-wsl.md)). Trên WSL, giới hạn thực tế vì vậy thấp hơn con số RAM cho phép |
| `noisy-neighbor.sh` | Hàng xóm ồn ào: B đo sysbench khi A chạy stress-ng, có và không có giới hạn CPU | Một node, bằng root | Staging 26/11 (12 CPU, 5 lần mỗi điều kiện, trung vị event/giây của B): một mình 3.374; A bị giới hạn `limits.cpu=1` 3.418 (không chậm đi); A không giới hạn 2.586 (chậm đi 23%) |
| `overcommit.sh` | Overcommit: 1:1, 2:1, 4:1 container trên cùng 2 CPU | Một node, bằng root | Staging 26/11 (sysbench 1 luồng mỗi container, 30 giây): 1:1 khoảng 3.400 event/giây, p95 0,32 ms; 2:1 khoảng 1.700, p95 4,3 ms; 4:1 khoảng 845, p95 12,3 ms |
| `rto.sh` | Chịu lỗi: giết control plane bằng `SIGKILL`, đo thời gian sống lại và số request lỗi của app khách | Node chạy control plane, bằng root | Staging 26/11, 5 lần: `/healthz` trả lời lại sau trung vị 5,5 giây (4,9–6,8), trong đó 3 giây là `RestartSec` của systemd; lệnh API thật (đọc database và hỏi Incus) sau 6,0 giây (5,4–7,5). App của khách qua Traefik nhận 313 request trong lúc đó, không request nào lỗi |

Cách chạy:

```
uv run python infra/bench/provision.py --operator secrets/staging-operator.json --type container -n 20 --csv container.csv
uv run python infra/bench/load.py https://<địa chỉ nhóm máy> --workers 20 --seconds 300
sudo bash infra/bench/noisy-neighbor.sh 5 20 > noisy.csv
sudo bash infra/bench/overcommit.sh 30 2 > overcommit.csv
sudo bash infra/bench/density.sh 20 200 3072 > density.csv
sudo MINICLOUD_API_KEY=<key của operator> bash infra/bench/rto.sh <tên miền của một app> 5 > rto.csv
```

Hai script `.sh` tạo container tạm trong project `default` (pool `tenants`), cài `sysbench` và `stress-ng` từ kho của Ubuntu, rồi tự xoá container khi xong. Đừng chạy chúng cùng lúc với thí nghiệm khác trên cùng node, vì chúng tranh CPU với nhau.
