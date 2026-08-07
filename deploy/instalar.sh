#!/usr/bin/env bash
# Instalação inicial do celest.ia (Finance + Flights) num VPS Ubuntu/Debian.
# Rode UMA VEZ, como root:
#
#   curl -fsSL https://raw.githubusercontent.com/rogeriomonea-del/Celst.ia-Finance/main/deploy/instalar.sh | bash
#   # ou, com o repositório já clonado:
#   sudo bash deploy/instalar.sh
#
# Idempotente: rodar de novo não estraga nada que já existe.

set -euo pipefail

# Domínios. Os padrões abaixo são os do projeto; sobrescreva por variável para
# usar outros. DOMINIO_ALIAS redireciona (301) para DOMINIO_FLIGHTS.
DOMINIO_FLIGHTS="${DOMINIO_FLIGHTS:-celestiaflights.com}"
DOMINIO_ALIAS="${DOMINIO_ALIAS:-celestiaflights.cloud}"
DOMINIO_FINANCE="${DOMINIO_FINANCE:-financas.celestiaflights.com}"

# Usuário do acesso protegido ao app financeiro (a senha é gerada aqui).
USUARIO_FINANCE="${USUARIO_FINANCE:-rogerio}"
REPO_FINANCE="${REPO_FINANCE:-https://github.com/rogeriomonea-del/Celst.ia-Finance.git}"
REPO_FLIGHTS="${REPO_FLIGHTS:-https://github.com/rogeriomonea-del/Celest.ia-v2-Alpha.git}"
BRANCH_FINANCE="${BRANCH_FINANCE:-main}"
BRANCH_FLIGHTS="${BRANCH_FLIGHTS:-main}"

USUARIO=celestia
BASE=/opt/celestia
DADOS=/var/lib/celestia
CONFIG=/etc/celestia

log() { printf '\n\033[1;36m→ %s\033[0m\n' "$*"; }
erro() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || erro "rode como root (sudo bash deploy/instalar.sh)"

# --- 1. Pacotes -------------------------------------------------------------
log "instalando pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git nginx python3 python3-pip python3-venv openssl ca-certificates

if ! command -v node >/dev/null || [ "$(node -v | sed 's/v\([0-9]*\).*/\1/')" -lt 18 ]; then
  log "instalando Node.js 22 LTS"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi

log "versões: node $(node -v) · npm $(npm -v) · python $(python3 --version | cut -d' ' -f2)"

# --- 2. Usuário e diretórios ------------------------------------------------
if ! id "$USUARIO" >/dev/null 2>&1; then
  log "criando usuário de serviço $USUARIO (sem shell de login)"
  useradd --system --create-home --home-dir /home/$USUARIO --shell /usr/sbin/nologin "$USUARIO"
fi

mkdir -p "$BASE" "$DADOS/finance" "$CONFIG"
chown -R "$USUARIO:$USUARIO" "$BASE" "$DADOS"
chmod 750 "$DADOS" "$DADOS/finance"

# --- 3. Código --------------------------------------------------------------
clonar_ou_atualizar() {
  local repo="$1" destino="$2" branch="$3"
  if [ -d "$destino/.git" ]; then
    log "atualizando $destino"
    sudo -u "$USUARIO" git -C "$destino" fetch --quiet origin "$branch"
    sudo -u "$USUARIO" git -C "$destino" checkout --quiet "$branch"
    sudo -u "$USUARIO" git -C "$destino" reset --hard --quiet "origin/$branch"
  else
    log "clonando $repo → $destino"
    sudo -u "$USUARIO" git clone --quiet --branch "$branch" "$repo" "$destino"
  fi
}

clonar_ou_atualizar "$REPO_FINANCE" "$BASE/finance" "$BRANCH_FINANCE"
clonar_ou_atualizar "$REPO_FLIGHTS" "$BASE/flights" "$BRANCH_FLIGHTS"

# --- 4. Configuração e segredos --------------------------------------------
if [ ! -f "$CONFIG/finance.env" ]; then
  log "gerando $CONFIG/finance.env (token de API aleatório)"
  TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-40)"
  cat > "$CONFIG/finance.env" <<EOF
# Configuração do celest.ia Finance. Arquivo lido pelos serviços systemd.
# Mantenha 0600 — contém segredo.

# Onde ficam o banco SQLite dos extratos e os arquivos originais importados.
CELESTIA_DATA_DIR=$DADOS/finance

# Declara que o storage é durável (VPS tem disco real, ao contrário de serverless).
CELESTIA_DATA_DURAVEL=1

# Token exigido nas rotas do módulo de extratos (Authorization: Bearer ...).
CELESTIA_API_TOKEN=$TOKEN

# Tamanho máximo de upload em MB (o nginx também limita, em 20m).
CELESTIA_MAX_UPLOAD_MB=15

# Opcional: cotações da B3 via brapi.dev.
# BRAPI_TOKEN=

# Opcional: parecer analítico via Claude na plataforma de passagens.
# ANTHROPIC_API_KEY=
EOF
  chmod 600 "$CONFIG/finance.env"
  chown root:root "$CONFIG/finance.env"
else
  log "$CONFIG/finance.env já existe — preservado"
fi

