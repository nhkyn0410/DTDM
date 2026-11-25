# Buổi thử: cụm 2 laptop chạy trong WSL2

Buổi thử trả lời câu hỏi của [ADR-0006](adr/0006-node-trong-wsl2.md): hai laptop Windows có ghép được thành một cụm Incus khi node chạy trong WSL2 hay không, với mạng mirrored và pool btrfs. Kết quả quyết định nhóm chốt cách chạy node nào.

File này viết cho cả người lẫn agent (Codex, Claude Code…). Mỗi máy có một agent thì nhắn cho agent của máy đó:

> Đọc docs/buoi-thu-wsl.md và làm phần của máy A.

Máy kia nhắn tương tự, thay "máy A" bằng "máy B".

## Chuẩn bị

- **Máy:** 2 laptop không chạy staging, thường là laptop-2 (máy A, dựng cụm) và laptop-3 (máy B, vào sau). laptop-1 đang chạy staging nên chưa thử ở buổi này.
- **Mạng:** một router còn 2 cổng LAN trống và 2 dây mạng. Wi-Fi cũng thử được, nhưng kết quả trên Wi-Fi không thay được kết quả trên mạng dây.
- **Thời gian:** khoảng 3 giờ.
- **Trên mỗi máy:**
  - Chạy `git pull` để có bản mới nhất của repo.
  - Windows 11 22H2 trở lên, đã có WSL2 với distro Ubuntu 24.04 (mục 5.2 của [kiem-tra-laptop.md](kiem-tra-laptop.md)).
  - Cắm sạc suốt buổi.
  - Tạm dừng Windows Update: Settings > Windows Update > Pause updates.

## Quy tắc

- Hỏi chủ máy trước bước 1 và bước 2: hai bước này đổi cấu hình Windows và WSL. Mọi thay đổi phía Windows hoàn tác được bằng `setup-windows.ps1 -Undo`.
- `--reset-incus` xoá mọi instance Incus đang có trong WSL của máy đó. Chỉ dùng khi chủ máy đồng ý.
- Không làm các bước này trên laptop-1.
- Token vào cụm là bí mật: gửi bằng tin nhắn riêng và chỉ dùng được một lần.
- Ghi kết quả từng bước vào bảng ở cuối file. Lỗi thì chép nguyên văn.

## Bước 0: xem trước (mỗi máy)

Mở PowerShell bằng "Run as administrator", ở thư mục repo:

```powershell
powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Distro Ubuntu-24.04 -WhatIf
```

Script chỉ in ra những gì sẽ đổi. Đọc cho chủ máy nghe, và để ý dòng "Mạng đang nối".

## Bước 1: cấu hình Windows (mỗi máy)

Ổ C: của laptop-2 và laptop-3 còn ít chỗ, nên chuyển luôn đĩa ảo WSL sang ổ D: (`-MoveDistroTo`):

```powershell
powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Distro Ubuntu-24.04 -MoveDistroTo D:\WSL\Ubuntu-24.04
wsl --shutdown
```

Script làm 4 việc:

- bật mạng mirrored;
- mở cổng 8443 và 20000–29999 trên tường lửa Hyper-V, chỉ với mạng Private;
- khi cắm sạc thì không ngủ và gập máy không làm gì;
- tạo tác vụ tự bật WSL khi Windows khởi động.

Ở mạng LAN của buổi thử, chuyển mạng sang Private: Settings > Network & internet > (mạng đang nối) > Private network.

## Bước 2: chuẩn bị WSL (mỗi máy)

```powershell
wsl -d Ubuntu-24.04 -u root
```

Trong WSL, ở thư mục repo trên ổ Windows (ví dụ `/mnt/c/Users/<tên>/DTDM`):

```bash
bash infra/wsl/setup-wsl.sh prepare laptop-2   # máy B: laptop-3
```

Lệnh này làm 3 việc: đặt tên node, bật systemd, cài `btrfs-progs`, và chép repo vào `/root/minicloud`. Nếu nó báo Incus đã có cấu hình, hỏi chủ máy, rồi chạy lại kèm `--reset-incus`.

Trên Windows chạy `wsl --shutdown`, rồi mở lại WSL bằng `wsl -d Ubuntu-24.04 -u root`. Kiểm tra:

```bash
bash /root/minicloud/infra/wsl/setup-wsl.sh status
```

Kết quả phải có:

- tên máy đúng;
- `init: systemd`;
- mục IPv4 có đúng IP LAN của Windows (xem bằng `ipconfig` trên Windows). Ghi lại IP này.

Rồi cài Incus từ kho Zabbly:

```bash
cd /root/minicloud/infra/node && ./10-install-incus.sh
```

## Bước 3: dựng cụm (ứng với bước thử 1–2 của ADR)

