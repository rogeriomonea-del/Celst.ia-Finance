"""Move os dados importados ANTES do login por conta para a conta dona.

Até a autenticação multiusuário, o módulo de extratos gravava tudo na raiz de
dados (``CELESTIA_DATA_DIR/extratos.db`` + ``originais/``). Agora cada conta
logada só enxerga ``usuarios/<id>/``, e a raiz fica para acesso de máquina.
Este utilitário move (não copia, não apaga) o banco e os originais da raiz
para o espaço de uma conta — normalmente a primeira, do administrador.

.. code-block:: console

    $ CELESTIA_DATA_DIR=/var/lib/celestia/finance \\
        python3 -m engine.auth.migrar_dados --para rogerio@exemplo.com
    $ python3 -m engine.auth.migrar_dados --para 1 --simular   # só mostra

Regras: a conta precisa existir (entre no app uma vez antes); o espaço da
conta precisa estar vazio (sem ``extratos.db``) — nunca se sobrescreve nada.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from ..extratos.store import diretorio_dados
from . import contas
from .store import conexao, espaco_do_usuario, normaliza_email


def _resolver_usuario(identificador: str) -> Optional[contas.Usuario]:
    with conexao() as db:
        if identificador.isdigit():
            return contas.usuario_por_id(db, int(identificador), incluir_inativos=True)
        linha = db.execute(
            "SELECT * FROM usuarios WHERE email = ?", (normaliza_email(identificador),)
        ).fetchone()
        return contas._linha_para_usuario(linha) if linha else None


def planejar(raiz: Path, destino: Path) -> List[tuple]:
    """Pares ``(origem, destino)`` do que seria movido."""
    movimentos = []
    for nome in ("extratos.db", "extratos.db-wal", "extratos.db-shm"):
        if (raiz / nome).exists():
            movimentos.append((raiz / nome, destino / nome))
    if (raiz / "originais").is_dir():
        movimentos.append((raiz / "originais", destino / "originais"))
    return movimentos


def migrar(identificador: str, *, simular: bool = False, saida=sys.stdout) -> int:
    raiz = diretorio_dados()
    usuario = _resolver_usuario(identificador)
    if usuario is None:
        print(f"Conta '{identificador}' não encontrada. Entre no app uma vez antes.", file=saida)
        return 2
    movimentos = planejar(raiz, espaco_do_usuario(usuario.id))
    if not movimentos:
        print(f"Nada para migrar: não há extratos.db nem originais/ em {raiz}.", file=saida)
        return 0
    destino = espaco_do_usuario(usuario.id)
    if (destino / "extratos.db").exists() or (destino / "originais").exists():
        print(
            f"O espaço da conta {usuario.email} ({destino}) já tem dados — nada foi movido."
            " Resolva manualmente para não sobrescrever.",
            file=saida,
        )
        return 3
    for origem, alvo in movimentos:
        print(f"{'(simulação) ' if simular else ''}{origem} → {alvo}", file=saida)
        if not simular:
            shutil.move(str(origem), str(alvo))
    if not simular:
        try:
            os.chmod(destino, 0o700)
        except OSError:
            pass
        print(f"Pronto: dados movidos para a conta {usuario.email} (id {usuario.id}).", file=saida)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m engine.auth.migrar_dados",
        description="Move extratos.db e originais/ da raiz de dados para a conta indicada.",
    )
    parser.add_argument("--para", required=True, help="e-mail ou id da conta dona dos dados")
    parser.add_argument("--simular", action="store_true", help="só mostra o que faria")
    argumentos = parser.parse_args(list(argv) if argv is not None else None)
    return migrar(argumentos.para, simular=argumentos.simular)


if __name__ == "__main__":  # pragma: no cover - ponto de entrada
    raise SystemExit(main())
