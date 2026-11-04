# Deploy na Hostinger — celest.ia Finance + Flights no mesmo VPS

Guia de produção para rodar os dois projetos num VPS Ubuntu da Hostinger.

> **Precisa ser VPS**, não hospedagem compartilhada. O celest.ia Finance roda
> dois processos permanentes (Next.js e o motor Python) e grava em disco; o
> plano compartilhado da Hostinger não sustenta isso de forma confiável.
> O plano KVM 1 (1 vCPU, 4 GB) já dá conta dos dois apps com folga.

## O que sai melhor aqui do que na Vercel

Na Vercel o sistema de arquivos é apagado a cada requisição, então o módulo de
extratos não podia gravar nada — o banco e os arquivos originais sumiriam. Num
VPS há disco real, e é aí que ele funciona por inteiro:

- os extratos importados **persistem** (SQLite em `/var/lib/celestia/finance`);
- os arquivos originais ficam guardados de forma imutável e auditável, como a
  rastreabilidade exige;
- sem limite de 10 s por requisição — extrato grande em PDF processa inteiro;
- contas e sessões de login ficam no mesmo disco, com uma sessão só valendo
  para os dois domínios;
- backup diário automático, sob seu controle.

## Arquitetura

```
                    ┌─────────── nginx (80/443) ───────────┐
  financas.dominio ─┤  /api/py/ → 127.0.0.1:8787 (Python)  │
                    │  /        → 127.0.0.1:3000 (Next.js) │
                    ├──────────────────────────────────────┤
  voos.dominio ─────┤  /api/py/ → 127.0.0.1:8787 (Python)  │  ← mesmo motor,
                    │  /        → build estático do Vite   │    mesma sessão
                    └──────────────────────────────────────┘

  /opt/celestia/finance     código do celest.ia Finance
  /opt/celestia/flights     código do celest.ia Flights
  /var/lib/celestia/finance banco SQLite + originais + contas/sessões  ← o que importa
  /etc/celestia/finance.env segredos (0600, fora do repositório)
  /var/backups/celestia     backups diários (14 cópias)
```

Os dois serviços rodam como o usuário `celestia`, sem shell de login, com
blindagem do systemd (`ProtectSystem=strict`, sem escrita fora do diretório de
dados). O motor Python escuta **só em 127.0.0.1** — quem fala com a internet é
o nginx. Os dois hosts encaminham `/api/py/` para esse mesmo motor: é ele que
emite o cookie de sessão, e por isso um login só vale nas duas plataformas.

## Instalação (uma vez)

Conecte no VPS por SSH como root e rode:

```bash
apt update && apt install -y git
git clone https://github.com/rogeriomonea-del/Celst.ia-Finance.git /tmp/celestia
bash /tmp/celestia/deploy/instalar.sh
```

Só isso. O script instala Node 22, Python e nginx; cria o usuário de serviço;
clona os dois repositórios; gera o `finance.env` com um token aleatório e as
variáveis do login (SMTP e Google comentadas, prontas para preencher);
compila os dois sites; sobe os serviços; configura o nginx e agenda o backup.
Ele é idempotente — rodar de novo não quebra o que já existe, e numa
instalação anterior ele **acrescenta** ao `finance.env` as variáveis que
faltarem, sem mexer nas que você já definiu.

No fim ele imprime o **token da API** (acesso de máquina às rotas de extratos)
e um resumo do login: como entrar, onde ver o código sem SMTP e a URI de
retorno do Google.

### Endereços e DNS

A topologia configurada por padrão:

| Endereço | Serve | Acesso |
|---|---|---|
| `celestiaflights.com` (+ `www`) | celest.ia Flights | público; login do app nas áreas de conta |
| `celestiaflights.cloud` (+ `www`) | redireciona 301 para o `.com` | público |
| `financas.celestiaflights.com` | celest.ia Finance | **login do app** (código por e-mail ou Google) |

Um domínio só é o canônico (o `.com`); o `.cloud` redireciona. Isso protege a
marca sem manter dois sites iguais no ar nem dividir o ranqueamento no Google.

