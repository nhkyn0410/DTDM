# Chạy Incus thật trong WSL2 để phát triển

Ai có Windows 11 với WSL2 (Ubuntu 24.04) đều dựng được một Incus một node, để chạy control plane với driver thật mà không phải chờ cụm 3 laptop. Ngày 2026-11-25, cách này đã giúp phát hiện và sửa hai lỗi mà test giả lập không thấy:

- **Project `restricted` cần profile riêng:** lỗi Incus trả về là "Projects without their own profiles cannot be restricted".
- **Máy đơn không nhận `target`:** lỗi Incus trả về là "Target only allowed when clustered".

Phần chỉ có ở cụm nhiều node (chọn laptop, danh sách thành viên, chuyển instance) vẫn phải thử trên laptop thật.

## Cài một lần

Trong WSL (`wsl -d Ubuntu`):

```bash
sudo apt-get update && sudo apt-get install -y incus
# Nếu báo "dpkg was interrupted": sudo dpkg --configure -a, rồi cài lại.
sudo incus admin init --preseed <<'EOF'
config:
  core.https_address: "[::]:8443"
networks:
- name: incusbr0
  type: bridge
  config:
    ipv4.address: auto
    ipv4.nat: "true"
    ipv6.address: none
    # Truy vấn DNS ngược do dnsmasq tự trả lời, không chuyển lên trên (xem "Sự cố đã gặp")
    raw.dnsmasq: |-
      local=/in-addr.arpa/
      local=/ip6.arpa/
storage_pools:
- name: local
  driver: dir
profiles:
- name: default
  devices:
    root: {path: /, pool: local, type: disk}
    eth0: {name: eth0, network: incusbr0, type: nic}
EOF
```

Kernel của WSL không có ZFS nên pool dùng kiểu `dir`; tên pool vẫn là `local` để khớp cấu hình mặc định. Pool `dir` không áp được giới hạn dung lượng ổ của container (đã thử), nên chỉ hợp để phát triển. Cụm thật dùng btrfs theo [ADR-0006](adr/0006-node-trong-wsl2.md).

Tạo chứng chỉ cho control plane, lưu vào thư mục `secrets/` của repo (đã có trong `.gitignore`):

```bash
cd /mnt/c/Code/DTDM   # đổi theo chỗ bạn clone repo
mkdir -p secrets
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -sha384 -nodes \
  -keyout secrets/client.key -out secrets/client.crt -days 3650 -subj "/CN=minicloud-dev"
sudo incus config trust add-certificate secrets/client.crt --name minicloud-dev
sudo cp /var/lib/incus/server.crt secrets/server.crt
```

## Mỗi lần dùng

WSL tự tắt khi không còn lệnh nào chạy, kéo theo Incus dừng. Mở một cửa sổ `wsl -d Ubuntu` và để nguyên trong lúc làm việc; chạy `incus list` một lần để đánh thức Incus.

Tạo file `.env` ở gốc repo trên Windows:

```
MINICLOUD_DRIVER=incus
MINICLOUD_INCUS_URL=https://[::1]:8443
MINICLOUD_INCUS_CLIENT_CERT=secrets/client.crt
MINICLOUD_INCUS_CLIENT_KEY=secrets/client.key
MINICLOUD_INCUS_CA_CERT=secrets/server.crt
MINICLOUD_PUBLIC_DOMAIN=vexenhanh.id.vn
MINICLOUD_INGRESS_ROUTES_FILE=dev-routes/minicloud.yml
```

Phải dùng `[::1]` chứ không dùng `127.0.0.1`: WSL chỉ chuyển cổng của Incus sang Windows qua địa chỉ IPv6 loopback. Vì lý do đó, cổng proxy của instance cũng đi qua `[::1]`; control plane tự lấy địa chỉ này từ `MINICLOUD_INCUS_URL`.

Sau đó chạy `uv run uvicorn minicloud.main:create_app --factory --reload` như bình thường.

## Kiểm tra nhanh

1. Mở http://127.0.0.1:8000/docs, đăng ký rồi tạo một instance.
2. Chờ instance chạy. Lần đầu mất khoảng một phút để tải image; các lần sau chỉ vài giây.
3. Trong WSL, chạy `incus list --all-projects` để thấy instance nằm trong project `mc-…`.
4. Đọc tuyến trong `dev-routes/minicloud.yml`. Nếu trong container có web server ở cổng 80, mở địa chỉ `http://[::1]:<cổng>` ghi trong file đó sẽ thấy trang của nó.

## Dùng WSL làm laptop-1 tạm (staging trên Internet)

