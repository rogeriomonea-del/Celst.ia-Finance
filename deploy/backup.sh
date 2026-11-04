#!/usr/bin/env bash
# Backup do celest.ia Finance: banco SQLite + arquivos originais importados.
#
# Roda pelo cron todo dia às 3h (ver /etc/cron.d/celestia-backup) e também
# antes de cada atualização. Guarda 14 cópias diárias e some com as antigas.
#
#   sudo /usr/local/bin/celestia-backup
#   DESTINO=/mnt/externo sudo /usr/local/bin/celestia-backup

set -euo pipefail

DADOS="${CELESTIA_DATA_DIR:-/var/lib/celestia/finance}"
DESTINO="${DESTINO:-/var/backups/celestia}"
MANTER="${MANTER:-14}"
CARIMBO="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DESTINO"
chmod 700 "$DESTINO"

if [ ! -f "$DADOS/extratos.db" ]; then
  echo "[$CARIMBO] nada a salvar: $DADOS/extratos.db não existe ainda"
  exit 0
fi

# A API `.backup` do SQLite é a forma correta de copiar um banco em uso: sai
# consistente mesmo com escrita acontecendo, ao contrário de `cp` no arquivo
# (que pegaria o WAL pela metade). Usamos o módulo do Python em vez do CLI
# `sqlite3` porque o Python é requisito do motor e está sempre presente — o
# CLI é um pacote separado que pode faltar.
python3 - "$DADOS/extratos.db" "$DESTINO/extratos-$CARIMBO.db" <<'PY'
import sqlite3, sys

origem, destino = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{origem}?mode=ro", uri=True) as src, sqlite3.connect(destino) as dst:
    src.backup(dst)
PY

# Os originais são imutáveis, então o tar nunca pega arquivo sendo reescrito.
if [ -d "$DADOS/originais" ]; then
  tar -czf "$DESTINO/originais-$CARIMBO.tar.gz" -C "$DADOS" originais
fi

chmod 600 "$DESTINO"/*-"$CARIMBO"* 2>/dev/null || true

# Rotação: mantém as N cópias mais recentes de cada tipo.
for padrao in "extratos-*.db" "originais-*.tar.gz"; do
  # shellcheck disable=SC2012 - nomes são controlados (carimbo de data)
  ls -1t "$DESTINO"/$padrao 2>/dev/null | tail -n "+$((MANTER + 1))" | while read -r antigo; do
    rm -f "$antigo"
  done
done

TAMANHO="$(du -sh "$DESTINO" | cut -f1)"
echo "[$CARIMBO] backup concluído em $DESTINO (total $TAMANHO, mantendo $MANTER cópias)"
