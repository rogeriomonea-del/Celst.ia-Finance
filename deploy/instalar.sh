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

# --- Variáveis de ambiente do login ------------------------------------------
# (As duas funções abaixo são idênticas às do atualizar.sh — mantenha em sincronia.)

# Domínio do cookie de sessão: só quando o app financeiro é SUBDOMÍNIO do
# domínio de voos (financas.celestiaflights.com sob celestiaflights.com →
# .celestiaflights.com, e a mesma sessão vale nos dois apps). Em qualquer
# outro caso fica vazio e o cookie vale só no host do app financeiro — nunca
# se tenta adivinhar o domínio por contagem de pontos: "minhaempresa.com.br"
# viraria ".com.br" (sufixo público, o navegador descarta o cookie) e
# "srv123.hstgr.cloud" viraria ".hstgr.cloud" (o token iria para qualquer VPS
# de terceiros nesse sufixo).
dominio_cookie() {
  local finance="$1" flights="${2:-}"
  if [ -n "$flights" ] && [ "$finance" != "$flights" ] && [ "${finance%.$flights}" != "$finance" ]; then
    printf '.%s' "$flights"
  fi
}

# Garante que uma variável exista no arquivo de ambiente, sem duplicar.
#   garantir_variavel ARQUIVO 'NOME=valor' 'comentário 1' 'comentário 2'...
#   garantir_variavel ARQUIVO '# NOME=' 'comentário'      # deixa desativada
# Se NOME já aparece no arquivo (ativa ou comentada), nada muda — o valor que
# o administrador escreveu é preservado. Se não aparece, acrescenta no fim,
# precedida dos comentários.
garantir_variavel() {
  local arquivo="$1" definicao="$2" nome comentario
  shift 2
  nome="${definicao#\#}"
  nome="${nome# }"
  nome="${nome%%=*}"
  if grep -Eq "^[[:space:]]*#?[[:space:]]*${nome}=" "$arquivo"; then
    return 0
  fi
  {
    echo ""
    for comentario in "$@"; do echo "# $comentario"; done
    echo "$definicao"
  } >> "$arquivo"
  echo "  + $nome"
  VARIAVEIS_ADICIONADAS=$((${VARIAVEIS_ADICIONADAS:-0} + 1))
}

# Acrescenta ao finance.env as variáveis do login que ainda faltam.
completar_env_login() {
  local arquivo="$1" dominio="$2" flights="${3:-}" raiz cookie
  cookie="$(dominio_cookie "$dominio" "$flights")"
  # Caixa de e-mail de exemplo no domínio raiz (financas.x.com → x.com).
  raiz="${cookie#.}"; raiz="${raiz:-$dominio}"
  garantir_variavel "$arquivo" "CELESTIA_URL_BASE=https://$dominio" \
    "--- Login e contas ---------------------------------------------------" \
    "URL pública do app financeiro. Usada nos links e na URI de retorno do" \
    "Google: {CELESTIA_URL_BASE}/api/py/auth/google/retorno"
  garantir_variavel "$arquivo" "CELESTIA_COOKIE_DOMINIO=$cookie" \
    "Domínio do cookie de sessão. Com o ponto na frente (.celestiaflights.com)" \
    "a mesma sessão vale para o app financeiro e para a plataforma de voos." \
    "Vazio = só o host do app financeiro."
  garantir_variavel "$arquivo" "# CELESTIA_SMTP_HOST=smtp.hostinger.com" \
    "SMTP para enviar o código de acesso por e-mail. Enquanto estiver vazio" \
    "o código NÃO é enviado: aparece no log (journalctl -u celestia-finance-motor)."
  garantir_variavel "$arquivo" "# CELESTIA_SMTP_PORT=465" \
    "465 = SSL (Hostinger), 587 = STARTTLS."
  garantir_variavel "$arquivo" "# CELESTIA_SMTP_USER=nao-responda@$raiz" \
    "Caixa remetente (na Hostinger, um e-mail criado no painel do domínio)."
  garantir_variavel "$arquivo" "# CELESTIA_SMTP_SENHA=" \
    "Senha dessa caixa."
  garantir_variavel "$arquivo" "# CELESTIA_SMTP_DE=" \
    "Remetente exibido (opcional; padrão = o usuário acima)."
  garantir_variavel "$arquivo" "CELESTIA_APP_NOME=celest.ia" \
    "Nome que aparece no assunto e no corpo do e-mail."
  garantir_variavel "$arquivo" "# GOOGLE_CLIENT_ID=" \
    "Entrar com Google (opcional). Crie um cliente OAuth 2.0 'Aplicativo da Web'" \
    "em console.cloud.google.com e cadastre EXATAMENTE a URI de retorno:" \
    "https://$dominio/api/py/auth/google/retorno"
  garantir_variavel "$arquivo" "# GOOGLE_CLIENT_SECRET=" \
    "Segredo do cliente OAuth."
  garantir_variavel "$arquivo" "# CELESTIA_CADASTRO_ABERTO=0" \
    "1 = toda conta nova já entra ativa, sem aprovação do administrador." \
    "Padrão 0: o primeiro login vira admin; os seguintes aguardam aprovação em /admin."
}

