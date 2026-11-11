# Deploy VPS — resumo

**Documentação completa:** [docs/DEPLOY.md](../docs/DEPLOY.md)  
**Fluxo visual:** [docs/html/fluxo-deploy.html](../docs/html/fluxo-deploy.html)

| Item | Valor |
|------|--------|
| VPS | `root@191.252.208.229` |
| Código | `/var/www/monitor` |
| URL | https://www.nestertec.com.br/adm/house/monitor/ |
| WG | VPS `10.10.10.1` ↔ sites `10.10.10.n` + VPN `10.20.n.0/24` (UDP `51820`) |

## Snippets neste diretório

- [nginx/nestertec-monitor.conf](nginx/nestertec-monitor.conf) — location no nginx do store
- [wireguard/](wireguard/) — templates e notas curtas (detalhe em `docs/DEPLOY.md`)

## Deploy via NesterConfigs

1. Sync: `./scripts/deploy-producao.sh --remote` (ou rsync manual para `/var/www/monitor`)
2. No painel: projeto **ICSEE NVR** → Deploy / Pipeline (roda o mesmo script **sem** `--remote` na VPS)

## Ativar túnel em casa (sudo)

```bash
sudo cp ~/.config/icsee-wg0-home.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
# ou: sudo wg-quick down wg0; sudo wg-quick up wg0
ping -c2 10.10.10.1
```

Confirme MASQUERADE na **iface LAN ativa** (cabo ou Wi‑Fi). Ver checklist em `docs/DEPLOY.md`.

PC de casa precisa permanecer ligado com o cliente WireGuard.
