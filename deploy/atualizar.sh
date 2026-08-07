#!/usr/bin/env bash
# Atualiza o celest.ia Finance no VPS: puxa o código, recompila e reinicia.
#
#   sudo bash /opt/celestia/finance/deploy/atualizar.sh            # branch atual
#   sudo BRANCH=claude/extratos-bancarios bash .../atualizar.sh    # outra branch
#
# Faz backup antes de mexer e reverte o código se o build falhar — o site
# continua no ar com a versão anterior em vez de ficar quebrado.

set -euo pipefail

USUARIO=celestia
BASE=/opt/celestia/finance
BRANCH="${BRANCH:-}"

log() { printf '\n\033[1;36m→ %s\033[0m\n' "$*"; }
erro() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || erro "rode como root"
[ -d "$BASE/.git" ] || erro "$BASE não é um clone git — rode o instalar.sh primeiro"

cd "$BASE"
ANTERIOR="$(git rev-parse HEAD)"
[ -n "$BRANCH" ] || BRANCH="$(git rev-parse --abbrev-ref HEAD)"

log "backup antes de atualizar"
/usr/local/bin/celestia-backup || echo "  (backup falhou — seguindo mesmo assim)"

log "buscando $BRANCH"
sudo -u "$USUARIO" git fetch --quiet origin "$BRANCH"
sudo -u "$USUARIO" git checkout --quiet "$BRANCH"
sudo -u "$USUARIO" git reset --hard --quiet "origin/$BRANCH"
NOVO="$(git rev-parse HEAD)"

if [ "$ANTERIOR" = "$NOVO" ]; then
  log "já está na versão mais recente ($(git log -1 --format=%s))"
  exit 0
fi

log "de ${ANTERIOR:0:7} para ${NOVO:0:7} — $(git log -1 --format=%s)"

reverter() {
  log "build falhou — voltando para ${ANTERIOR:0:7}"
  sudo -u "$USUARIO" git reset --hard --quiet "$ANTERIOR"
  sudo -u "$USUARIO" npm ci --no-audit --no-fund --silent || true
  sudo -u "$USUARIO" npm run build || true
  systemctl restart celestia-finance-motor celestia-finance-site
  erro "atualização revertida; o site voltou à versão anterior"
}
trap reverter ERR

log "dependências"
python3 -m pip install --quiet --upgrade openpyxl
sudo -u "$USUARIO" npm ci --no-audit --no-fund --silent

log "testes do motor"
sudo -u "$USUARIO" python3 -m pytest -q || erro "testes falharam — atualização abortada"

log "build do site"
sudo -u "$USUARIO" npm run build

trap - ERR

log "reiniciando serviços"
systemctl restart celestia-finance-motor
systemctl restart celestia-finance-site

sleep 3
if systemctl is-active --quiet celestia-finance-motor && systemctl is-active --quiet celestia-finance-site; then
  printf '\n\033[1;32m✓ atualizado para %s\033[0m\n\n' "${NOVO:0:7}"
else
  erro "serviços não subiram — journalctl -u celestia-finance-site -n 50"
fi