[ "$(id -u)" -eq 0 ] || erro "rode como root (sudo bash deploy/instalar.sh)"

# --- 1. Pacotes -------------------------------------------------------------
log "instalando pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git nginx python3 python3-pip python3-venv ca-certificates

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

# Token para acesso de máquina às rotas de extratos (Authorization: Bearer ...).
# No navegador o acesso é pela sessão de login, sem token.
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
  log "$CONFIG/finance.env já existe — valores preservados"
fi

# Nas duas situações (arquivo novo ou antigo) acrescenta o que faltar para o
# login — assim uma instalação anterior ganha as variáveis novas sem perder
# as que já tinha.
log "conferindo variáveis do login em $CONFIG/finance.env"
completar_env_login "$CONFIG/finance.env" "$DOMINIO_FINANCE" "$DOMINIO_FLIGHTS"

# A proteção do app financeiro agora é o login do próprio app (código por
# e-mail ou Google, com aprovação de contas em /admin). O htpasswd de
# instalações anteriores não é mais lido pelo nginx.
if [ -f "$CONFIG/htpasswd" ] || [ -f "$CONFIG/senha-financas.txt" ]; then
  log "aviso: $CONFIG/htpasswd e senha-financas.txt são de uma instalação anterior e NÃO são mais usados (o login é do app). Pode apagá-los."
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
# Se já estavam no ar, recarrega com o finance.env possivelmente atualizado.
systemctl restart celestia-finance-motor.service celestia-finance-site.service

# --- 7. nginx ---------------------------------------------------------------
log "configurando nginx"
CONF=/etc/nginx/sites-available/celestia
montar_conf_nginx() {
  # Gera a configuração a partir do modelo, com os domínios e sem IPv6 se o
  # host não tiver (sem isso `listen [::]:80` impede o nginx de subir).
  local destino="$1"
  install -m 644 "$BASE/finance/deploy/nginx-celestia.conf" "$destino"
  sed -i "s/DOMINIO_FLIGHTS/$DOMINIO_FLIGHTS/g; s/DOMINIO_ALIAS/$DOMINIO_ALIAS/g; s/DOMINIO_FINANCE/$DOMINIO_FINANCE/g" "$destino"
  if [ ! -f /proc/net/if_inet6 ]; then
    log "host sem IPv6 — removendo os listen [::] da configuração"
    sed -i '/listen \[::\]/d' "$destino"
  fi
}

