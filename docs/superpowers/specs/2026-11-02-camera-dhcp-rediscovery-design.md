# Rediscovery de câmeras DHCP + aviso “perdida” + substituir dispositivo

**Date:** 2026-11-02  
**Status:** Approved for planning (pending user review of this file)  
**Project path:** `/home/nestertec/Documentos/projects/icsee`

## Goal

Câmeras ICSEE costumam vir em DHCP: o IP muda e o NVR deixa de alcançá-las porque o vínculo atual é o campo `host`. Este trabalho adiciona (1) monitoramento periódico de alcance, (2) rediscovery automática por credenciais do dispositivo com atualização do IP no mesmo registro, (3) estado “perdida” com modal global e central de notificações, (4) retry manual e fluxo “Substituir dispositivo” que preserva identidade, configs e gravações.

## Decisions locked

| Topic | Choice |
|-------|--------|
| Abordagem | Monitor em background no processo do NVR (mesmo padrão do `recorder_manager`) |
| Health check | TCP na porta RTSP (`host` + `rtsp_port`), a cada ~30s |
| Disparo de rediscovery | 3 falhas consecutivas (~1,5 min) |
| Match na rediscovery | Primeiro IP livre que autenticar (ordem de resposta / `as_completed`, não o menor IP) |
| IPs já cadastrados | Ignorar hosts já usados por **outras** câmeras |
| Persistência | Atualizar `host` no mesmo `Camera.id` (não cria registro novo) |
| Sem match | Marcar câmera como `lost`; modal global + notificação reabrível |
| Retry | Botão “Tentar novamente” na edição da câmera |
| Troca de aparelho | “Substituir dispositivo” na edição: credenciais/host novos no mesmo registro |
| CIDR da varredura | `guess_default_cidr()` (já usado na descoberta manual) |

## Architecture

```text
lifespan
  └─ camera_monitor (thread/loop)
        ├─ a cada 30s: TCP RTSP por câmera enabled
        ├─ 3 falhas → scan CIDR → auth_probe (user/senha da câmera)
        ├─ match → UPDATE cameras.host + sync_go2rtc + reconcile
        └─ sem match → status=lost + notificação ativa

Browser (qualquer página)
  ├─ modal global se houver alerta “perdida” ainda não dispensado nesta sessão de UI
  └─ ícone de notificações (badge) → reabre alertas ativos
```

### Components

1. **`camera_monitor`** (novo módulo, iniciado no `lifespan` junto com o recorder)  
   - Loop de health TCP  
   - Orquestra rediscovery reutilizando `scan_rtsp_hosts` / `auth_probe_hosts` / `probe_rtsp_auth`  
   - Atualiza DB, dispara `sync_go2rtc` e `recorder_manager.reconcile` após mudança de host  
   - Cooldown de rediscovery por câmera: no mínimo **2 minutos** entre varreduras completas da mesma câmera (evita flood de scan enquanto `lost`)

2. **Modelo `Camera` (campos novos)**  
   - `reachability_status`: `ok` | `unreachable` | `lost` (string curta)  
   - `fail_count`: inteiro  
   - `last_seen_at`: datetime nullable  
   - Migração leve via `PRAGMA`/`ALTER` no estilo já usado em `db.py`

3. **Notificações**  
   - Persistidas (SQLite): pelo menos `id`, `camera_id`, `kind` (`camera_lost`), `message`, `created_at`, `active`  
   - Enquanto `active` e câmera ainda `lost`, aparecem no ícone  
   - Ao recuperar (rediscovery ou substituir / retry OK): desativa notificação e volta `reachability_status` para `ok`  
   - Fechar a modal **não** desativa o alerta; só marca “já mostrado nesta sessão” no cliente (sessionStorage ou equivalente) para não reabrir em loop até o usuário abrir o ícone

4. **UI**  
   - Modal global em `base.html` (todas as páginas autenticadas)  
   - Ícone de notificações no header com badge de ativos  
   - Em `camera_form.html`: “Tentar novamente” e “Substituir dispositivo”

5. **Rotas**  
   - `GET /notifications` (JSON da lista ativa) e/ou fragmento HTML  
   - `POST /cameras/{id}/retry-reachability`  
   - `POST /cameras/{id}/replace` (host, username, password, opcionalmente porta/canal/streams)

## Data flow

### Health → rediscovery → update

1. Para cada `Camera` com `enabled=True`, TCP open em `(host, rtsp_port)` com timeout curto.  
2. Sucesso → `fail_count=0`, `last_seen_at=now`, se estava `lost`/`unreachable` → `ok` e desativa alerta.  
3. Falha → `fail_count += 1`, status pelo menos `unreachable`.  
4. Se `fail_count >= 3` e cooldown de scan ok →  
   - `hosts = scan_rtsp_hosts(cidr)`  
   - remover hosts iguais a `host` de **outras** câmeras  
   - autenticar hosts candidatos em paralelo com username/password desta câmera  
   - o **primeiro** futuro que retornar `ok` vence (demais podem ser cancelados/ignorados) → `cam.host = novo`; commit; sync go2rtc; reconcile; limpar lost/alerta  
   - nenhum `ok` → `reachability_status=lost`; criar/ativar notificação `camera_lost`

### Substituir dispositivo

1. Admin informa host (manual ou via fluxo de discover existente) + user/senha novos.  
2. `probe_rtsp_auth` (ou equivalente) valida.  
3. No mesmo `Camera`: atualiza `host`, `username`, `password_enc` (e campos RTSP se enviados).  
4. Não move arquivos de gravação: segmentos já apontam para `camera_id`.  
5. Limpa `lost` / fail_count / notificação; sync + reconcile.

Credenciais antigas não precisam ser reinformadas para “transferir” histórico: a transferência é a preservação do registro e dos segmentos.

## Error handling

| Situação | Comportamento |
|----------|----------------|
| Rede instável (1–2 falhas) | Só incrementa contador; sem scan |
| Vários IPs aceitam as mesmas credenciais | Usa o **primeiro** que autenticar entre os livres |
| Nenhum IP autentica | `lost` + modal/notificação; câmera permanece no painel |
| Scan CIDR inválido / grande demais | Log + não marca lost por erro de scan; tenta depois |
| Usuário fecha modal | Alerta continua ativo no ícone |
| Câmera desligada fisicamente depois reparada | “Tentar novamente” ou espera o monitor (respeitando cooldown) |
| Troca de hardware | “Substituir dispositivo” com credenciais novas |

## Testing

- Unit/integration: health success zera `fail_count`; 3 falhas disparam caminho de rediscovery (mock scan/auth).  
- Rediscovery atualiza `host` e ignora IP de outra câmera.  
- Sem match → status `lost` + notificação ativa.  
- Retry e replace limpam estado e atualizam credenciais/host.  
- Teste de UI mínimo opcional; pelo menos endpoints JSON/POST cobertos.

## Out of scope

- Configurar IP fixo na câmera via API do fabricante  
- Confirmação manual quando houver múltiplos matches  
- E-mail / push / Telegram  
- Identidade estável tipo MAC/serial (não disponível de forma confiável no fluxo RTSP atual)  
- Monitorar câmeras com `enabled=False`

## Relation to MVP

Complementa o registro de câmeras do MVP (`2026-11-01-nvr-mvp-design.md`): o `id` continua a chave estável para live, go2rtc e gravações; só o `host` (e, no replace, as credenciais) muda quando o DHCP ou o hardware mudam.
