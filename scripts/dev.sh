#!/usr/bin/env bash
# Sobe o ambiente completo de desenvolvimento: motor Python + site Next.js.
#
#   ./scripts/dev.sh              # site em http://localhost:3000
#   PORTA_SITE=4000 ./scripts/dev.sh
#
# O motor Python guarda os dados em CELESTIA_DATA_DIR (padrão ~/.celestia-financeiro):
# é lá que ficam o banco SQLite dos extratos e os arquivos originais importados.
# Ctrl+C derruba os dois processos.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PORTA_MOTOR="${PORTA_MOTOR:-8787}"
PORTA_SITE="${PORTA_SITE:-3000}"
export CELESTIA_DATA_DIR="${CELESTIA_DATA_DIR:-$HOME/.celestia-financeiro}"

echo "→ dados em: $CELESTIA_DATA_DIR"
mkdir -p "$CELESTIA_DATA_DIR"

# --- pré-requisitos -------------------------------------------------------
command -v python3 >/dev/null || { echo "✗ python3 não encontrado (precisa de 3.11+)"; exit 1; }
command -v node >/dev/null || { echo "✗ node não encontrado (precisa de 18+)"; exit 1; }

if ! python3 -c "import openpyxl" >/dev/null 2>&1; then
  echo "→ instalando openpyxl (única dependência do motor)…"
  python3 -m pip install --quiet openpyxl || {
    echo "✗ falha ao instalar openpyxl. Rode: python3 -m pip install openpyxl"; exit 1; }
fi

if [ ! -d node_modules ]; then
  echo "→ instalando dependências do site (npm install)…"
  npm install --no-audit --no-fund
fi

# --- processos ------------------------------------------------------------
encerrar() {
  echo ""
  echo "→ encerrando…"
  [ -n "${PID_MOTOR:-}" ] && kill "$PID_MOTOR" 2>/dev/null || true
  [ -n "${PID_SITE:-}" ] && kill "$PID_SITE" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap encerrar EXIT INT TERM

echo "→ motor Python em http://127.0.0.1:$PORTA_MOTOR"
python3 -m engine.server --port "$PORTA_MOTOR" &
PID_MOTOR=$!

# Espera o motor responder antes de subir o site (evita erro na primeira chamada).
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PORTA_MOTOR/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done

export NEXT_PUBLIC_ENGINE_URL="http://127.0.0.1:$PORTA_MOTOR"
echo "→ site em http://localhost:$PORTA_SITE"
npx next dev -p "$PORTA_SITE" &
PID_SITE=$!

echo ""
echo "Pronto. Abra http://localhost:$PORTA_SITE"
echo "  /importar   analisa sua planilha de carteira (.xlsm/.xlsx/.csv)"
echo "  /extratos   extratos bancários, cartões e faturas"
echo "Ctrl+C encerra os dois."
wait