**Antes de instalar, aponte o DNS.** No painel do registrador, crie registros
do tipo `A` para o IP do VPS (`82.112.244.246` no caso deste servidor):

| Nome | Tipo | Valor |
|---|---|---|
| `@` (celestiaflights.com) | A | IP do VPS |
| `www` | A | IP do VPS |
| `financas` | A | IP do VPS |
| `@` (celestiaflights.cloud) | A | IP do VPS |
| `www` (no .cloud) | A | IP do VPS |

Confira a propagação antes de seguir:

```bash
dig +short celestiaflights.com          # tem que devolver o IP do VPS
dig +short financas.celestiaflights.com
```

Para usar outros domínios, passe as variáveis:

```bash
DOMINIO_FLIGHTS=outrodominio.com \
DOMINIO_ALIAS=outrodominio.net \
DOMINIO_FINANCE=painel.outrodominio.com \
bash /tmp/celestia/deploy/instalar.sh
```

O instalador deriva o domínio do cookie de sessão do `DOMINIO_FINANCE`
(`painel.outrodominio.com` → `.outrodominio.com`). Se o app financeiro ficar
num domínio sem subdomínio, o cookie fica restrito a ele e não há SSO com a
plataforma de voos.

### Login e contas

O celest.ia Finance mostra patrimônio, extratos bancários e faturas, então
nada dele é servido sem sessão: o `middleware.ts` do Next redireciona quem não
tem o cookie para `/entrar`, e o motor Python responde `401` a toda rota de
dados sem sessão válida. **Não há mais senha HTTP no nginx** — se você vem de
uma instalação antiga, o `/etc/celestia/htpasswd` e o `senha-financas.txt`
deixaram de ser lidos e podem ser apagados.

**Como se entra**

1. **Código por e-mail.** Em `/entrar`, informe o e-mail e peça o código: seis
   dígitos, válidos por 10 minutos, uso único, no máximo 5 pedidos a cada 15
   minutos por e-mail. Sem SMTP configurado o código **não é enviado** — ele
   vai para o log do motor e a tela avisa isso (veja abaixo).
2. **Entrar com Google.** O botão só aparece quando `GOOGLE_CLIENT_ID` e
   `GOOGLE_CLIENT_SECRET` estão no `finance.env`.

**Aprovação de contas**

