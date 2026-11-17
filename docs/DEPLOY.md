# Deploy — ICSEE NVR

Fonte canônica de implantação. Snippets em `deploy/` apontam para cá. Fluxo visual: [html/fluxo-deploy.html](html/fluxo-deploy.html).

## Ambientes

| | Local | VPS |
|--|-------|-----|
| Host | PC de casa | `root@191.252.208.229` |
| Código | clone do repo | `/var/www/monitor` |
| URL | http://127.0.0.1:8090/ | https://www.nestertec.com.br/adm/house/monitor/ |
| Câmeras | LAN direta | LAN via WireGuard |

## 1. Local

```bash
cp .env.example .env   # ajuste ADMIN_*, SECRET_KEY, CAMERA_SECRET_KEY
# Local:
PUBLIC_BASE_PATH=
GO2RTC_PUBLIC_URL=/rtc

docker compose up -d --build
```

Serviços: `icsee-nvr` :8080, `icsee-go2rtc` :1984, `icsee-edge` :8090 (todos `network_mode: host`).

## 2. WireGuard (casa ↔ VPS) — multi-site

Objetivo: a VPS alcança as câmeras de **cada localidade** via bloco VPN único (`10.20.n.0/24`) com NETMAP 1:1 para a LAN real do cliente. Sem Docker na casa — só WireGuard.

### Endereços típicos

| Peer | IP túnel | Alcance câmeras |
|------|----------|-----------------|
| VPS (servidor) | `10.10.10.1/24`, UDP `51820` | — |
| Site n (cliente) | `10.10.10.(n+1)/32` | `AllowedIPs` inclui `10.20.n.0/24` |

Templates: [`deploy/wireguard/`](../deploy/wireguard/). O painel gera o `.conf` do cliente (**Localidade** / Admin).

### Env na VPS (além do NVR)

```env
WG_SERVER_PUBLIC_KEY=<pubkey do servidor>
WG_SERVER_ENDPOINT=<IP_PUBLICO>:51820
WG_SERVER_TUNNEL_IP=10.10.10.1
WG_VPN_BASE=10.20.0.0/16
WG_PEERS_DIR=/data/wireguard/peers
# Opcional no host (fora do Docker):
# WG_APPLY_CMD=/usr/local/bin/icsee-apply-peer.sh
```

Copie [`deploy/wireguard/apply-peer.sh`](../deploy/wireguard/apply-peer.sh) para o host se quiser aplicar peers automaticamente.

### Casa / cliente — pontos críticos

1. Baixar `.conf` no painel (já traz `NETMAP` vpn→lan + MASQUERADE).
2. Ajustar iface LAN no conf (`eth0` / `enp…` / `wlx…`) se necessário (**Localidade** ou editar o arquivo).
3. Instalar e manter ligado:

```bash
sudo cp icsee-site-N-wg0.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

4. Cadastrar câmeras com o **IP da LAN**; a VPS usa o IP mapeado em `10.20.n.x`.

### Testes

```bash
# cliente
ping -c2 10.10.10.1

# VPS
ping -c2 10.10.10.N
nc -vz 10.20.N.3 554   # IP VPN da câmera (não o 192.168.x)
```

Handshake OK + `:554` no bloco VPN = NVR na VPS pode puxar RTSP.

Cutover detalhado: [specs/2026-11-10-multi-tenant-wireguard-sites-design.md](superpowers/specs/2026-11-10-multi-tenant-wireguard-sites-design.md).

## 3. VPS — Docker

### Sync de código (laptop → VPS)

O painel **NesterConfigs** só reconstrói o que já está em `/var/www/monitor` (não faz rsync). Antes do Deploy no painel, sincronize o código:

```bash
# do clone local
./scripts/deploy-producao.sh --remote
# ou só sync + rebuild remoto:
# rsync … && ssh … 'cd /var/www/monitor && bash scripts/deploy-producao.sh'
```

### Manual / in-place na VPS

```bash
# .env na VPS (não versionar) — paths de container + path público
PUBLIC_BASE_PATH=/adm/house/monitor
GO2RTC_PUBLIC_URL=/adm/house/monitor/rtc
DATABASE_URL=sqlite:////data/nvr.db
DEFAULT_STORAGE_PATH=/recordings
GO2RTC_CONFIG_PATH=/data/go2rtc.generated.yaml
GO2RTC_API=http://127.0.0.1:1984
# ADMIN_*, SECRET_KEY, CAMERA_SECRET_KEY iguais ao DB
# WG_SERVER_* para multi-tenant WireGuard

