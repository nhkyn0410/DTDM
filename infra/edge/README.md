# Cổng vào public

Vai trò 2 phụ trách. Mạng nhà hoặc ký túc xá thường không có IP public, nên cần một điểm vào nằm ngoài:

- **Phương án A (khuyên dùng):** một VPS nhỏ có IP public, chạy WireGuard và Traefik. VPS tới được cụm qua đường hầm WireGuard do laptop-1 chủ động mở, nên chạy được cả sau CGNAT.
- **Phương án B:** Cloudflare Tunnel. Không cần VPS, nhưng chỉ chuyển được HTTP(S) và phụ thuộc vào Cloudflare.

Tên miền trong ví dụ là `example.com`; thay bằng tên miền của nhóm.

## WireGuard

Tạo khoá trên mỗi máy: `wg genkey | tee private.key | wg pubkey > public.key`. Không commit khoá vào repo.

`/etc/wireguard/wg0.conf` trên VPS:

```ini
[Interface]
Address = 10.99.0.1/24
ListenPort = 51820
PrivateKey = <khoá riêng của VPS>

[Peer]
# laptop-1, kèm mạng LAN của cụm để VPS tới được cả 3 laptop
PublicKey = <khoá công khai của laptop-1>
AllowedIPs = 10.99.0.11/32, 192.168.1.0/24
```

`/etc/wireguard/wg0.conf` trên laptop-1:

```ini
[Interface]
Address = 10.99.0.11/24
PrivateKey = <khoá riêng của laptop-1>

[Peer]
PublicKey = <khoá công khai của VPS>
Endpoint = <IP public của VPS>:51820
AllowedIPs = 10.99.0.1/32
PersistentKeepalive = 25
```

Trên laptop-1, bật chuyển tiếp gói và NAT để VPS tới được laptop-2 và laptop-3. Thay `enp0s31f6` bằng tên cổng mạng dây của máy:

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/90-minicloud.conf && sudo sysctl --system
sudo iptables -t nat -A POSTROUTING -s 10.99.0.0/24 -o enp0s31f6 -j MASQUERADE
sudo systemctl enable --now wg-quick@wg0
```

## Traefik trên VPS

Chứng chỉ wildcard của Let's Encrypt cần thử thách DNS-01. Ví dụ dưới dùng Cloudflare làm DNS và cần biến môi trường `CF_DNS_API_TOKEN`.

`traefik.yml`:

```yaml
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint: { to: websecure, scheme: https }
  websecure:
    address: ":443"
certificatesResolvers:
  le:
    acme:
      email: <email-của-nhóm>
      storage: /letsencrypt/acme.json
      dnsChallenge:
        provider: cloudflare
providers:
  file:
    directory: /etc/traefik/dynamic
    watch: true
```

`dynamic/api.yml` đưa API ra `api.example.com`:

```yaml
http:
  routers:
    api:
      rule: Host(`api.example.com`)
      entryPoints: [websecure]
      service: api
      tls:
        certResolver: le
        domains:
          - main: example.com
            sans: ["*.example.com", "*.apps.example.com"]
  services:
    api:
      loadBalancer:
        servers:
          - url: http://10.99.0.11:8000
```

App của khách sẽ có dạng `<instance>.apps.example.com`. Control plane sẽ sinh thêm file trong `dynamic/` cho từng instance, trỏ tới `<IP LAN của node>:<cổng proxy>`; xem mục "Đưa app của khách ra Internet" trong [kiến trúc](../../docs/architecture.md).

## Kiểm tra

- Từ VPS: `ping 10.99.0.11` và `curl http://192.168.1.12:<cổng>` đều phải tới được.
- Từ điện thoại dùng 4G: mở `https://api.example.com/healthz` và thấy `"status": "ok"`.
