# VPS WireGuard Monitor Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare ICSEE NVR for VPS deploy behind `https://nestertec.com/adm/house/monitor/` with WireGuard reachability to home LAN cameras.

**Architecture:** Add `PUBLIC_BASE_PATH` so redirects/assets/go2rtc URLs work under a reverse-proxy prefix; ship WireGuard server/client config templates and an nginx snippet; keep runtime auth as current password login.

**Tech Stack:** FastAPI, Jinja2, Docker Compose, WireGuard, nginx.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-11-10-vps-wireguard-monitor-phase1-design.md`
- Public path: `/adm/house/monitor`
- Tunnel: WireGuard (VPS server, home client)
- No 2FA / multi-tenant in this phase
- Prefer host networking notes for VPS RTSP/WG

## File map

| Path | Responsibility |
|------|----------------|
| `app/nvr/config.py` | `public_base_path` setting |
| `app/nvr/urls.py` | `abs_path()` helper |
| `app/nvr/main.py` | Mount static under prefix-aware paths if needed |
| `app/nvr/routers/web.py` | Prefixed redirects |
| `app/nvr/templates/*.html` | Prefixed nav/static links |
| `deploy/wireguard/*` | Server/client example configs + README |
| `deploy/nginx/nestertec-monitor.conf` | location snippet |
| `.env.example` | VPS env vars |
| `docker-compose.yml` / notes | Production hints |

---

### Task 1: PUBLIC_BASE_PATH helper + redirects

**Files:** Create `app/nvr/urls.py`; modify `config.py`, `web.py`; test `app/tests/test_public_base_path.py`

- [ ] Add `public_base_path: str = ""` to Settings
- [ ] `abs_path("/cameras")` → `/adm/house/monitor/cameras` when base set
- [ ] Replace hard-coded `RedirectResponse("/...")` with `abs_path`
- [ ] Tests with env `PUBLIC_BASE_PATH=/adm/house/monitor`

### Task 2: Templates + go2rtc public URL under prefix

**Files:** `base.html`, other templates with absolute `/` links; ensure `GO2RTC_PUBLIC_URL` defaults documented as `/adm/house/monitor/rtc`

- [ ] Pass `base_path` into templates via context processor or each render
- [ ] Prefix `/static` and nav hrefs

### Task 3: Deploy artifacts (WireGuard + nginx)

**Files:** `deploy/wireguard/README.md`, `wg0-server.conf.example`, `wg0-client-home.conf.example`, `deploy/nginx/nestertec-monitor.conf`

- [ ] Document keys generation, forwarding, VPS UDP, LAN route test `nc -vz 192.168.1.x 554`
- [ ] Nginx location + WebSocket headers for `/adm/house/monitor/` and `/adm/house/monitor/rtc/`

### Task 4: .env.example + verify tests

- [ ] Document `PUBLIC_BASE_PATH`, `GO2RTC_PUBLIC_URL`
- [ ] Full pytest green
- [ ] Commit
