# Manual de operação — ICSEE NVR

Guia do dia a dia. Detalhes de infra em [DEPLOY.md](DEPLOY.md); componentes em [ARQUITETURA.md](ARQUITETURA.md); diagramas em [html/index.html](html/index.html).

## URLs

| Ambiente | URL |
|----------|-----|
| Local (edge nginx) | http://127.0.0.1:8090/ |
| Local (app direto) | http://127.0.0.1:8080/ |
| Produção (VPS) | https://www.nestertec.com.br/adm/house/monitor/ |

Login: e-mail/senha da conta. O seed `ADMIN_EMAIL` / `ADMIN_PASSWORD` vira **superadmin**. Usuários comuns só veem as câmeras da própria localidade.

## Telas

- **Live** — grade de câmeras; botões **SD** / **HD**; tela cheia.
- **Câmeras** — cadastro (IP da LAN), edição, teste RTSP, descoberta no bloco VPN, reattach.
- **Gravações** — consulta por câmera e período (segmentos MP4).
- **Logs** — últimos eventos de saúde / perda / rediscovery (filtrados por tenant).
- **Localidade** — CIDR/iface e download do `.conf` WireGuard.
- **Configurações** — cota/storage (somente superadmin altera).
- **Admin** (superadmin) — criar usuários/sites, baixar peer servidor/cliente, re-provisionar chaves.

Credenciais de câmera são as do **dispositivo** (usuário curto tipo XM), não a conta cloud do app ICSEE.

## Live: o que o navegador carrega

Cada card usa um iframe apontando para o go2rtc:

- Local: `/rtc/stream.html?src=cam_{id}` (e `_hd` no HD)
- VPS: `/adm/house/monitor/rtc/stream.html?src=cam_{id}`

O valor vem de `GO2RTC_PUBLIC_URL` no `.env`. Se estiver errado (ex.: `/rtc` na VPS), o iframe mostra a página 404 do site Nestertec em vez do vídeo.

## Checklist — câmera sem imagem

1. **PC na rede certa?** Câmeras e o host do NVR (ou o peer WireGuard de casa) precisam estar na mesma LAN das câmeras. Interface errada (cabo vs Wi‑Fi) = ARP FAILED / ping morto.
2. **Ping / TCP 554 local** nos IPs cadastrados (`192.168.1.x`).
3. **IP mudou (DHCP)?** Use **Descobrir na rede** + reattach com logins salvos; atualize o host na ficha da câmera.
4. **VPS sem vídeo, local ok?** Túnel WireGuard ativo? `ping 10.10.10.1` ↔ `10.10.10.N`. Na VPS: `nc -vz <ip-vpn-camera> 554` (ex. `10.20.1.3`, não o `192.168.x`).
5. **MASQUERADE na iface ativa** — se o PC de casa mudou de `enp2s0` para Wi‑Fi (`wlx…`), o `PostUp` do `wg0` precisa MASQUERADE nessa iface. Ver [DEPLOY.md](DEPLOY.md).
6. **`GO2RTC_PUBLIC_URL`** no container VPS deve ser `/adm/house/monitor/rtc` (`docker exec icsee-nvr printenv GO2RTC_PUBLIC_URL`). Não deixar o `docker-compose.yml` sobrescrever com `/rtc`.
7. **401 Unauthorized no go2rtc** — senha/usuário do dispositivo incorretos para aquele IP (ou IP é de outra câmera). Confira Logs e `docker logs icsee-go2rtc`.
8. Hard refresh no browser (Ctrl+Shift+R) após mudar env/nginx.

## Descoberta e reattach

1. Em Câmeras / Configurações → **Descobrir na rede**.
2. CIDR típico: `192.168.1.0/24`, porta `554`.
3. **Varrer** lista hosts com a porta aberta.
4. **Reattach** tenta logins já salvos nos IPs novos; ou informe login manual para testar.

O monitor de saúde (background) marca `lost` após falhas TCP consecutivas e registra em Logs.

## Rede de casa (lembrete)

- PC/roteador do cliente com WireGuard (`wg-quick@wg0`) precisa permanecer ligado para a VPS alcançar o bloco VPN do site.
- App ICSEE no celular pode mostrar câmeras via **nuvem/P2P** mesmo quando a LAN local não as alcança — isso não prova RTSP local.

## Atualizar este manual

Mudou URL, tela ou procedimento de troubleshooting → edite este arquivo e, se o fluxo mudou, o HTML em `html/fluxo-*.html`.
