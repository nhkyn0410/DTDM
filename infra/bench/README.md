# Thí nghiệm

Script cho các thí nghiệm ở mục 9 của đề cương. Kết quả đưa vào Chương 4 của báo cáo.

| Script | Thí nghiệm | Chạy ở đâu | Kết quả đã có |
| --- | --- | --- | --- |
| `provision.py` | Thời gian cấp phát: tạo rồi xoá N máy qua API, in p50 và p95 | Máy bất kỳ, gọi API công khai | Staging 26/11, 20 container: p50 6,7 giây, p95 7,4 giây, lâu nhất 7,6 giây. Máy ảo chưa chạy được vì WSL của laptop-1 thiếu `/dev/kvm` |
| `load.py` | Autoscaling: bắn tải vào địa chỉ của một nhóm máy | Máy bất kỳ | Staging 26/11, 20 luồng qua Cloudflare: nhóm lên 2 máy sau 88 giây, 3 máy sau 149 giây; 11 → 26 request mỗi giây. Hết tải: về 2 máy sau 4,5 phút, về 1 máy sau 3 phút nữa. Có bước chờ app trước khi chia tải: 0 request lỗi suốt 6 phút, 11 → 28 request mỗi giây |
| `noisy-neighbor.sh` | Hàng xóm ồn ào: B đo sysbench khi A chạy stress-ng, có và không có giới hạn CPU | Một node, bằng root | Staging 26/11 (12 CPU, 5 lần mỗi điều kiện, trung vị event/giây của B): một mình 3.374; A bị giới hạn `limits.cpu=1` 3.418 (không chậm đi); A không giới hạn 2.586 (chậm đi 23%) |
| `overcommit.sh` | Overcommit: 1:1, 2:1, 4:1 container trên cùng 2 CPU | Một node, bằng root | Staging 26/11 (sysbench 1 luồng mỗi container, 30 giây): 1:1 khoảng 3.400 event/giây, p95 0,32 ms; 2:1 khoảng 1.700, p95 4,3 ms; 4:1 khoảng 845, p95 12,3 ms |

Cách chạy:

```
uv run python infra/bench/provision.py --operator secrets/staging-operator.json --type container -n 20 --csv container.csv
uv run python infra/bench/load.py https://<địa chỉ nhóm máy> --workers 20 --seconds 300
sudo bash infra/bench/noisy-neighbor.sh 5 20 > noisy.csv
sudo bash infra/bench/overcommit.sh 30 2 > overcommit.csv
```

Hai script `.sh` tạo container tạm trong project `default` (pool `tenants`), cài `sysbench` và `stress-ng` từ kho của Ubuntu, rồi tự xoá container khi xong. Đừng chạy chúng cùng lúc với thí nghiệm khác trên cùng node, vì chúng tranh CPU với nhau.
