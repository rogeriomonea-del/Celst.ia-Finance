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
- backup diário automático, sob seu controle.

## Arquitetura

```
                    ┌─────────── nginx (80/443) ───────────┐
  financas.dominio ─┤  /api/py/ → 127.0.0.1:8787 (Python)  │
                    │  /        → 127.0.0.1:3000 (Next.js) │
                    └──────────────────────────────────────┘
  voos.dominio ─────→ arquivos estáticos do build Vite

  /opt/celestia/finance     código do celest.ia Finance
  /opt/celestia/flights     código do celest.ia Flights
  /var/lib/celestia/finance banco SQLite + originais importados  ← o que importa
  /etc/celestia/finance.env segredos (0600, fora do repositório)
  /var/backups/celestia     backups diários (14 cópias)
```

Os dois serviços rodam como o usuário `celestia`, sem shell de login, com
blindagem do systemd (`ProtectSystem=strict`, sem escrita fora do diretório de
dados). O motor Python escuta **só em 127.0.0.1** — quem fala com a internet é
o nginx.

## Instalação (uma vez)

Conecte no VPS por SSH como root e rode:

```bash
apt update && apt install -y git
git clone https://github.com/rogeriomonea-del/Celst.ia-Finance.git /tmp/celestia
bash /tmp/celestia/deploy/instalar.sh
```

Só isso. O script instala Node 22, Python e nginx; cria o usuário de serviço;
clona os dois repositórios; gera o `finance.env` com um token aleatório;
compila os dois sites; sobe os serviços; configura o nginx e agenda o backup.
Ele é idempotente — rodar de novo não quebra o que já existe.

No fim ele imprime o **token da API**, necessário para as rotas do módulo de
extratos. Guarde.

### Endereços e DNS

A topologia configurada por padrão:

| Endereço | Serve | Acesso |
|---|---|---|
| `celestiaflights.com` (+ `www`) | celest.ia Flights | público |
| `celestiaflights.cloud` (+ `www`) | redireciona 301 para o `.com` | público |
| `financas.celestiaflights.com` | celest.ia Finance | **protegido por senha** |

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

### Proteção do app financeiro

O celest.ia Finance mostra patrimônio, extratos bancários e faturas. Num
domínio público, qualquer um que descobrisse o endereço veria tudo — então ele
fica atrás de **autenticação HTTP no nginx**. O instalador gera usuário e senha
e grava em `/etc/celestia/senha-financas.txt` (0600, só root lê).

Trocar a senha depois:

```bash
NOVA=$(head -c 18 /dev/urandom | base64 | tr -d '=+/' | cut -c1-20)
printf 'rogerio:%s\n' "$(openssl passwd -apr1 "$NOVA")" > /etc/celestia/htpasswd
chown root:www-data /etc/celestia/htpasswd && chmod 640 /etc/celestia/htpasswd
printf 'usuario: rogerio\nsenha:   %s\n' "$NOVA" > /etc/celestia/senha-financas.txt
systemctl reload nginx
echo "nova senha: $NOVA"
```

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

O certbot ajusta os blocos do nginx e renova sozinho.

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
automaticamente — o site não fica quebrado.

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
| `CELESTIA_DATA_DIR` | Onde ficam o banco e os originais. Padrão `/var/lib/celestia/finance` |
| `CELESTIA_DATA_DURAVEL` | `1` declara disco real (num VPS, sempre) |
| `CELESTIA_API_TOKEN` | Token exigido nas rotas de extratos (`Authorization: Bearer`) |
| `CELESTIA_MAX_UPLOAD_MB` | Limite de upload no motor (padrão 15; o nginx limita em 20) |
| `BRAPI_TOKEN` | Opcional — cotações da B3 |
| `ANTHROPIC_API_KEY` | Opcional — parecer analítico via Claude |

Depois de editar: `systemctl restart celestia-finance-motor celestia-finance-site`.

## Diagnóstico

```bash
systemctl status celestia-finance-motor celestia-finance-site nginx
journalctl -u celestia-finance-motor -f     # log do motor
journalctl -u celestia-finance-site -f      # log do site
curl -s 127.0.0.1:8787/health               # motor vivo?
curl -sI 127.0.0.1:3000                     # site vivo?
nginx -t                                    # configuração válida?
du -sh /var/lib/celestia/finance/*          # espaço dos dados
```

| Sintoma | Causa provável |
|---|---|
| 502 no navegador | Um dos serviços caiu — veja o `journalctl` |
| 413 ao subir arquivo | Passou de 20 MB; ajuste `client_max_body_size` e `CELESTIA_MAX_UPLOAD_MB` |
| 401 nas rotas de extrato | Token ausente ou errado no cabeçalho |
| Site antigo depois de atualizar | Build não rodou; confira o fim do `atualizar.sh` |

## Segurança

- Firewall: libere só 22, 80 e 443 (`ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable`).
- SSH por chave, sem senha (`PasswordAuthentication no` em `/etc/ssh/sshd_config`).
- O `finance.env` é 0600 e pertence ao root; os serviços leem sem expor no processo.
- Os dados financeiros nunca saem do servidor: o único acesso à internet do
  motor é a cotação da brapi, que envia apenas tickers.
- Considere restringir o acesso ao domínio de finanças por IP ou Basic Auth no
  nginx enquanto for uso pessoal.
