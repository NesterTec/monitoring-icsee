# Documentação ICSEE NVR

Índice da documentação operacional e técnica. Preferir estes arquivos a notas soltas em `deploy/` ou specs antigas.

## O que ler

| Documento | Para quê |
|-----------|----------|
| [MANUAL.md](MANUAL.md) | Uso do painel: URLs, login, live, câmeras, logs, checklist de falhas |
| [ARQUITETURA.md](ARQUITETURA.md) | Componentes, dados, contratos, pontos de extensão |
| [DEPLOY.md](DEPLOY.md) | Local, VPS, WireGuard, nginx — **fonte canônica de deploy** |
| [html/index.html](html/index.html) | Hub visual com diagramas de fluxo (abrir no navegador) |

Histórico de design (não é o manual do dia a dia): `docs/superpowers/specs/` e `docs/superpowers/plans/`.

Atalhos legados:

- `docs/tecnico.html` → redireciona para `html/index.html`
- `deploy/README.md` e `deploy/wireguard/README.md` → resumos com link para `DEPLOY.md`

## Como manter (regra)

Sempre que o **comportamento** ou a **topologia** mudar:

1. Atualize o Markdown correspondente (`MANUAL` / `ARQUITETURA` / `DEPLOY`).
2. Atualize o HTML do fluxo afetado em `html/fluxo-*.html` (e o sumário em `html/index.html` se precisar).
3. Não duplique o procedimento completo em `deploy/`; mantenha lá só um ponteiro.

Não versionar segredos: senhas, chaves WireGuard privadas, `.env` real. Use placeholders.

## Abrir o hub HTML

```bash
xdg-open docs/html/index.html
# ou sirva a pasta:
python3 -m http.server -d docs/html 8765
# → http://127.0.0.1:8765/
```
