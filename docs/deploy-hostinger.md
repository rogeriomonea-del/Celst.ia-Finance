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
DOMINIO_FINANCE=financas.seudominio.com.br \
DOMINIO_FLIGHTS=voos.seudominio.com.br \
bash /tmp/celestia/deploy/instalar.sh
```

O script instala Node 22, Python, nginx e sqlite; cria o usuário de serviço;
clona os dois repositórios; gera o `finance.env` com um token aleatório;
compila os dois sites; sobe os serviços; configura o nginx e agenda o backup.
Ele é idempotente — rodar de novo não quebra o que já existe.

No fim ele imprime o **token da API**, que você vai precisar para as rotas do
módulo de extratos. Guarde.

### DNS e HTTPS

No painel da Hostinger, aponte os registros `A` dos dois subdomínios para o IP
do VPS. Depois, no servidor:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d financas.seudominio.com.br -d voos.seudominio.com.br
```

O certbot ajusta os blocos do nginx e renova sozinho.

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
