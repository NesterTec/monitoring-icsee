# WireGuard — resumo

**Procedimento completo:** [docs/DEPLOY.md](../../docs/DEPLOY.md) (seção WireGuard)  
**Diagrama:** [docs/html/fluxo-deploy.html](../../docs/html/fluxo-deploy.html)

Objetivo: a VPS alcança as câmeras de cada site via `10.20.n.0/24` (NETMAP → LAN real). Preferir baixar o `.conf` pelo painel (**Localidade** / Admin).

## Templates / scripts

- `wg0-server.conf.example` — VPS (`10.10.10.1`)
- `wg0-client-home.conf.example` — peer legado (LAN anunciada direto)
- `wg0-client-site.conf.example` — cliente multi-site com NETMAP
- `apply-peer.sh` — aplica peer no `wg0` do host (`WG_APPLY_CMD`)

## Teste rápido

```bash
# cliente
ping -c2 10.10.10.1

# VPS
ping -c2 10.10.10.N
nc -vz 10.20.N.3 554
```

`PostUp` do cliente: `NETMAP` vpn→lan + MASQUERADE na iface LAN ativa (`enp…` / `wlx…`).

Env do NVR na VPS:

```env
PUBLIC_BASE_PATH=/adm/house/monitor
GO2RTC_PUBLIC_URL=/adm/house/monitor/rtc
WG_SERVER_PUBLIC_KEY=...
WG_SERVER_ENDPOINT=<IP>:51820
```
