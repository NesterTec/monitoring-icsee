#!/usr/bin/env bash
# Deploy ICSEE NVR na VPS (in-place). Usado pelo NesterConfigs via SSH.
# Uso (na VPS): ./scripts/deploy-producao.sh
# Ou do laptop: ./scripts/deploy-producao.sh --remote

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${ICSEE_VPS:-root@191.252.208.229}"
REMOTE_PATH="${ICSEE_REMOTE_PATH:-/var/www/monitor}"
COMPOSE=(docker compose)

if [ "${1:-}" = "--remote" ]; then
  echo "==> Rsync para ${REMOTE_HOST}:${REMOTE_PATH}"
  rsync -az --delete \
    --exclude '.git' \
    --exclude '.env' \
    --exclude 'recordings/*' \
    --exclude 'app/data/*' \
    --exclude 'app/.venv' \
    --exclude 'app/.pytest_cache' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'go2rtc.generated.yaml' \
    "${ROOT_DIR}/" "${REMOTE_HOST}:${REMOTE_PATH}/"

  echo "==> Executando deploy remoto"
  ssh "${REMOTE_HOST}" "cd ${REMOTE_PATH} && bash scripts/deploy-producao.sh"
  exit 0
fi

cd "$ROOT_DIR"

echo "==> ICSEE NVR deploy (${ROOT_DIR})"

if [ ! -f .env ]; then
  echo "Erro: .env não encontrado em ${ROOT_DIR}"
  exit 1
fi

echo "==> Subindo containers"
"${COMPOSE[@]}" up -d --build

echo "==> Aguardando edge"
sleep 5

echo "==> Health local"
if curl -fsS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8090/login" | grep -Eq '200|302|303'; then
  echo "OK: edge :8090/login"
else
  echo "Aviso: edge :8090/login não respondeu 200/302 — confira docker compose ps / logs"
  "${COMPOSE[@]}" ps || true
  exit 1
fi

echo "==> Deploy concluído. URL: https://www.nestertec.com.br/adm/house/monitor/"
