# Deploy VPS — fase 1

1. **WireGuard** — siga `wireguard/README.md` até `nc -vz 192.168.1.x 554` funcionar na VPS.  
2. **Docker ICSEE** — copie o projeto, `.env` com segredos e:

```env
PUBLIC_BASE_PATH=/adm/house/monitor
GO2RTC_PUBLIC_URL=/adm/house/monitor/rtc
```

```bash
docker compose up -d --build
```

3. **Nginx** — inclua `nginx/nestertec-monitor.conf` no vhost de `nestertec.com` e recarregue o nginx.  
4. Abra `https://nestertec.com/adm/house/monitor/` e faça login.

PC de casa precisa permanecer ligado com o cliente WireGuard.
