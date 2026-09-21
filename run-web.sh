#!/usr/bin/env bash
# Запуск web-бэкенда (uv run agent-web) и фронтенда (npm run dev) вместе.
# Ctrl+C останавливает оба процесса.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT"
FRONTEND_DIR="$ROOT/web"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  echo "Остановка процессов..."
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "→ Бэкенд: http://127.0.0.1:8321"
(cd "$BACKEND_DIR" && uv run agent-web) &
BACKEND_PID=$!

echo "→ Фронтенд: http://localhost:5173 (проксирует /api на 8321)"
(cd "$FRONTEND_DIR" && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "Оба запущены. Открой http://localhost:5173 в браузере. Ctrl+C — остановить."
echo "Порт бэкенда: MY_AGENT_PORT или --port; перезагрузка кода: --reload."
echo ""

wait "$BACKEND_PID" "$FRONTEND_PID"