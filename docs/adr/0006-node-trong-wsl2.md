# ADR-0006: Node chạy trong WSL2 trên Windows của từng laptop

- Trạng thái: Không thực hiện trong đồ án. Ngày 2026-11-26 nhóm quyết định không dựng cụm laptop-2 và laptop-3 vì không kịp hạn 28/11/2026, nên buổi thử dưới đây không diễn ra. Phần nhiều node chỉ được kiểm chứng trên cụm ảo 3 VM (`infra/lab`). Mục "Cập nhật 2026-11-26" ở cuối ghi những phần vẫn được dùng cho staging.
- Ngày: 2026-11-25
- Thay một phần [ADR-0003](0003-ha-tang-3-laptop.md): cách cài hệ điều hành và loại pool lưu trữ.

## Bối cảnh

- **Máy:** cả ba laptop là máy gaming cá nhân chạy Windows 11 Home, chủ máy dùng hằng ngày ([hardware.md](../hardware.md)).
- **Phần cứng:** nhóm không mua thêm gì. Trưởng nhóm có sẵn một SSD di động khoảng 500 GB.
- **Ổ trong còn ít chỗ:**
  - laptop-2 trống khoảng 78 GB (C: và D: cộng lại).
  - laptop-3 trống 76 GB ở D:, còn C: đã đầy.
  - Chỉ laptop-1 còn nhiều chỗ, nhưng trên ổ HDD (464 GB ở D:).
- **Quorum:** cụm 3 node cần ít nhất 2 node chạy thì mới quản lý được (đã thử trên cụm ảo). Vì vậy ít nhất 2 laptop phải chạy thường trực ở chỗ đặt cụm.
- **Kinh nghiệm sẵn có:** staging đã chạy trên Windows + WSL2 từ ngày 2026-11-25. Script 00–30 đã chạy được trên VM Ubuntu với Incus 7.6 và ZFS.
- **Nhân Linux của WSL** (6.6, WSL 2.5.9) không có ZFS, nhưng có btrfs, LVM thin, Open vSwitch và VXLAN. KVM chạy được: cụm ảo đã chạy VM bên trong WSL.
- **Driver lưu trữ:** trong một cụm Incus, mỗi pool dùng chung một driver cho mọi node.
- **Giới hạn ổ:** ngày 2026-11-25, tôi thử trong WSL với mỗi container giới hạn ổ 1 GiB:
  - Pool `dir` (staging đang dùng): ghi được 1,2 GB, tức giới hạn không có tác dụng. Container còn thấy cả ổ ảo 1 TB của WSL.
  - Pool btrfs: dừng ở khoảng 430 MB, đúng lúc tổng dung lượng chạm 1 GiB. Giới hạn có tác dụng.

## Các phương án

| Phương án | Ưu điểm | Nhược điểm |
| --- | --- | --- |
| A. Cài Ubuntu Server song song với Windows trên ổ trong | Giống server thật nhất: ZFS, suspend và Wake-on-LAN của Linux, chạy đúng script 00–30 | Phải thu nhỏ phân vùng trên ổ gần đầy, có rủi ro mất dữ liệu; cụm chạy 24/7 nên chủ máy không dùng được Windows |
| B. Ubuntu Server trên SSD di động, khởi động từ USB | Không đụng ổ trong; Linux thật, có ZFS | Chỉ một SSD nên chỉ dùng cho một máy; máy đó không dùng được Windows trong lúc chạy cụm; tuột dây là mất node |
| **C. Windows + WSL2 (mạng mirrored) trên cả 3 máy** | Không cài lại máy nào; chủ máy vẫn dùng Windows; staging đang chạy kiểu này; dùng lại script 10–30 | Không có ZFS; WSL dừng khi Windows ngủ hoặc khởi động lại; cụm WSL trải trên nhiều máy chưa ai thử; điện đo được gồm cả Windows |
| D. laptop-1 chạy B, hai máy kia chạy C | Node ghim ổn định nhất | Cả cụm phải bỏ ZFS để dùng chung driver; hai kiểu vận hành cùng lúc; trưởng nhóm mất Windows trên laptop-1 |

## Quyết định

1. **Node:** chọn C. Cả 3 laptop chạy node trong WSL2 Ubuntu 24.04, với mạng WSL ở chế độ mirrored để Incus nghe trên IP LAN của Windows.
2. **Lưu trữ:** pool btrfs tên `local` (file loop bên trong WSL) thay cho ZFS. Không dùng pool `dir`, vì nó không giới hạn được dung lượng ổ của khách.
3. **Incus:** cả 3 máy cài cùng một bản từ kho Zabbly (script 10). Bản Incus 6.0 của Ubuntu trên staging sẽ được thay khi chuyển staging vào cụm.
4. **laptop-1:** dùng máy MSI (32 GB RAM, đang chạy staging), đặt cố định ở chỗ đặt cụm. Ở đó luôn có thêm ít nhất một máy nữa chạy thường trực.
5. **SSD di động:** gắn cố định vào laptop-1 và chứa đĩa ảo WSL của máy này (`wsl --manage <distro> --move <thư mục trên SSD>`).
   - Việc này trả lại khoảng 34 GB trên ổ C: của laptop-1, và cho node ghim chạy trên SSD thay vì ổ HDD chậm.
   - Nếu SSD đã định dạng NTFS và còn đủ chỗ, chỉ cần đặt file đĩa ảo lên đó, không phải xoá dữ liệu sẵn có. Nếu phải định dạng lại thì sao lưu dữ liệu trên SSD trước.
   - Hai máy kia đặt đĩa ảo WSL ở D: (laptop-2 trống khoảng 53 GB, laptop-3 trống 76 GB).
