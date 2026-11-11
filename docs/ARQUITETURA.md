# Arquitetura — ICSEE NVR

Visão de componentes e contratos. Operação: [MANUAL.md](MANUAL.md). Deploy: [DEPLOY.md](DEPLOY.md). Fluxos visuais: [html/index.html](html/index.html).

## Objetivo

NVR para câmeras ICSEE/Xiongmai: painel web, live no navegador, gravação em disco, rediscovery DHCP, acesso remoto via VPS + WireGuard (path sob nestertec.com.br).

## Stack

| Camada | Tecnologia | Função |
|--------|------------|--------|
| App | Python 3.12, FastAPI, Jinja2 | Painel, API, orquestração |
| DB | SQLite + SQLAlchemy | Usuários, câmeras, settings, segmentos, logs |
| Live | go2rtc + FFmpeg (H264) | RTSP → MSE/WebRTC no browser |
| Gravação | FFmpeg no container `app` | RTSP → TS → MP4 + cota |
| Edge | nginx (`icsee-edge`) :8090 | `/` → app :8080; `/rtc/` → go2rtc :1984 |
| Pack | Docker Compose, `network_mode: host` | LAN + rotas WG do host |

## Serviços Docker

```
browser
   │
   ▼
[edge :8090] ──/──► [app :8080] ──► SQLite + recordings/
       │                │
       └──/rtc/──► [go2rtc :1984] ──RTSP──► câmeras LAN
                         ▲
                    go2rtc.generated.yaml
                    (escrito por app/nvr/go2rtc_sync.py)
```

- **app** (`icsee-nvr`): FastAPI, sync go2rtc, recorder, monitor de reachability.
- **go2rtc** (`icsee-go2rtc`): producers `ffmpeg:rtsp://…#video=h264` por stream SD/HD.
- **edge** (`icsee-edge`): único entrypoint local/VPS na 8090.

Na VPS, o nginx do **NesterStore** faz strip de `/adm/house/monitor/` e encaminha para `172.18.0.1:8090` (gateway da rede Docker → host).

## Path público (`PUBLIC_BASE_PATH`)

| Variável | Local | VPS |
|----------|-------|-----|
| `PUBLIC_BASE_PATH` | vazio | `/adm/house/monitor` |
| `GO2RTC_PUBLIC_URL` | `/rtc` | `/adm/house/monitor/rtc` |

Templates usam `abs_path` / `window.ICSEE_BASE` para links e assets. Iframes de live usam `go2rtc_public_url`.

**Importante:** `GO2RTC_PUBLIC_URL` deve vir do `.env`. Não fixar `/rtc` em `environment:` do compose na VPS (sobrescreve o env_file e quebra o live no domínio público).

## Sync go2rtc

Arquivo: [`app/nvr/go2rtc_sync.py`](../app/nvr/go2rtc_sync.py).

1. Lê câmeras `enabled`.
2. Descriptografa senha (Fernet / `CAMERA_SECRET_KEY`).
3. Monta URL RTSP XM via [`app/nvr/rtsp.py`](../app/nvr/rtsp.py).
4. Escreve `streams.cam_{id}` e `cam_{id}_hd` como **um** producer ffmpeg cada (evita sessões RTSP extras em H265).
5. POST `/api/restart` no go2rtc.

## Monitor / rediscovery

- Health TCP na porta RTSP; `fail_count` → status `lost`.
- Logs em tabela `health_logs` (UI **Logs**, últimos ~30).
- Scan de rede + reattach com credenciais salvas ([`reattach.py`](../app/nvr/reattach.py), discover).

## Modelo de dados (resumo)

| Tabela | Uso |
|--------|-----|
| `users` | Contas (Argon2); `role` = `superadmin` \| `user` |
| `sites` | Localidade: `lan_cidr`, `vpn_cidr`, `tunnel_ip`, chaves WG |
| `cameras` | `site_id`, `host_lan` (LAN), `host` (VPN/RTSP), credenciais, flags |
| `app_settings` | storage, quota |
| `recording_segments` | metadados MP4 |
| `health_logs` / `notifications` | auditoria de saúde |

Multi-tenant: um processo na VPS; isolamento por `site_id`. Spec: [superpowers/specs/2026-11-10-multi-tenant-wireguard-sites-design.md](superpowers/specs/2026-11-10-multi-tenant-wireguard-sites-design.md).

## RTSP ICSEE / Xiongmai

```
rtsp://USER:PASS@IP:554/user=USER&password=PASS&channel=1&stream=N.sdp?
# stream=1 → sub (SD / live)
# stream=0 → main (HD / gravação)
```

## Segurança

- Login: Argon2. Papéis: superadmin vs user (sites próprios).
- Câmeras / chaves WG privadas: Fernet (`CAMERA_SECRET_KEY` — mesma chave se copiar o DB).
- Não expor RTSP na internet; só painel atrás de HTTPS + VPN por site (NETMAP).
- `.env` e `go2rtc.generated.yaml` fora do git (contêm segredos).

## Extensão futura (não implementado)

Pontos naturais sem redesign:

- 2FA por e-mail no login
- Auth no go2rtc ou edge (URLs `/rtc` por `cam_id`)
- Cota/storage por tenant
- Alertas push além de Logs
- PTZ / ONVIF

Ao implementar, atualize este arquivo + o HTML do fluxo correspondente.

## Estrutura de pastas (relevante)

```
icsee/
├── app/nvr/           # código
├── recordings/
├── docs/              # manuais + html/
├── deploy/            # snippets nginx/WG (ponteiros para docs/DEPLOY.md)
├── docker-compose.yml
├── nginx.conf
└── go2rtc.generated.yaml
```