- O **primeiro** e-mail que entra vira administrador e já fica ativo.
- As contas seguintes nascem **inativas** ("sua conta foi criada e aguarda
  aprovação do administrador") até um admin aprovar em `/admin`.
- `CELESTIA_CADASTRO_ABERTO=1` faz toda conta nova entrar ativa (só para
  ambientes fechados de confiança).
- Desativar uma conta derruba as sessões dela na hora.

**A tela `/admin`** (só para `admin=true`): contas pendentes com botão
"Aprovar"; tabela de contas (criada em, último acesso, sessões ativas,
provedores, Admin/Ativa) com Aprovar/Desativar, Tornar/Remover admin e
"Encerrar sessões"; sessões ativas (IP, navegador, última atividade) com
encerramento individual; e acessos recentes (login, código errado, bloqueio,
alterações feitas por admins). O backend recusa (`409`) rebaixar ou desativar
a si mesmo e remover o último admin.

**Uma sessão para os dois domínios (SSO)**

O cookie `celestia_sessao` é `HttpOnly`, `SameSite=Lax`, `Secure`, dura 30
dias e sai com `Domain=CELESTIA_COOKIE_DOMINIO` — `.celestiaflights.com`.
O instalador só preenche esse domínio quando o host de finanças é
**subdomínio** do host de voos (`financas.celestiaflights.com` sob
`celestiaflights.com`); em qualquer outro arranjo ele fica vazio e cada site
tem o seu login, sem SSO — um domínio adivinhado (`.com.br`, `.hstgr.cloud`)
faria o navegador descartar o cookie ou mandá-lo para sites de terceiros.
Quem entra em `financas.celestiaflights.com` já está logado em
`celestiaflights.com`, e vice-versa, porque o nginx encaminha `/api/py/` dos
dois hosts para o mesmo motor. Por isso o **HTTPS é obrigatório**: o cookie
`Secure` não viaja em `http://`. (O único caso sem `Secure` é
`CELESTIA_COOKIE_INSEGURO=1`, que o `scripts/dev.sh` usa em `localhost`.)

**Ver o código no log quando não há SMTP**

```bash
journalctl -u celestia-finance-motor -f
# ... celestia.correio WARNING SMTP não configurado — código de voce@exemplo.com: 123456
```

Serve para entrar pela primeira vez (e virar admin) antes de configurar o
e-mail. Com SMTP configurado o código nunca vai para o log — em caso de falha
no envio fica registrado só o tipo do erro.

**Configurar o SMTP na Hostinger**

1. No hPanel, em *E-mails*, crie uma caixa no domínio (ex.:
   `nao-responda@celestiaflights.com`) e anote a senha.
2. Em `/etc/celestia/finance.env`, descomente e preencha:

   ```ini
   CELESTIA_SMTP_HOST=smtp.hostinger.com
   CELESTIA_SMTP_PORT=465            # SSL; 587 também funciona (STARTTLS)
   CELESTIA_SMTP_USER=nao-responda@celestiaflights.com
   CELESTIA_SMTP_SENHA=senha-da-caixa
   CELESTIA_SMTP_DE=nao-responda@celestiaflights.com
   CELESTIA_APP_NOME=celest.ia
   ```

3. `systemctl restart celestia-finance-motor` e peça um código em `/entrar`.
   Se der `502` com "falha ao enviar", o motivo (tipo do erro, sem o código)
   está no `journalctl -u celestia-finance-motor`.

Para outro provedor (Gmail, Brevo, SES…) basta trocar host, porta e
credenciais; o motor usa só SMTP com SSL/STARTTLS, sem SDK.

**Configurar o "Entrar com Google"**

1. Em [console.cloud.google.com](https://console.cloud.google.com) → *APIs e
   serviços* → *Tela de consentimento OAuth*: tipo **Externo**, nome
   "celest.ia", e-mail de suporte; escopos `email` e `profile` bastam. Em
   *Público* publique o app (ou deixe em teste e cadastre os e-mails de
   teste).
2. *Credenciais* → *Criar credenciais* → *ID do cliente OAuth* → tipo
   **Aplicativo da Web**. Em *URIs de redirecionamento autorizados* cadastre
   **exatamente**:

   ```
   https://financas.celestiaflights.com/api/py/auth/google/retorno
   ```

   (isto é `{CELESTIA_URL_BASE}/api/py/auth/google/retorno`; uma barra ou
   `www` a mais e o Google devolve `redirect_uri_mismatch`). Em *Origens
   JavaScript autorizadas* pode deixar vazio — o fluxo é todo no servidor.
3. Copie o ID e o segredo para o `finance.env`:

   ```ini
   GOOGLE_CLIENT_ID=000000000000-exemplo.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-exemplo
   ```

4. `systemctl restart celestia-finance-motor`. O botão "Entrar com Google"
   aparece em `/entrar` e na plataforma de voos. A plataforma de voos inicia o
   login pelo mesmo endpoint e volta para `celestiaflights.com` ao final — o
   motor só aceita destinos cujo host termina com o domínio do cookie.

Uma conta criada pelo Google segue a mesma regra de aprovação (a primeira é
admin, as outras aguardam). Se o Google devolver erro, a tela de login mostra
`?erro=google_cancelado` ou `?erro=conta_inativa` com a explicação.

O host de finanças também vai com `X-Robots-Tag: noindex`, e qualquer nome não
reconhecido (inclusive o IP direto) recebe `444` e não serve nada — sem isso, um
varredor de IP cairia no primeiro site configurado.

### HTTPS

Só depois do DNS propagar:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d celestiaflights.com -d www.celestiaflights.com \
        -d celestiaflights.cloud -d www.celestiaflights.cloud \
        -d financas.celestiaflights.com
```

O certbot ajusta os blocos do nginx e renova sozinho. Rodar o `instalar.sh`
de novo depois disso **não** sobrescreve essa configuração: ele detecta o
HTTPS do certbot, grava o modelo novo em
`/etc/nginx/sites-available/celestia.novo`, mostra o `diff` e deixa a
mesclagem para você. Sem HTTPS o login não
funciona em produção: o cookie de sessão é `Secure`.

### Capacidade

Um KVM 2 (2 vCPU, 8 GB) já roda os dois projetos com folga. Em planos maiores
(KVM 8: 8 vCPU, 32 GB) sobra bastante margem — o gargalo do módulo de extratos
é I/O de disco na importação, não CPU, e o SQLite em NVMe dá conta de anos de
lançamentos com consultas em milissegundos.

## Atualizar

```bash
sudo bash /opt/celestia/finance/deploy/atualizar.sh
```

Faz backup, puxa o código, **roda a suíte de testes** e só então recompila e
reinicia. Se o build ou os testes falharem, ele volta para a versão anterior
automaticamente — o site não fica quebrado. Também acrescenta ao
`finance.env` as variáveis que a versão nova passou a usar (sem tocar nas
existentes) e avisa se o nginx instalado ainda tem o `auth_basic` antigo ou
está sem o `/api/py/` no host de voos — como o certbot reescreve os blocos, essa
parte é ajustada à mão comparando com `deploy/nginx-celestia.conf`.

**Dados importados antes do login por conta.** Até esta versão, o módulo de
extratos gravava tudo na raiz de dados (`/var/lib/celestia/finance/extratos.db`
e `originais/`). Agora cada conta logada enxerga só `usuarios/<id>/`, então
esses dados não aparecem para ninguém até serem movidos para a conta dona. O
`atualizar.sh` avisa quando encontra esse caso; para migrar (move, não copia,
nada é apagado):

```bash
# 1. entre no app uma vez — o primeiro login vira administrador
# 2. mova o banco e os originais para essa conta
cd /opt/celestia/finance
sudo -u celestia env CELESTIA_DATA_DIR=/var/lib/celestia/finance \
  python3 -m engine.auth.migrar_dados --para seu@email.com      # --simular só mostra
```

Para publicar uma branch específica antes de mergear:

```bash
sudo BRANCH=claude/extratos-bancarios bash /opt/celestia/finance/deploy/atualizar.sh
```

## Backup e restauração

O backup roda todo dia às 3h e guarda 14 cópias em `/var/backups/celestia`.
Também roda antes de cada atualização.

```bash
sudo /usr/local/bin/celestia-backup                  # sob demanda
DESTINO=/mnt/externo sudo /usr/local/bin/celestia-backup   # outro destino
```

Restaurar:

```bash
systemctl stop celestia-finance-motor
cp /var/backups/celestia/extratos-AAAAMMDD-HHMMSS.db /var/lib/celestia/finance/extratos.db
tar -xzf /var/backups/celestia/originais-AAAAMMDD-HHMMSS.tar.gz -C /var/lib/celestia/finance
chown -R celestia:celestia /var/lib/celestia/finance
systemctl start celestia-finance-motor
```

O banco é copiado pela API `.backup` do SQLite, que sai consistente mesmo com
o sistema em uso — não é um `cp` do arquivo.

## Variáveis de ambiente (`/etc/celestia/finance.env`)

| Variável | Para quê |
|---|---|
| `CELESTIA_DATA_DIR` | Onde ficam o banco, os originais e as contas/sessões. Padrão `/var/lib/celestia/finance` |
| `CELESTIA_DATA_DURAVEL` | `1` declara disco real (num VPS, sempre) |
| `CELESTIA_API_TOKEN` | Token para acesso de **máquina** às rotas de extratos (`Authorization: Bearer`); no navegador vale a sessão |
| `CELESTIA_MAX_UPLOAD_MB` | Limite de upload no motor (padrão 15; o nginx limita em 20) |
| `CELESTIA_URL_BASE` | URL pública do app financeiro (`https://financas.celestiaflights.com`); base da URI de retorno do Google |
| `CELESTIA_COOKIE_DOMINIO` | `Domain` do cookie de sessão (`.celestiaflights.com` → vale nos dois sites). Vazio = só o host |
| `CELESTIA_COOKIE_INSEGURO` | `1` tira o `Secure` do cookie — **só** desenvolvimento em `http://` |
| `CELESTIA_HOSTS_PERMITIDOS` | Hosts extras (vírgula) para onde o login pode redirecionar |
| `CELESTIA_SMTP_HOST` / `_PORT` / `_USER` / `_SENHA` / `_DE` | Envio do código por e-mail (465 SSL ou 587 STARTTLS). Sem `HOST`+`USER`, o código vai para o log |
| `CELESTIA_APP_NOME` | Nome no assunto e corpo do e-mail (padrão `celest.ia`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Habilitam o "Entrar com Google" |
| `CELESTIA_CADASTRO_ABERTO` | `1` dispensa a aprovação de contas novas (padrão `0`) |
| `CELESTIA_AUTH_ABERTO` | `1` abre a API sem login — **nunca** em produção |
| `BRAPI_TOKEN` | Opcional — cotações da B3 |
| `ANTHROPIC_API_KEY` | Opcional — parecer analítico via Claude |

Depois de editar: `systemctl restart celestia-finance-motor celestia-finance-site`.

## Diagnóstico

```bash
systemctl status celestia-finance-motor celestia-finance-site nginx
journalctl -u celestia-finance-motor -f     # log do motor (inclui o código de login sem SMTP)
journalctl -u celestia-finance-site -f      # log do site
curl -s 127.0.0.1:8787/health               # motor vivo?
curl -s 127.0.0.1:8787/auth/config          # login: google/envio_real configurados?
curl -sI 127.0.0.1:3000                     # site vivo?
nginx -t                                    # configuração válida?
du -sh /var/lib/celestia/finance/*          # espaço dos dados
```

| Sintoma | Causa provável |
|---|---|
| 502 no navegador | Um dos serviços caiu — veja o `journalctl` |
| 413 ao subir arquivo | Passou de 20 MB; ajuste `client_max_body_size` e `CELESTIA_MAX_UPLOAD_MB` |
| 401 nas rotas de extrato | Sem sessão (no navegador: cai em `/entrar`) ou token Bearer errado |
| Volta para `/entrar` logo após entrar | Cookie não gravou: site em `http://` (cookie é `Secure`) ou `CELESTIA_COOKIE_DOMINIO` não bate com o host |
| Logado em finanças mas não em voos | `CELESTIA_COOKIE_DOMINIO` sem o ponto inicial / domínio errado, ou falta o `location /api/py/` no host de voos |
| "Sua conta aguarda aprovação" | Normal a partir da 2ª conta — aprove em `/admin` |
| Código não chega por e-mail | SMTP não configurado (está no log) ou credenciais erradas (`502` + tipo do erro no log) |
| `redirect_uri_mismatch` no Google | A URI no console não é exatamente `{CELESTIA_URL_BASE}/api/py/auth/google/retorno` |
| Site antigo depois de atualizar | Build não rodou; confira o fim do `atualizar.sh` |

## Segurança

- Firewall: libere só 22, 80 e 443 (`ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable`).
- SSH por chave, sem senha (`PasswordAuthentication no` em `/etc/ssh/sshd_config`).
- O `finance.env` é 0600 e pertence ao root; os serviços leem sem expor no processo.
- Os dados financeiros nunca saem do servidor: o único acesso à internet do
  motor é a cotação da brapi, que envia apenas tickers (e o SMTP/Google, se
  configurados, que recebem só e-mail e código).
- O login nunca grava senha: códigos ficam com hash e expiram; o cookie de
  sessão é `HttpOnly`, `Secure`, `SameSite=Lax`.
- Deixe `CELESTIA_CADASTRO_ABERTO` e `CELESTIA_AUTH_ABERTO` em `0` em
  produção; aprove contas uma a uma em `/admin` e revise os acessos recentes
  de vez em quando.