Phần này dựng y như laptop-1 thật, bằng chính các script trong `infra/node`. Cả nhóm vì vậy dùng được `https://cloud-api.vexenhanh.id.vn` trước khi có cụm 3 laptop.

Giữ WSL chạy nền. Máy tính cũng phải bật và không được ngủ. Cách bền nhất là chạy một lần, trong PowerShell mở bằng "Run as administrator":

```powershell
powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Distro Ubuntu -SkipNetwork
```

Lệnh này làm hai việc:

- Khi cắm sạc, máy không ngủ, không ngủ đông, và gập máy không làm gì. Trước đó laptop-1 ngủ sau 1 giờ và ngủ đông sau 15 phút; staging đã ngừng 30 phút vì máy ngủ.
- Tạo tác vụ `minicloud-wsl`: Windows vừa khởi động là bật WSL, không cần ai đăng nhập. Đã thử ngày 26/11/2026: Windows khởi động lúc 09:29:06, tác vụ bật WSL lúc 09:29:37 (trước khi có phiên đăng nhập nào), API trả lời từ 09:30:28. Khoảng 6,5 phút staging ngừng trong lần khởi động lại đó không bị tính tiền (394 giây-máy).

`-SkipNetwork` giữ nguyên mạng của WSL, nên staging không phải khởi động lại. Thêm `-WhatIf` để xem trước; dùng `-Undo` để trả lại như cũ. Máy vẫn ngủ khi chạy pin.

Task Scheduler có thể ghi Last Run Result của `minicloud-wsl` là `0x800710E0`. Đó không phải lỗi: lúc đăng nhập, tác vụ được kích hoạt lần nữa và bị bỏ qua vì WSL đã chạy từ lúc khởi động. Muốn biết tác vụ có đang giữ WSL không, chạy `Get-Process wsl`: phải có tiến trình với `SessionId` bằng 0.

Khi chưa chạy lệnh trên, hoặc sau một lần `wsl --shutdown`, giữ WSL bằng:

```powershell
powershell -ExecutionPolicy Bypass -File infra\wsl\keep-wsl-alive.ps1
```

Cài các dịch vụ, chạy trong WSL với quyền root, thư mục repo trên ổ C:

```bash
cd /mnt/c/Code/DTDM
sudo ./infra/node/50-install-control-plane.sh vexenhanh.id.vn   # control plane + mã mời
sudo ./infra/node/60-install-ingress.sh                         # Traefik + cloudflared
sudo ./infra/node/61-setup-tunnel.sh minicloud-wsl vexenhanh.id.vn
```

Đặt tên tunnel là `minicloud-wsl`; laptop-1 thật sẽ dùng `minicloud`. Khi chạy script 61 trên laptop-1, `--overwrite-dns` tự chuyển các tên máy từ tunnel WSL sang tunnel của laptop-1.

Máy của khách trên staging nằm ở pool btrfs `tenants`, nơi giới hạn ổ của từng máy có tác dụng. Pool này dùng một file loop 20 GiB, chỉ chiếm chỗ thật khi có dữ liệu, nên khách không thể ghi tràn ổ C:. Pool `local` (dir) chỉ còn dùng cho cụm ảo. Dựng lại staging từ đầu thì làm thêm:

```bash
sudo apt-get install -y btrfs-progs
sudo incus storage create tenants btrfs size=20GiB
echo "MINICLOUD_INCUS_STORAGE_POOL=tenants" | sudo tee -a /etc/minicloud/minicloud.env
sudo systemctl restart minicloud-api
```

Muốn chuyển một instance có sẵn sang pool khác, phải ghi rõ project đích, nếu không Incus 6.0 chuyển nó sang project `default`:

```bash
sudo incus stop --project mc-… <tên>
sudo incus move --project mc-… --target-project mc-… <tên> --storage tenants
sudo incus start --project mc-… <tên>
```

Ngày 2026-11-25, thiếu `--target-project` khiến app `hello` và API của staging gián đoạn khoảng 3 phút.

Các lệnh hay dùng:

| Việc | Lệnh |
| --- | --- |
| Xem mã mời | `sudo grep INVITE /etc/minicloud/minicloud.env` |
| Cấp quyền admin | `sudo minicloud-admin make-admin <email>` |
| Cập nhật code sau khi sửa | chạy lại script 50 (tự sao lưu database rồi migrate) |
| Sao lưu ngay | `sudo minicloud-admin backup --dir /var/lib/minicloud/backups` |
| Xem dung lượng pool của khách | `sudo incus storage info tenants` |
| Đối chiếu ngay database với Incus | `POST /v1/admin/reconcile` với API key admin (worker vẫn tự làm mỗi phút) |
| Xem log | `journalctl -u minicloud-api -u traefik -u cloudflared -f` |
| Tắt hết | `wsl --terminate Ubuntu` (chạy trên Windows) |
| Gỡ tunnel thử | `cloudflared tunnel delete minicloud-wsl`, rồi xoá các bản ghi `cloud-api` và `*` trong dashboard Cloudflare |