if [ -f "$CONF" ] && grep -Eq 'managed by Certbot|listen[[:space:]]+(\[::\]:)?443' "$CONF"; then
  # Já tem HTTPS do certbot: sobrescrever apagaria os certificados e o
  # redirecionamento 80→443 — e, como o cookie de sessão é Secure, o login
  # pararia de funcionar até rodar o certbot de novo. Gera o modelo novo ao
  # lado e deixa a mesclagem para quem opera.
  montar_conf_nginx "$CONF.novo"
  log "$CONF tem HTTPS do certbot — preservado. Modelo novo em $CONF.novo"
  if ! diff -q "$CONF" "$CONF.novo" >/dev/null 2>&1; then
    echo "  Diferenças (atual → novo):"
    diff -u "$CONF" "$CONF.novo" | sed 's/^/    /' | head -80 || true
    echo "  Mescle o que falta (ex.: remover auth_basic; location /api/py/ no host de voos),"
    echo "  depois:  nginx -t && systemctl reload nginx"
  fi
  NGINX_PRESERVADO=1
else
  montar_conf_nginx "$CONF"
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
echo "  Token da API para acesso de máquina (guarde):"
grep CELESTIA_API_TOKEN "$CONFIG/finance.env"
echo ""
IP_VPS="$(curl -s -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
echo "  Endereços configurados:"
echo "   • Voos:     http://$DOMINIO_FLIGHTS  (e www)"
echo "   • Alias:    http://$DOMINIO_ALIAS → redireciona para o .com"
echo "   • Finanças: http://$DOMINIO_FINANCE  (protegido pelo login do app)"
echo ""
echo "  Login e contas:"
echo "   • Entre em https://$DOMINIO_FINANCE/entrar com seu e-mail. A PRIMEIRA conta"
echo "     que entrar vira administradora; as seguintes aguardam aprovação em /admin."
echo "   • Sem SMTP configurado o código de acesso NÃO é enviado por e-mail — ele"
echo "     aparece no log do motor:  journalctl -u celestia-finance-motor -f"
echo "     Para enviar de verdade, preencha CELESTIA_SMTP_* em $CONFIG/finance.env"
echo "     e rode: systemctl restart celestia-finance-motor"
echo "   • Entrar com Google (opcional): GOOGLE_CLIENT_ID/SECRET no finance.env e,"
echo "     no console do Google, a URI de retorno EXATA:"
echo "       https://$DOMINIO_FINANCE/api/py/auth/google/retorno"
COOKIE_DOMINIO="$(dominio_cookie "$DOMINIO_FINANCE" "$DOMINIO_FLIGHTS")"
if [ -n "$COOKIE_DOMINIO" ]; then
  echo "   • A mesma sessão vale em $DOMINIO_FLIGHTS (cookie em $COOKIE_DOMINIO)."
else
  echo "   • $DOMINIO_FINANCE não é subdomínio de $DOMINIO_FLIGHTS: cada site tem o seu login"
  echo "     (sem SSO). Para compartilhar a sessão, use algo como financas.$DOMINIO_FLIGHTS."
fi
echo ""
echo "  Próximos passos:"
echo "   1. DNS — aponte no painel do registrador, tipo A, para $IP_VPS:"
echo "        $DOMINIO_FLIGHTS · www · $DOMINIO_ALIAS · www · $DOMINIO_FINANCE"
echo "      Confira com:  dig +short $DOMINIO_FLIGHTS"
echo "   2. HTTPS (obrigatório: o cookie de sessão só viaja em HTTPS; só depois do DNS propagar):"
echo "      apt install -y certbot python3-certbot-nginx"
echo "      certbot --nginx -d $DOMINIO_FLIGHTS -d www.$DOMINIO_FLIGHTS \\"
echo "              -d $DOMINIO_ALIAS -d www.$DOMINIO_ALIAS -d $DOMINIO_FINANCE"
echo "   3. Firewall:  ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable"
echo "   4. Atualizações futuras:  sudo bash $BASE/finance/deploy/atualizar.sh"
echo ""
