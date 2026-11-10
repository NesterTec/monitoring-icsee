# WireGuard — casa ↔ VPS (fase 1)

Objetivo: a VPS alcança as câmeras em `192.168.1.0/24` (ajuste a subnet se a sua for outra).

## 1. VPS (servidor)

```bash
sudo apt update && sudo apt install -y wireguard
umask 077
wg genkey | tee /etc/wireguard/server.key | wg pubkey > /etc/wireguard/server.pub
wg genkey | tee /etc/wireguard/home.key | wg pubkey > /etc/wireguard/home.pub
```

Copie `wg0-server.conf.example` → `/etc/wireguard/wg0.conf` e preencha as chaves + IP público da VPS na doc do cliente.

```bash
sudo sysctl -w net.ipv4.ip_forward=1
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-wireguard.conf
sudo ufw allow 51820/udp   # ou a porta escolhida
sudo systemctl enable --now wg-quick@wg0
```

## 2. Casa (cliente)

No PC que fica ligado:

```bash
sudo apt install -y wireguard
# copie wg0-client-home.conf.example → /etc/wireguard/wg0.conf
sudo sysctl -w net.ipv4.ip_forward=1
sudo systemctl enable --now wg-quick@wg0
```

O `PostUp` do exemplo faz MASQUERADE da LAN para a VPS alcançar `192.168.1.x`.

## 3. Teste

Na **VPS**:

```bash
nc -vz 192.168.1.5 554
# ou: ping -c2 192.168.1.5
```

Se TCP 554 abrir, o NVR na VPS pode usar os mesmos hosts das câmeras.

## 4. Docker NVR

Com `network_mode: host` (como no compose atual), o app herda as rotas do host incluindo `wg0`.

Env sugerido (além do `.env` usual):

```env
PUBLIC_BASE_PATH=/adm/house/monitor
GO2RTC_PUBLIC_URL=/adm/house/monitor/rtc
```
