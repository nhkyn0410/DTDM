# Kiểm tra laptop và cài phần mềm cho đồ án

File này viết cho agent lập trình (Codex, Claude Code…) chạy trên laptop của thành viên nhóm. Agent kiểm tra máy để làm node của cụm (laptop-2, laptop-3), điền kết quả vào [hardware.md](hardware.md), rồi cùng chủ máy cài phần mềm của đồ án. Người cũng làm theo bằng tay được.

Cách dùng: `git pull` để có bản mới nhất của repo, mở agent trong thư mục repo, rồi nhắn:

> Đọc docs/kiem-tra-laptop.md và làm theo cho máy này. Máy này là laptop-2.

## Agent cần làm

1. Kiểm tra phần cứng, điền dòng của máy này vào `docs/hardware.md` (bước 1 đến 3).
2. Đo điện, nếu chủ máy đồng ý rút sạc khoảng 5 phút (bước 4).
3. Cùng chủ máy cài phần mềm của đồ án (bước 5).
4. Báo lại cho chủ máy (bước 6).

## Quy tắc

- Hỏi chủ máy trước khi cài phần mềm, chạy lệnh cần quyền admin, hoặc khởi động lại máy. Nói rõ cài gì và để làm gì.
- Không đụng vào BIOS, phân vùng ổ đĩa, BitLocker hay thứ tự khởi động. Không cài Ubuntu đè lên Windows. Những việc đó chủ máy làm cùng trưởng nhóm theo [infra/README.md](../infra/README.md).
- Không chạy các script trong `infra/node/`. Chúng dành cho node chạy Ubuntu Server và do trưởng nhóm điều phối; script 50, 60, 61 chỉ chạy trên laptop-1.
- Không gõ mật khẩu thay chủ máy: mật khẩu Windows, mật khẩu Ubuntu trong WSL, đăng nhập GitHub.
- Không tắt Windows Defender hay tường lửa, không đổi cấu hình hệ thống để "sửa" một lệnh bị lỗi. Lệnh lỗi thì dừng và báo nguyên văn lỗi cho chủ máy.
- Nếu môi trường của agent không cho chạy lệnh cần mạng hoặc quyền admin, đưa đúng lệnh đó cho chủ máy tự chạy.
- Chỉ sửa `docs/hardware.md`: dòng của máy này trong hai bảng, và mục ghi chú của máy này. Không ghi số serial, khoá Windows, tên hay mật khẩu Wi-Fi.
- Không commit, không push. Cho chủ máy xem diff; chủ máy tự tạo nhánh và mở PR theo [CONTRIBUTING.md](../CONTRIBUTING.md).

## Bước 1: Xác định máy

Nếu lời nhắn chưa nói đây là laptop mấy, hỏi chủ máy. Trưởng nhóm phân số; dòng nào trong bảng phần cứng còn trống thì chưa có máy nhận.

Xem máy chạy hệ điều hành gì:

- **Windows**, kể cả khi agent đang chạy trong WSL: làm bước 2. Phần cứng mà WSL thấy là của máy ảo (RAM bị chia đôi, không có RAPL, MAC không phải MAC thật), nên phải gọi script của Windows.
- **Linux chạy thẳng trên máy:** làm theo mục [Máy chạy Linux](#máy-chạy-linux) ở cuối file, rồi sang bước 3.
- **macOS:** dừng ở đây. Báo chủ máy rằng Mac không làm node Incus được, và nhắn trưởng nhóm.

## Bước 2: Chạy script kiểm tra (Windows)

Trong PowerShell, ở thư mục repo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\check\check-laptop.ps1 -Node laptop-2
```

Nếu agent chạy trong WSL:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w infra/check/check-laptop.ps1)" -Node laptop-2
```

Script không cần quyền admin, không đổi gì trên máy, chạy khoảng 15 giây. Nó in ra một báo cáo Markdown và lưu bản giống hệt vào `%TEMP%\minicloud-kiem-tra-laptop-2.md`. Nếu chữ tiếng Việt in ra bị lỗi, đọc file đó. Báo cáo có bốn phần:

| Phần của báo cáo | Agent làm gì |
| --- | --- |
| Dòng cho bảng phần cứng | Thay dòng `laptop-2` trong bảng đầu của `docs/hardware.md` |
| Ghi chú cho docs/hardware.md | Thay dòng "Chưa kiểm tra." trong mục `### laptop-2` |
| Cần chú ý | Giải thích ngắn từng điểm cho chủ máy |
| Chi tiết | Không chép; dùng khi cần trả lời câu hỏi |

## Bước 3: Soát lại trước khi sửa file

- **IP:** máy đang ở nhà thì vẫn ghi IP hiện tại. IP sẽ đổi khi gom 3 máy về chung một router; lúc đó trưởng nhóm đặt IP cố định.
- **MAC:** phải là MAC của cổng mạng dây, vì Wake-on-LAN dùng nó. Không có cổng LAN thì ô này là "—"; đọc mục Cần chú ý cho chủ máy.
- **Ô "không rõ":** thử tìm bằng cách khác (`systeminfo`, Device Manager, tem dưới đáy máy). Không đoán.
- **Script lỗi giữa chừng:** gửi nguyên văn lỗi cho chủ máy và trưởng nhóm. Không tự sửa script.

## Bước 4: Đo điện (rút sạc, khoảng 5 phút)

Hỏi chủ máy trước. Chuẩn bị:

- Pin trên 40%, tốt nhất trên 80%.
- Đóng game, trình duyệt nặng, máy ảo.
- Để máy yên: không chạm bàn phím, không gập máy.

Chủ máy rút sạc, rồi chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\check\check-laptop.ps1 -Node laptop-2 -MeasurePower
```

Script in lại báo cáo phần cứng, rồi:

1. Chờ 30 giây cho máy ổn định.
2. Đo 2 phút lúc nhàn.
3. Chạy hết mọi luồng CPU trong 2 phút. Quạt kêu to là bình thường.

Nó in thêm một dòng cho bảng số đo điện năng, chỉ có hai ô "rút sạc". Chép hai số đó vào dòng `laptop-2` của bảng thứ hai, và chép dòng "Điều kiện" vào ghi chú của máy. Nếu script báo máy đang cắm sạc hoặc pin yếu, làm theo lời nó rồi chạy lại. Xong thì cắm sạc lại.

Các ô còn lại của bảng điện để trống:

- **Cắm sạc:** để trống, vì nhóm không có ổ cắm đo điện.
- **Thời gian ngủ, thời gian thức dậy:** đo sau khi máy chạy Ubuntu, bằng suspend và Wake-on-LAN ([ADR-0004](adr/0004-green-cloud.md)).

## Bước 5: Cài phần mềm

Xem mục "Công cụ" và "WSL" trong phần Chi tiết của báo cáo để biết máy đã có gì; chỉ cài thứ còn thiếu. Hỏi chủ máy trước mỗi mục.

### 5.1 Công cụ cho mọi thành viên

| Phần mềm | Để làm gì | Lệnh cài trên Windows |
| --- | --- | --- |
| Git | Lấy code, tạo nhánh, mở PR | `winget install --id Git.Git -e` |
| uv | Cài Python 3.12 và thư viện của dự án, chạy test | `winget install --id astral-sh.uv -e` |

Mở cửa sổ PowerShell mới để nhận PATH mới. Trong thư mục repo:

```powershell
uv sync
uv run pytest
```

`uv sync` tự tải Python 3.12 nếu máy chưa có. Không test nào được báo `failed`; có test `skipped` là bình thường. Thử app bằng `uv run uvicorn minicloud.main:create_app --factory`, rồi mở http://127.0.0.1:8000.

### 5.2 Incus trong WSL2 (máy Windows làm node)

Nhóm đang dùng cách này cho laptop-1 tạm. Nó cho máy một Incus thật để thử, trong lúc nhóm chưa quyết định có cài Ubuntu Server cho các laptop hay không.

Nếu máy này sẽ tham gia [buổi thử cụm WSL](buoi-thu-wsl.md), chỉ làm bước 1–3 dưới đây. Buổi thử tự cài Incus bản Zabbly; Incus cài trước bằng `apt` sẽ phải xoá đi.

1. Ô VT-x/AMD-V trong bảng phải là "Có, đã bật". Nếu chưa, dừng lại: chủ máy phải bật Virtualization trong BIOS.
2. Cài WSL và Ubuntu 24.04. Lệnh cần quyền admin (chủ máy mở PowerShell bằng "Run as administrator") và có thể phải khởi động lại máy:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

   Dùng đúng bản 24.04 để giống các node. Lần đầu mở, Ubuntu hỏi tên và mật khẩu; chủ máy tự gõ.

3. Mở Ubuntu (`wsl -d Ubuntu-24.04`) và kiểm tra systemd: `systemctl is-system-running` phải in `running` hoặc `degraded`. Nếu không, thêm hai dòng sau vào `/etc/wsl.conf`, chạy `wsl --shutdown` trên Windows, rồi mở lại Ubuntu:

   ```ini
   [boot]
   systemd=true
   ```

4. Cài Incus và công cụ tạo tải:

   ```bash
   sudo apt-get update
   sudo apt-get install -y incus stress-ng jq
   ```

5. Khởi tạo Incus bằng khối `incus admin init --preseed` trong mục "Cài một lần" của [dev-incus-wsl.md](dev-incus-wsl.md). Phần tạo chứng chỉ ngay sau đó chỉ cần khi chủ máy muốn chạy control plane với Incus thật. Không làm mục "Dùng WSL làm laptop-1 tạm": chỉ máy của trưởng nhóm chạy staging.
6. Thử tạo rồi xoá một container:

   ```bash
   sudo incus launch images:ubuntu/24.04 thu-ct
   sudo incus list
   sudo incus delete -f thu-ct
   ```

   Nếu `/dev/kvm` có trong WSL, thử thêm VM: `sudo incus launch images:ubuntu/24.04 thu-vm --vm`, rồi `sudo incus delete -f thu-vm`. Ghi vào ghi chú của máy: container chạy được không, VM chạy được không.

WSL tự tắt khi không còn cửa sổ nào dùng nó, kéo theo Incus dừng. Muốn Incus chạy thì để một cửa sổ Ubuntu mở.

### 5.3 Tuỳ vai trò của chủ máy

| Vai trò | Phần mềm thêm |
| --- | --- |
| 4. IAM và billing | Docker Desktop (`winget install --id Docker.DockerDesktop -e`), để chạy Keycloak bằng docker compose |
| 6. Green cloud và giám sát | Docker Desktop, để chạy Prometheus và Grafana: `docker compose -f deploy/docker-compose.dev.yml --profile monitoring up -d` |

Docker Desktop cần WSL2 và sẽ hỏi chủ máy chấp nhận điều khoản; để chủ máy tự bấm.

### 5.4 Khi nhóm chốt cài Ubuntu Server cho máy này

Chủ máy và trưởng nhóm làm theo [infra/README.md](../infra/README.md). Script 00 cài `stress-ng`, `ethtool`, `zfsutils-linux` và `chrony`; script 10 cài Incus. Agent chỉ giúp chuẩn bị:

- Nhắc chủ máy sao lưu dữ liệu.
- Nếu báo cáo nói BitLocker đang bật, nhắc lưu khoá khôi phục.
- Tra phím vào BIOS và boot menu theo model máy.

## Bước 6: Báo lại

Tóm tắt cho chủ máy:

- Dòng đã điền vào hai bảng, và các điểm cần chú ý.
- Phần mềm đã cài, phần còn thiếu, kết quả `uv run pytest`.
- Diff của `docs/hardware.md`. Chủ máy tạo nhánh (ví dụ `docs/hardware-laptop-2`) rồi mở PR. Nếu PR của laptop-2 và laptop-3 đụng nhau ở bảng, khi gộp giữ cả hai dòng.

## Máy chạy Linux

Nếu máy chạy Ubuntu hoặc Linux khác thẳng trên phần cứng, lấy số liệu bằng các lệnh sau. Chỉ `ethtool` và `dmidecode` cần `sudo`.

| Ô trong bảng | Lệnh |
| --- | --- |
| Model | `cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name` |
| CPU (nhân/luồng) | `lscpu`: dòng Model name; nhân = Core(s) per socket × Socket(s); luồng = CPU(s) |
| RAM | `free -g`; loại và tốc độ: `sudo dmidecode -t memory` |
| Ổ đĩa | `lsblk -d -o NAME,MODEL,SIZE,ROTA,TRAN` (ROTA=0 là SSD) |
| Cổng LAN, MAC | `ip -br link`: cổng tên `en…` là mạng dây; tốc độ và Wake-on-LAN: `sudo ethtool <tên-cổng>` |
| IP | `ip -br -4 addr` |
| VT-x/AMD-V | `grep -cw -e vmx -e svm /proc/cpuinfo` lớn hơn 0 là có; `ls /dev/kvm` có file là đã bật |
| RAPL | `ls /sys/class/powercap/` có `intel-rapl:0` là có |

Ghi thêm vào ghi chú kết quả của `cat /sys/power/mem_sleep`: có chữ `deep` là máy ngủ được kiểu S3.

Đo điện: rút sạc, đọc `cat /sys/class/power_supply/BAT*/power_now` (đơn vị µW) vài lần lúc nhàn, rồi vài lần trong lúc `stress-ng --cpu 0 --timeout 120s` đang chạy (bỏ 30 giây đầu). Máy không có `power_now` thì lấy `current_now` (µA) nhân `voltage_now` (µV) rồi chia 10^12 để ra W.