cd /var/www/monitor
bash scripts/deploy-producao.sh
# equivalente: docker compose up -d --build + curl :8090/login
docker exec icsee-nvr printenv GO2RTC_PUBLIC_URL PUBLIC_BASE_PATH
```

`CAMERA_SECRET_KEY` deve ser a mesma usada para gerar `password_enc` no SQLite copiado.

### NesterConfigs

Projeto seedado `icsee` → path `/var/www/monitor` → script `scripts/deploy-producao.sh`. Health: `https://www.nestertec.com.br/adm/house/monitor/login`.

## 4. Nginx (NesterStore)

Arquivo no servidor: `/var/www/store/docker/nginx/production.ssl.conf`  
Snippet versionado: [`deploy/nginx/nestertec-monitor.conf`](../deploy/nginx/nestertec-monitor.conf)

- `location /adm/house/monitor/` → rewrite + `proxy_pass http://172.18.0.1:8090`
- `Connection "upgrade"` para WebSocket do go2rtc
- Após editar: `docker exec nesterstore-nginx nginx -t && docker exec nesterstore-nginx nginx -s reload`

O IP `172.18.0.1` é o gateway da rede Docker `store_nesterstore` (host). Confirme com `docker network inspect` se a rede mudar.

## 5. Verificações rápidas

```bash
# edge local na VPS
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/login

# público
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://www.nestertec.com.br/adm/house/monitor/login
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://www.nestertec.com.br/adm/house/monitor/rtc/stream.html?src=cam_2'

# stream puxando bytes (VPS)
timeout 6 curl -sS -N 'http://127.0.0.1:1984/api/stream.mp4?src=cam_2' -o /tmp/t.mp4
ls -la /tmp/t.mp4
```

## 6. Problemas frequentes

| Sintoma | Causa provável |
|---------|----------------|
| 404 Nestertec nos iframes Live | `GO2RTC_PUBLIC_URL=/rtc` na VPS |
| VPS não abre `:554`, casa sim | MASQUERADE na iface errada / WG down |
| Ping LAN morto | PC na rede/SSID errada; câmeras off |
| 401 no go2rtc | Credencial ≠ dispositivo naquele IP |
| 502 no path monitor | edge :8090 parado ou gateway nginx errado |

## Atualizar este doc

Mudou IP da VPS, path público, rede Docker ou procedimento WG → edite aqui e [html/fluxo-deploy.html](html/fluxo-deploy.html). Mantenha `deploy/README.md` como resumo curto.

## SSO (OIDC)

Login via NesterAdmin (Authorization Code + PKCE, tokens validados por JWKS com PyJWT).

- Env (`.env`, ver `.env.example`): `OIDC_ISSUER`, `OIDC_CLIENT_ID=icsee`, `OIDC_CLIENT_SECRET` (nunca no git), `OIDC_REDIRECT_URI=https://www.nestertec.com.br/adm/house/monitor/auth/oidc/callback`, `SSO_REQUIRED`.
- Rotas do app: `GET /auth/oidc/login` e `GET /auth/oidc/callback` (nginx remove o prefixo `/adm/house/monitor`; `PUBLIC_BASE_PATH` o recoloca nos redirects).
- O e-mail do ID token precisa corresponder a um usuário local (`users.email`); o `sub` fica em `users.oidc_sub` (coluna criada automaticamente no SQLite). Não há auto-cadastro.
- `SSO_REQUIRED=false` (padrão): botão "Entrar com SSO Nestertec" em `/login`. `SSO_REQUIRED=true`: `/login`, `require_user` e páginas protegidas redirecionam ao SSO (APIs/JSON seguem com 401).