### Sự cố đã gặp

- **WSL tự tắt làm staging sập.** WSL dừng distro khi không còn tiến trình `wsl.exe` nào, kéo theo API, Traefik, cloudflared và mọi container. Lần khởi động lại sau đó lại bật tất cả, nên nhìn từ ngoài, staging cứ chập chờn. Trên laptop chạy thật, `infra/wsl/setup-windows.ps1` đăng ký một tác vụ chạy `wsl.exe -d <distro> -u root --exec sleep infinity` mỗi khi Windows khởi động. Khi chưa chạy script đó, chạy `powershell -ExecutionPolicy Bypass -File infra\wsl\keep-wsl-alive.ps1`. Script này chạy ẩn đúng lệnh trên, và nó dừng khi Windows khởi động lại hoặc khi có ai chạy `wsl --shutdown`. Ngày 26/11, cửa sổ `wsl` giữ staging bị đóng khi khởi động lại WSL, và staging tắt bật liên tục trong khoảng 2 phút.
- **`sync()` treo nên container không dừng được (26/11/2026).** Thí nghiệm mật độ chạy tới 200 container. Từ khoảng container thứ 130, lệnh `sync()` bắt đầu treo trong kernel của WSL: `/proc/<pid>/stack` dừng ở `fuse_sync_fs`, chờ một filesystem FUSE của chính WSL trả lời (filesystem này không có trong `/proc/mounts` của distro). Lúc khởi động lần đầu, container chạy `systemd-machine-id-setup --commit`, lệnh này gọi `sync()`. Vì vậy 67 container bị kẹt, và `incus delete -f` cũng treo theo. Cách gỡ:
  1. Đặt `boot.autostart=false` cho các container kẹt.
  2. Chạy `wsl --shutdown`.
  3. Mở lại WSL rồi xoá các container đó.

  Sau đó `sync` chạy bình thường trở lại. Đừng chạy thí nghiệm mật độ trên node đang phục vụ người dùng.
- **`npm install` mất 5–8 phút trong container (26/11/2026).** Trong container trên staging, tra DNS ngược (PTR) cho IP công khai không ai trả lời: mỗi lần mất 10 giây (`getent hosts 104.16.10.34`). npm gọi `process.report.getReport()` để biết máy dùng glibc hay musl, và Node tra ngược tên của từng kết nối đang mở, đồng bộ trên luồng chính. `gdb` cho thấy npm đứng ở `getnameinfo` → `uv_getnameinfo` → `node::report::ReportEndpoint`. Nhờ vậy, lần triển khai Express mất 461 giây và Vite 477 giây. Đã sửa ở hai chỗ:
  1. **Buildpack:** dùng Node 22.14 với `NODE_OPTIONS=--report-exclude-network`; Node 22.11 bỏ qua cờ này. `npm install` của Express còn 7 giây.
  2. **Mạng của staging:** dnsmasq của `incusbr0` tự trả lời mọi truy vấn ngược: `incus network set incusbr0 raw.dnsmasq "$(printf 'local=/in-addr.arpa/\nlocal=/ip6.arpa/')"`. Lệnh này cũng có trong preseed của script 20 và ở mục "Cài một lần" bên trên.
     - Tra ngược 104.16.10.34 còn 3 ms; tên của container (`hello-0027e5.incus`) vẫn tra ngược được.
     - Khách tự chạy `npm install` (không cờ, cache trống) xong trong 6 giây.
     - Đổi lại, IP công khai không còn tra ra tên.
     - Lúc đặt, dnsmasq khởi động lại; IP của container không đổi.

## Cụm ảo 3 node

Trong lúc chờ 3 laptop, có thể dựng một cụm Incus 3 node ngay trong WSL. Cụm gồm 3 VM Ubuntu 24.04 tên `lab-1`, `lab-2`, `lab-3`, chạy đúng các script 00, 10, 20, 21, 30 của `infra/node` như trên laptop thật. Một control plane riêng ở cổng 8100 nối vào cụm này để làm smoke test.

Staging không bị đụng tới:

