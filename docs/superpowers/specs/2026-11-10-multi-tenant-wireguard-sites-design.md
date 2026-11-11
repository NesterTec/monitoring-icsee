# Multi-tenant WireGuard sites — design

**Date:** 2026-11-10  
**Status:** Implemented  
**Project path:** `/home/nestertec/Documentos/projects/icsee`

## Goal

Plataforma multi-usuário na VPS: superadmin provisiona usuários/localidades; cada usuário baixa um `.conf` WireGuard, mantém só o túnel (sem Docker local) e gerencia apenas as próprias câmeras.

## Decisions locked

| Topic | Choice |
|-------|--------|
| Cliente local | Só WireGuard (sem agente / sem Docker) |
| Colisão de LAN | Bloco VPN `/24` por site + `NETMAP` 1:1 |
| Papéis | Superadmin cria user+site; user baixa conf e CRUD câmeras |
| Stack | Um app + um go2rtc + um SQLite (isolamento por `site_id`) |
| Captura | Continua na VPS (RTSP via túnel) |

## User onboarding

1. Superadmin: **Admin** → cria e-mail/senha + localidade (LAN CIDR).
2. Sistema aloca `tunnel_ip` (`10.10.10.n`) e `vpn_cidr` (`10.20.n.0/24`), gera chaves, grava snippet em `WG_PEERS_DIR`.
3. Usuário: **Localidade** → Baixar WireGuard → instala `wg-quick@wg0` (PC ligado 24/7).
4. Usuário cadastra câmeras com **IP da LAN**; backend grava `host_lan` + `host` mapeado.
5. Live/gravação usam `host` (VPN); UI filtrada por sites do usuário.

## Data model

- `User.role`: `superadmin` | `user`
- `Site`: owner, `lan_cidr`, `vpn_cidr`, `tunnel_ip`, WG keys (private Fernet), `lan_iface`
- `Camera.site_id`, `host_lan`, `host` (alvo RTSP na VPS)

Seed: `ADMIN_EMAIL` vira superadmin; câmeras órfãs → site “Casa Nestertec”.

## Env (VPS)

```env
WG_SERVER_PUBLIC_KEY=...
WG_SERVER_ENDPOINT=VPS_IP:51820
WG_SERVER_TUNNEL_IP=10.10.10.1
WG_VPN_BASE=10.20.0.0/16
WG_PEERS_DIR=/data/wireguard/peers
# opcional no host:
# WG_APPLY_CMD=/usr/local/bin/icsee-apply-peer.sh
```

Script host: [`deploy/wireguard/apply-peer.sh`](../../../deploy/wireguard/apply-peer.sh).

## Cutover do peer atual (casa Nestertec)

1. Definir env WG_* na VPS; rebuild app.
2. Login superadmin → Admin → baixar **Peer servidor** do site padrão e aplicar no `wg0` (ou `apply-peer.sh`).
3. Baixar **Cliente** / Localidade → substituir `/etc/wireguard/wg0.conf` em casa (NETMAP).
4. Regenerar hosts das câmeras (download do conf já chama `apply_camera_host_mapping`) ou editar/salvar cada câmera.
5. Testar `nc -vz 10.20.1.x 554` na VPS e Live no painel.

## Out of scope

- Auth no go2rtc `/rtc` (IDs não vazam na UI; URL direta ainda é gap)
- Cota/storage por tenant
- Agente local / Tailscale
