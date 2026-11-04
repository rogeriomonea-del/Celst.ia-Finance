#!/usr/bin/env bash
# Atualiza o celest.ia Finance no VPS: puxa o código, recompila e reinicia.
#
#   sudo bash /opt/celestia/finance/deploy/atualizar.sh            # branch atual
#   sudo BRANCH=claude/extratos-bancarios bash .../atualizar.sh    # outra branch
#
# Faz backup antes de mexer e reverte o código se o build falhar — o site
# continua no ar com a versão anterior em vez de ficar quebrado.
#
# Também acrescenta ao /etc/celestia/finance.env as variáveis novas que uma
# versão do código passou a precisar (sem tocar nas que já existem) e reinicia
# os serviços para que elas valham.

set -euo pipefail

USUARIO=celestia
BASE=/opt/celestia/finance
CONFIG=/etc/celestia
BRANCH="${BRANCH:-}"

log() { printf '\n\033[1;36m→ %s\033[0m\n' "$*"; }
erro() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- Variáveis de ambiente do login ------------------------------------------
# (As funções abaixo são idênticas às do instalar.sh — mantenha em sincronia.)

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

# Host do app financeiro: por variável, ou lido do nginx já instalado (o
# server_name do bloco que encaminha para o Next em :3000).
descobrir_dominio_finance() {
  local conf=/etc/nginx/sites-available/celestia dominio=""
  if [ -f "$conf" ]; then
    dominio="$(awk '/^[[:space:]]*server_name/ {sn=$2} /127\.0\.0\.1:3000/ {print sn; exit}' "$conf" | tr -d ';')"
  fi
  printf '%s' "${dominio:-financas.celestiaflights.com}"
}

# Host da plataforma de voos: por variável, ou o server_name do bloco que
# serve o build estático (root .../celestia_dashboard/dist).
descobrir_dominio_flights() {
  local conf=/etc/nginx/sites-available/celestia dominio=""
  if [ -f "$conf" ]; then
    dominio="$(awk '/^[[:space:]]*server_name/ {sn=$2} /celestia_dashboard\/dist/ {print sn; exit}' "$conf" | tr -d ';')"
  fi
  printf '%s' "${dominio:-celestiaflights.com}"
}

# Completa o finance.env e, se entrou variável nova, avisa.
completar_env() {
  [ -f "$CONFIG/finance.env" ] || { echo "  ($CONFIG/finance.env não existe — rode o instalar.sh)"; return 0; }
  local dominio flights
  dominio="${DOMINIO_FINANCE:-$(descobrir_dominio_finance)}"
  flights="${DOMINIO_FLIGHTS:-$(descobrir_dominio_flights)}"
  log "conferindo variáveis do login em $CONFIG/finance.env (host $dominio; voos em $flights)"
  VARIAVEIS_ADICIONADAS=0
  completar_env_login "$CONFIG/finance.env" "$dominio" "$flights"
  if [ "$VARIAVEIS_ADICIONADAS" -gt 0 ]; then
    echo "  $VARIAVEIS_ADICIONADAS variável(is) nova(s) acrescentada(s) com valores padrão — revise em $CONFIG/finance.env"
  else
    echo "  nada a acrescentar"
  fi
}

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
  # Mesmo sem código novo, o env pode estar incompleto (ex.: instalação antiga).
  completar_env
  if [ "${VARIAVEIS_ADICIONADAS:-0}" -gt 0 ]; then
    log "reiniciando serviços para carregar o env"
    systemctl restart celestia-finance-motor celestia-finance-site
  fi
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

# Variáveis que a versão nova passou a usar (sem mexer nas existentes).
completar_env

# O nginx instalado pode estar desatualizado em relação ao deploy/nginx-celestia.conf
# (o certbot reescreve os blocos, então não é reaplicado automaticamente).
# Avisa quando ainda tem o auth_basic antigo ou falta o /api/py/ no host de voos.
CONF=/etc/nginx/sites-available/celestia
if [ -f "$CONF" ]; then
  if grep -Eq '^[[:space:]]*auth_basic' "$CONF" || [ "$(grep -c 'location /api/py/' "$CONF")" -lt 2 ]; then
    log "aviso: $CONF está desatualizado em relação a $BASE/deploy/nginx-celestia.conf"
    echo "  O login agora é do app: remova o auth_basic/auth_basic_user_file do host de finanças"
    echo "  e acrescente no host de voos o mesmo 'location /api/py/ { proxy_pass http://127.0.0.1:8787/; ... }'."
    echo "  Depois:  nginx -t && systemctl reload nginx"
  fi
fi

# Dados importados ANTES do login por conta ficam na raiz de dados, que agora
# só o token de máquina alcança; cada conta logada enxerga usuarios/<id>/.
DADOS_FINANCE="$(grep -E '^CELESTIA_DATA_DIR=' "$CONFIG/finance.env" 2>/dev/null | cut -d= -f2-)"
DADOS_FINANCE="${DADOS_FINANCE:-/var/lib/celestia/finance}"
if [ -s "$DADOS_FINANCE/extratos.db" ] && [ ! -d "$DADOS_FINANCE/usuarios" ]; then
  log "aviso: há extratos importados antes do login por conta em $DADOS_FINANCE/extratos.db"
  echo "  Eles NÃO aparecem para quem entra no app até serem movidos para a conta dona:"
  echo "    1. entre no app uma vez (o primeiro login vira administrador);"
  echo "    2. sudo -u $USUARIO env CELESTIA_DATA_DIR=$DADOS_FINANCE \\"
  echo "         python3 -m engine.auth.migrar_dados --para seu@email.com   (em $BASE)"
  echo "  Nada é apagado: o comando move extratos.db e originais/ para usuarios/<id>/."
fi

log "reiniciando serviços"
systemctl restart celestia-finance-motor
systemctl restart celestia-finance-site

sleep 3
if systemctl is-active --quiet celestia-finance-motor && systemctl is-active --quiet celestia-finance-site; then
  printf '\n\033[1;32m✓ atualizado para %s\033[0m\n\n' "${NOVO:0:7}"
else
  erro "serviços não subiram — journalctl -u celestia-finance-site -n 50"
fi
