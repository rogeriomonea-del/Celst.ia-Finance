#!/usr/bin/env bash
# Sobe o ambiente completo de desenvolvimento: motor Python + site Next.js.
#
#   ./scripts/dev.sh              # site em http://localhost:3000
#   PORTA_SITE=4000 ./scripts/dev.sh
#
# O motor Python guarda os dados em CELESTIA_DATA_DIR (padrão ~/.celestia-financeiro):
# é lá que ficam o banco SQLite dos extratos, os arquivos originais importados
# e o banco de contas/sessões do login.
#
# Login em desenvolvimento: sem SMTP configurado, o código de acesso de 6
# dígitos NÃO é enviado por e-mail — ele é impresso neste terminal, na linha
# "SMTP não configurado — código de ...". O primeiro e-mail que entrar vira
# administrador. Para testar o envio real, exporte CELESTIA_SMTP_* antes de
# rodar (veja .env.example).
# Ctrl+C derruba os dois processos.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PORTA_MOTOR="${PORTA_MOTOR:-8787}"
PORTA_SITE="${PORTA_SITE:-3000}"
export CELESTIA_DATA_DIR="${CELESTIA_DATA_DIR:-$HOME/.celestia-financeiro}"

# Login: o cookie de sessão sai sem `Secure` (estamos em http://localhost) e os
# redirecionamentos do login (inclusive o retorno do Google) apontam para o site
# local. Em produção essas variáveis vêm de /etc/celestia/finance.env.
export CELESTIA_URL_BASE="${CELESTIA_URL_BASE:-http://localhost:$PORTA_SITE}"
export CELESTIA_COOKIE_INSEGURO="${CELESTIA_COOKIE_INSEGURO:-1}"

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
echo "  /entrar     login por código de e-mail (o primeiro e-mail vira admin)"
echo "  /importar   analisa sua planilha de carteira (.xlsm/.xlsx/.csv)"
echo "  /extratos   extratos bancários, cartões e faturas"
echo "  /admin      aprovação de contas, sessões e acessos"
if [ -z "${CELESTIA_SMTP_HOST:-}" ]; then
  echo ""
  echo "  Login sem SMTP: o código de 6 dígitos NÃO vai por e-mail — ele aparece"
  echo "  AQUI no terminal, na linha do motor \"SMTP não configurado — código de ...\"."
fi
echo "Ctrl+C encerra os dois."
wait
