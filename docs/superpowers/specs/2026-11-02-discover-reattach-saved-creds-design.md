# Reatachar câmeras com logins salvos (pós-varredura)

**Date:** 2026-11-02  
**Status:** Approved for implementation  
**Project path:** `/home/nestertec/Documentos/projects/icsee`

## Goal

Na tela **Nova câmera**, após a varredura RTSP, permitir reatachar câmeras já cadastradas usando os usuários/senhas salvos no banco: testar esses logins nos IPs **livres** (encontrados na varredura e ainda não cadastrados) e, no match, atualizar o `host` do registro existente.

## Decisions locked

| Topic | Choice |
|-------|--------|
| Onde | Tela Nova câmera, botão após varredura |
| Alvo dos testes | IPs da varredura **não cadastrados** |
| Credenciais | Pares user/senha das câmeras já no DB |
| Match | Atualiza `host` da câmera dona do login (mesmo `id`) |
| Conflito | Primeiro IP que autenticar para aquela câmera; host sai do pool |
| Não faz | Não cria câmera nova neste fluxo |

## Flow

1. Usuário varre (`/cameras/discover/scan`).  
2. Clica **“Reatachar com logins salvos”**.  
3. `POST /cameras/discover/reattach` com `{ hosts, port }`.  
4. Servidor: `free = hosts - {c.host for c in cameras}`.  
5. Para cada câmera cadastrada (ordem estável por `id`): testa credenciais nos `free` (paralelo, primeiro `ok`); se match → `cam.host = match`, limpa lost/notif, remove IP de `free`.  
6. Ao final: `sync_go2rtc` + `reconcile` se houve update.  
7. Resposta JSON com `updated` e `unmatched` para a UI.

## Out of scope

- Cadastrar câmera nova neste botão  
- Escolher manualmente entre matches múltiplos  
- Rodar sem varredura prévia (hosts vêm do client)