# Senha do app financeiro (autenticação HTTP no nginx). O app expõe patrimônio
# e extratos: sem isso, qualquer um que descobrir o endereço veria tudo.
if [ ! -f "$CONFIG/htpasswd" ]; then
  SENHA_FINANCE="$(head -c 18 /dev/urandom | base64 | tr -d '=+/' | cut -c1-20)"
  HASH="$(openssl passwd -apr1 "$SENHA_FINANCE")"
  printf '%s:%s\n' "$USUARIO_FINANCE" "$HASH" > "$CONFIG/htpasswd"
  chmod 640 "$CONFIG/htpasswd"
  chown root:www-data "$CONFIG/htpasswd"
  printf 'usuario: %s\nsenha:   %s\n' "$USUARIO_FINANCE" "$SENHA_FINANCE" > "$CONFIG/senha-financas.txt"
  chmod 600 "$CONFIG/senha-financas.txt"
  log "senha do app financeiro gerada em $CONFIG/senha-financas.txt"
else
  log "$CONFIG/htpasswd já existe — senha preservada"
fi

# --- 5. Dependências e build ------------------------------------------------
log "instalando dependência do motor Python (openpyxl)"
python3 -m pip install --quiet --upgrade openpyxl

log "compilando o site (npm ci && npm run build)"
cd "$BASE/finance"
sudo -u "$USUARIO" npm ci --no-audit --no-fund --silent
sudo -u "$USUARIO" npm run build

if [ -d "$BASE/flights/celestia_dashboard" ]; then
  log "compilando o celest.ia Flights"
  cd "$BASE/flights/celestia_dashboard"
  sudo -u "$USUARIO" npm ci --no-audit --no-fund --silent
  sudo -u "$USUARIO" npm run build
fi

# --- 6. Serviços ------------------------------------------------------------
log "instalando serviços systemd"
install -m 644 "$BASE/finance/deploy/celestia-finance-motor.service" /etc/systemd/system/
install -m 644 "$BASE/finance/deploy/celestia-finance-site.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now celestia-finance-motor.service
systemctl enable --now celestia-finance-site.service

# --- 7. nginx ---------------------------------------------------------------
log "configurando nginx"
CONF=/etc/nginx/sites-available/celestia
install -m 644 "$BASE/finance/deploy/nginx-celestia.conf" "$CONF"

sed -i "s/DOMINIO_FLIGHTS/$DOMINIO_FLIGHTS/g; s/DOMINIO_ALIAS/$DOMINIO_ALIAS/g; s/DOMINIO_FINANCE/$DOMINIO_FINANCE/g" "$CONF"

# Sem pilha IPv6 no host, `listen [::]:80` impede o nginx de subir. O VPS da
# Hostinger tem IPv6, mas containers e algumas VMs não — então checamos.
if [ ! -f /proc/net/if_inet6 ]; then
  log "host sem IPv6 — removendo os listen [::] da configuração"
  sed -i '/listen \[::\]/d' "$CONF"
fi

ln -sfn "$CONF" /etc/nginx/sites-enabled/celestia
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# --- 8. Backup diário -------------------------------------------------------
log "agendando backup diário às 3h"
install -m 755 "$BASE/finance/deploy/backup.sh" /usr/local/bin/celestia-backup
cat > /etc/cron.d/celestia-backup <<'EOF'
# Backup do banco e dos originais do celest.ia Finance.
0 3 * * * root /usr/local/bin/celestia-backup >> /var/log/celestia-backup.log 2>&1
EOF

# --- 9. Resumo --------------------------------------------------------------
sleep 2
printf '\n\033[1;32m═══ instalação concluída ═══\033[0m\n\n'
systemctl is-active --quiet celestia-finance-motor && echo "  ✓ motor Python ativo (127.0.0.1:8787)" || echo "  ✗ motor NÃO subiu — veja: journalctl -u celestia-finance-motor -n 50"
systemctl is-active --quiet celestia-finance-site && echo "  ✓ site Next.js ativo (127.0.0.1:3000)" || echo "  ✗ site NÃO subiu — veja: journalctl -u celestia-finance-site -n 50"
systemctl is-active --quiet nginx && echo "  ✓ nginx ativo" || echo "  ✗ nginx parado"

echo ""
echo "  Token da API (guarde):"
grep CELESTIA_API_TOKEN "$CONFIG/finance.env"
echo ""
IP_VPS="$(curl -s -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
echo "  Endereços configurados:"
echo "   • Voos:     http://$DOMINIO_FLIGHTS  (e www)"
echo "   • Alias:    http://$DOMINIO_ALIAS → redireciona para o .com"
echo "   • Finanças: http://$DOMINIO_FINANCE  (protegido por senha)"
echo ""
echo "  Acesso ao app financeiro:"
cat "$CONFIG/senha-financas.txt" | sed 's/^/   /'
echo ""
echo "  Próximos passos:"
echo "   1. DNS — aponte no painel do registrador, tipo A, para $IP_VPS:"
echo "        $DOMINIO_FLIGHTS · www · $DOMINIO_ALIAS · www · $DOMINIO_FINANCE"
echo "      Confira com:  dig +short $DOMINIO_FLIGHTS"
echo "   2. HTTPS (só depois do DNS propagar):"
echo "      apt install -y certbot python3-certbot-nginx"
echo "      certbot --nginx -d $DOMINIO_FLIGHTS -d www.$DOMINIO_FLIGHTS \\"
echo "              -d $DOMINIO_ALIAS -d www.$DOMINIO_ALIAS -d $DOMINIO_FINANCE"
echo "   2. Firewall:  ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable"
echo "   3. Atualizações futuras:  sudo bash $BASE/finance/deploy/atualizar.sh"
echo ""