- Các VM nằm trong project Incus `lab`, còn reconciler của staging chỉ xem các project `mc-*`.
- Control plane thử có database, chứng chỉ và file tuyến riêng trong `/root/minicloud-lab`.

Cần chuẩn bị:

- **RAM:** khoảng 9 GiB trống trong WSL, mỗi VM 3 GiB.
- **Ổ đĩa:** khoảng 15 GB trống trên ổ chứa đĩa ảo của WSL, thường là C:. Đĩa ảo không tự co lại khi xoá VM. Ngày 2026-11-25, dựng cụm hai lần (lần sau để thử lại script) làm ổ C: của máy trưởng nhóm giảm khoảng 20 GB.
- **KVM lồng nhau:** trong WSL phải có `/dev/kvm`. Windows 11 bật sẵn.
- **QEMU:** gói `incus` của Ubuntu chưa kèm QEMU, nên lần đầu script tự cài QEMU rồi khởi động lại Incus. Container của staging vẫn chạy trong lúc đó.

Trong WSL, ở thư mục repo:

```bash
sudo bash infra/lab/lab.sh up          # tạo 3 VM, chạy script 00, 10, 20, 21, 30 (khoảng 5 phút)
sudo bash infra/lab/lab.sh cp          # control plane thử ở http://127.0.0.1:8100
sudo bash infra/lab/smoke.sh all       # bước 6–8 của smoke test, khoảng 1–2 phút
sudo bash infra/lab/lab.sh stop        # dừng để trả RAM; "start" để bật lại
sudo bash infra/lab/lab.sh destroy --yes
```

`lab.sh up` chép bản repo đang có trên máy, kể cả phần chưa commit, nên dùng được để thử script vừa sửa. Sửa code control plane thì chạy lại `lab.sh cp`. `smoke.sh` còn chạy được từng bước lẻ (`create`, `probe`, `evacuate`, `watch`, `audit`…); chạy không tham số để xem danh sách.

Cụm ảo không thay được laptop thật ở mấy chỗ: mạng LAN và switch thật, Wake-on-LAN, đo điện, VM của khách (sẽ thành VM lồng trong VM lồng trong WSL), và tốc độ ổ đĩa.

Lần chạy đầu, ngày 2026-11-25, dùng Incus 7.6 cài từ kho Zabbly như script 10. Nó tìm ra bốn lỗi, đều đã sửa:

- **Script không có quyền chạy:** các file `infra/node/*.sh` thiếu quyền chạy trong git, nên trên Linux, quên `chmod +x` là gặp lỗi "Command not executable". Quyền chạy giờ được lưu trong git.
- **Evacuate làm tắt app của khách:** mặc định, `incus cluster evacuate` chỉ dừng instance nằm trên ổ cục bộ, nên app tắt cho tới khi laptop được restore. Control plane giờ đặt `cluster.evacuate=migrate` cho mỗi instance mới. Evacuate vì vậy chuyển nguội instance sang laptop khác; reconciler ghi node mới trong vòng một phút, và URL công khai đi theo.
- **Incus tự chọn laptop đích khi evacuate,** không theo scheduler của minicloud. Lần thử, nó dồn 3 instance s1.small (3 GiB) vào một node chỉ có 2 GiB dành cho khách. Vì vậy Green cloud phải tự chuyển instance đi trước khi evacuate ([ADR-0004](adr/0004-green-cloud.md)).
- **`move_instance` trả về quá sớm:** hàm này (Green cloud dùng để gom tải) trả về lúc instance chưa có IP ở laptop mới. Giờ nó chờ DHCP như `start_instance`.

Thử quorum cùng ngày, giả lập laptop ngủ bằng `incus pause` VM và đánh thức bằng `incus resume`:

- **1 trong 3 node ngủ:** cụm vẫn quản lý bình thường. Instance mới tránh node đang ngủ; app trên node đó không trả lời.
- **2 trong 3 node ngủ:** mọi lệnh Incus báo `no available cowsql leader server found`. Reconciler bỏ qua lượt, lệnh tạo instance chuyển sang `error`, còn app trên node thức vẫn phục vụ.
- **Đánh thức một node:** cụm quản lý lại được sau khoảng 7 giây. Instance trên các node vừa ngủ chạy tiếp như cũ.

Lần thử này sửa thêm hai lỗi:

- Planner Green cloud từng đề xuất cho 2 trong 3 laptop ngủ. Giờ cụm từ 3 node chỉ cho tối đa một laptop ngủ.
- Khi cụm không trả lời, `/v1/admin/nodes` và `/v1/admin/green/plan` trả HTTP 500. Giờ chúng trả 503 với mã `cluster_unavailable`.
