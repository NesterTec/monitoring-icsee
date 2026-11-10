# ICSEE NVR na VPS via WireGuard — Fase 1

**Date:** 2026-11-10  
**Status:** Approved for planning (pending user review of this file)  
**Project path:** `/home/nestertec/Documentos/projects/icsee`

## Goal

Rodar o NVR ICSEE na VPS Nestertec (como store/financeiro), alcançando as câmeras da LAN de casa por **WireGuard** (gratuito/ilimitado), e expor o painel em `https://nestertec.com/adm/house/monitor/` com o login por senha já existente.

## Decisions locked

| Topic | Choice |
|-------|--------|
| Onde roda NVR (gravação + go2rtc) | **VPS** |
| Como alcançar câmeras LAN | **WireGuard** (VPS = server; casa = client) |
| URL pública | Path `https://nestertec.com/adm/house/monitor/` |
| Auth nesta fase | Sessão/senha atual (sem 2FA) |
| Fora da fase 1 | 2FA e-mail, superusuário multi-conta, automação NesterConfigs |

## Problem

A VPS não roteia para `192.168.x.x`. Sem um túnel a partir da rede de casa, RTSP/Live/gravação na nuvem são impossíveis.

## Architecture

```text
[Câmeras 192.168.1.0/24]
        ↑
[PC casa: WireGuard client + IP forward da LAN]
        ║  UDP WireGuard
[VPS: WireGuard server (wg0)]
        ↓
[Docker: icsee-nvr + go2rtc + volumes]
        ↑
[nginx nestertec.com]
  location /adm/house/monitor/  →  NVR edge/app
```

### Components

1. **WireGuard (VPS)**  
   - Interface `wg0`, IP de túnel (ex. `10.10.10.1/24`).  
   - Firewall: UDP da porta WG aberta.  
   - Rotas/forwarding para alcançar hosts LAN anunciados pelo peer de casa.

2. **WireGuard (casa)**  
   - Cliente no PC (ligado 24/7) ou roteador com WG.  
   - Peer = VPS.  
   - Encaminha tráfego da VPS para `192.168.1.0/24` (`net.ipv4.ip_forward=1` + `PostUp` iptables MASQUERADE/FORWARD conforme o SO).  
   - Sem isso, a VPS não chega nas câmeras.

3. **Docker NVR na VPS**  
   - Mesmos serviços do compose atual: `app`, `go2rtc`, (edge nginx interno se necessário).  
   - Volumes: SQLite/`data`, `recordings/`.  
   - Contêineres precisam usar a rota via `wg0` até `192.168.1.x` (host network **ou** `extra_hosts`/routing no host com docker bridge + policy routing). Preferência inicial: **`network_mode: host`** na VPS (já usado localmente) para simplificar RTSP/WebRTC/WG.

4. **Nginx do domínio**  
   - `location /adm/house/monitor/` → proxy_pass para o NVR (porta do edge, ex. 8090).  
   - WebSocket/Upgrade para live (`/adm/house/monitor/rtc/` → go2rtc).  
   - App configurada com `ROOT_PATH=/adm/house/monitor` (ou equivalente) para assets, redirects e `GO2RTC_PUBLIC_URL`.

5. **Auth**  
   - Mantém login e-mail/senha do NVR.  
   - Superusuário 2FA fica em fase posterior.

## Data & migration

- Exportar/copiar `app/data/nvr.db` (e chave `CAMERA_SECRET_KEY`) para a VPS, **ou** recadastrar câmeras.  
- Hosts das câmeras continuam `192.168.1.x` (alcançáveis só via WG).  
- Gravações novas na VPS; histórico local opcional de migrar depois.

## Deploy checklist (fase 1)

1. Instalar e configurar WG server na VPS; liberar UDP.  
2. Configurar WG client em casa com forward da LAN.  
3. Teste: da VPS, `nc -vz 192.168.1.x 554` (ou ping) até as câmeras.  
4. Subir Docker ICSEE na VPS; sync go2rtc; validar Live/gravação.  
5. Ajustar app para path prefix `/adm/house/monitor`.  
6. Nginx site: location + SSL já existente do nestertec.com.  
7. Validar URL pública e login.

## Risks

| Risco | Mitigação |
|-------|-----------|
| PC casa desliga → câmeras somem na VPS | Manter PC/roteador ligado; monitorar WG; alerta depois |
| Path prefix quebra assets/WebRTC | Testes de `ROOT_PATH` + proxy `/rtc` |
| Docker bridge sem rota WG | Preferir host network na VPS nesta fase |
| Disco VPS / quota gravação | Quota já existente no NVR; dimensionar volume |

## Out of scope (próximas fases)

- 2FA por e-mail (`nestertec@gmail.com`)  
- Superusuário acessando qualquer conta/tenant  
- Integração deploy via NesterConfigs  
- Subdomínio dedicado  
- Trocar WireGuard por Tailscale/Cloudflare

## Relation to current ICSEE

Complementa o MVP local: o app continua o mesmo; muda o **onde** (VPS) e o **caminho de rede** até as câmeras (WG). DHCP rediscovery e SD/HD Live seguem válidos na VPS.