6. **Dự phòng là phương án D,** nếu buổi thử dưới đây hỏng: laptop-1 khởi động Ubuntu Server từ chính SSD đó, và cả cụm dùng btrfs.

## Thử trước khi chốt

Một buổi với 2 laptop cắm dây vào cùng một router. Nên dùng laptop-2 và laptop-3: laptop-1 đang chạy staging, mà bật mạng mirrored hay đưa nó vào cụm đều phải khởi động lại WSL của nó. Các bước chi tiết và bảng kết quả ở [buoi-thu-wsl.md](../buoi-thu-wsl.md).

1. Bật mạng mirrored trong WSL. Mở tường lửa Hyper-V cho cổng 8443 (cụm) và 20000–29999 (cổng công khai của app).
2. Dựng cụm 2 node bằng script 10, 20, 21, 30, dùng pool btrfs.
3. Tạo instance trên cả 2 máy. Mở app của máy này từ máy kia qua cổng công khai, và chuyển instance qua lại bằng evacuate rồi restore.
4. Khởi động lại Windows của máy thứ hai. WSL và Incus phải tự chạy lại, và máy phải tự vào lại cụm, không cần ai đăng nhập.
5. Cho máy thứ hai ngủ rồi đánh thức. Cụm phải chạy tiếp.

Đạt cả 5 bước thì chốt C. Hỏng ở bước 1–3 thì chuyển sang D. Hỏng ở bước 4–5 thì tìm cách khắc phục trước khi bỏ C.

## Hệ quả

- **Script:**
  - Script 20 và 21 cần chọn được driver (`POOL_DRIVER=btrfs`).
  - Cần thêm một script chuẩn bị WSL, chạy trong Linux: tên node trong `/etc/wsl.conf`, bật systemd, tắt interop và tắt tự gắn ổ Windows.
  - Cần thêm một script cho phía Windows: `.wslconfig` (mirrored, RAM cho WSL), tường lửa Hyper-V, không ngủ khi cắm sạc, tự chạy WSL khi Windows khởi động.
  - Cụm ảo (`infra/lab`) nên có thêm chế độ btrfs để giống cụm thật.
- **Bảo mật:** WSL mặc định thấy ổ C: của chủ máy (`/mnt/c`) và chạy được file `.exe` của Windows. Một container thoát được ra ngoài sẽ đụng tới file cá nhân của chủ máy. Vì vậy node phải tắt `automount` và `interop` trong `/etc/wsl.conf`, và repo phải nằm bên trong WSL thay vì trên `/mnt/c`.
- **Staging:** từng dùng pool `dir`, nên một khách có thể ghi đầy ổ ảo WSL, tức ghi đầy ổ C: của laptop-1. Ngày 2026-11-25 đã chuyển máy của khách sang pool btrfs `tenants` (tối đa 20 GiB), không chờ buổi thử.
- **Green cloud:**
  - Cho laptop ngủ nghĩa là cho Windows ngủ (Modern Standby); WSL dừng theo, giống bài thử `incus pause` trên cụm ảo.
  - Đánh thức bằng Wake-on-LAN tuỳ driver từng máy.
  - Điện đo được gồm cả phần Windows tiêu thụ.
- **OVN (vai trò 2):** nhân WSL có Open vSwitch nên vẫn làm được, nhưng chưa thử.
- **Báo cáo đồ án:** phải ghi rõ cụm chạy trong WSL2, không chạy thẳng trên phần cứng như ADR-0003 dự định, và giải thích vì sao.
- **Nếu không gom được 2 máy về một chỗ:** có thể nối 3 máy ở 3 nhà qua VPN (WireGuard). Cách này vẫn cần 2 máy chạy cùng lúc và có độ trễ cao hơn, nên chỉ dùng khi không còn cách nào khác.

## Cập nhật 2026-11-26

Cụm không được dựng, nhưng hai phần của quyết định này vẫn chạy trên staging (laptop-1, một node):

- **Pool btrfs:** máy của khách nằm trên pool btrfs `tenants` (20 GiB), nên giới hạn ổ có tác dụng.
- **Không ngủ, tự bật WSL:** `setup-windows.ps1 -SkipNetwork` tắt chế độ ngủ khi cắm sạc và tạo tác vụ `minicloud-wsl`, nhưng không bật mạng mirrored. Thử ngày 26/11: Windows khởi động lúc 09:29:06, tác vụ bật WSL lúc 09:29:37, trước khi có phiên đăng nhập nào; API trả lời từ 09:30:28. Như vậy bước 4 của buổi thử đã đạt trên một máy.

Nếu làm tiếp sau đồ án: làm buổi thử 2 máy trong [buoi-thu-wsl.md](../buoi-thu-wsl.md) rồi mới chốt C hay D.