Máy A:

```bash
POOL_DRIVER=btrfs POOL_SIZE=40GiB ./20-bootstrap-cluster.sh laptop-2 <IP LAN của máy A>
incus cluster add laptop-3        # in ra token cho máy B
```

Máy B:

```bash
POOL_DRIVER=btrfs POOL_SIZE=40GiB ./21-join-cluster.sh laptop-3 <IP LAN của máy B> <token>
```

**Đạt** khi `incus cluster list` trên cả hai máy thấy 2 node ONLINE.

Nếu máy B không vào được cụm:

- Kiểm tra máy B mở được `https://<IP máy A>:8443` (trình duyệt báo lỗi chứng chỉ là bình thường; báo không kết nối được là chưa thông).
- Xem lại hai việc ở bước 1: mạng đã là Private chưa, và luật tường lửa đã có chưa.

## Bước 4: app qua cổng công khai, rồi evacuate (ứng với bước thử 3)

Trên máy A, trong WSL:

```bash
cd /root/minicloud/infra/wsl
bash spike.sh apps      # mỗi node một container có web
bash spike.sh probe     # gọi từng app qua IP LAN của node đang chạy nó
```

Từ trình duyệt trên Windows của máy kia, mở `http://<IP>:<cổng>` của container chạy trên máy này. Cổng lấy từ `spike.sh status`. Phải thấy `hello from spike-…`.

```bash
bash spike.sh evacuate laptop-3   # container của máy B chuyển sang máy A
bash spike.sh restore laptop-3
```

**Đạt** khi mọi dòng `probe` là ĐẠT, kể cả lúc mở từ trình duyệt của máy kia. Ghi lại thời gian evacuate và restore mà script in ra.

## Bước 5: khởi động lại máy B (ứng với bước thử 4)

1. Khởi động lại Windows của máy B, và **không đăng nhập**.
2. Trên máy A, cứ 30 giây chạy `incus cluster list` một lần, cho tới khi laptop-3 ONLINE.
3. Chạy `bash spike.sh probe`.

**Đạt** khi laptop-3 ONLINE trong vòng 5 phút mà không ai đăng nhập. Nếu nó chỉ ONLINE sau khi đăng nhập, ghi lại: tác vụ tự bật WSL không chạy được khi chưa đăng nhập.

## Bước 6: cho máy B ngủ rồi đánh thức (ứng với bước thử 5)

1. Trên máy B: Start > Power > Sleep.
2. Trên máy A: `incus cluster list` phải báo laptop-3 OFFLINE sau khoảng 30 giây, và `bash spike.sh status` vẫn chạy. Cụm 2 node vẫn quản lý được khi máy B ngủ, vì máy B không giữ phiếu bầu của database.
3. Đánh thức máy B bằng nút nguồn. Đo thời gian tới khi laptop-3 ONLINE, rồi chạy `bash spike.sh probe`.

## Bước 7: harden (mỗi máy)

```bash
bash /root/minicloud/infra/wsl/setup-wsl.sh harden
```

Trên Windows chạy `wsl --shutdown`, rồi mở lại WSL. Chạy `setup-wsl.sh status`: phải thấy "không thấy" ở dòng ổ Windows. Sau đó chạy `bash spike.sh probe`; mọi app vẫn phải ĐẠT.

## Dọn dẹp

Chạy `bash spike.sh clean` để xoá các container thử.

Nếu nhóm chốt cách chạy node này thì giữ nguyên cụm. Nếu không, trên mỗi máy:

1. Trong WSL: `bash /root/minicloud/infra/wsl/setup-wsl.sh unharden`.
2. Trên Windows (PowerShell admin): `powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Undo`, rồi `wsl --shutdown`.
3. Muốn xoá hẳn Incus khỏi WSL thì chạy `setup-wsl.sh prepare <tên node> --reset-incus`.

## Kết quả (điền sau buổi thử)

| Bước | Đạt? | Thời gian | Ghi chú, lỗi nguyên văn |
| --- | --- | --- | --- |
| 3. Dựng cụm 2 node | | | |
| 4. App mở được từ máy kia | | | |
| 4. Evacuate rồi restore | | | |
| 5. Khởi động lại, không đăng nhập | | | |
| 6. Ngủ rồi đánh thức | | | |
| 7. App vẫn chạy sau khi harden | | | |

Gửi bảng này cho trưởng nhóm.

- **Đạt hết bước 3–7:** chốt ADR-0006.
- **Hỏng ở bước 3 hoặc 4** (dựng cụm, mở app từ máy kia): chuyển sang phương án D của ADR.
- **Hỏng ở bước 5–7:** ghi lại để tìm cách khắc phục. Đây chưa phải lý do bỏ cách chạy trong WSL.
