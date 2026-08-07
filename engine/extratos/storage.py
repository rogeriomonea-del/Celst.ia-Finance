"""Guarda imutável dos arquivos originais, endereçada por conteúdo.

O original é a prova de auditoria: uma vez gravado, nunca é sobrescrito nem
alterado. O caminho deriva do SHA-256 (``originais/ab/abcdef....pdf``), então
o mesmo conteúdo só ocupa espaço uma vez e qualquer alteração geraria outro
endereço — imutabilidade por construção.

Nada aqui é público: o diretório mora fora do repositório e fora de qualquer
pasta servida estaticamente; o download passa pela API, que aplica o token.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional, Tuple

from .store import diretorio_dados

_EXTENSAO_SEGURA = re.compile(r"^[a-z0-9]{1,8}$")


def _raiz() -> Path:
    raiz = diretorio_dados() / "originais"
    raiz.mkdir(parents=True, exist_ok=True)
    return raiz


def sha256_de(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def _extensao(nome_original: str) -> str:
    """Extensão saneada do nome original (só para conveniência humana)."""
    sufixo = Path(nome_original or "").suffix.lower().lstrip(".")
    return sufixo if _EXTENSAO_SEGURA.match(sufixo) else "bin"


def caminho_para(sha256: str, nome_original: str = "") -> Path:
    """Caminho canônico do objeto. Valida o hash — nunca monta caminho de fora."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("sha256 inválido")
    pasta = _raiz() / sha256[:2]
    return pasta / f"{sha256}.{_extensao(nome_original)}"


def gravar(conteudo: bytes, nome_original: str) -> Tuple[str, str]:
    """Grava o original de forma atômica; devolve ``(sha256, caminho_relativo)``.

    Escreve num arquivo temporário do mesmo diretório e faz ``os.replace`` —
    ou o objeto inteiro aparece, ou nada aparece. Se o objeto já existe
    (mesmo conteúdo), não regrava: imutável e idempotente.
    """
    digest = sha256_de(conteudo)
    destino = caminho_para(digest, nome_original)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not destino.exists():
        temporario = destino.with_suffix(destino.suffix + ".tmp")
        with open(temporario, "wb") as saida:
            saida.write(conteudo)
            saida.flush()
            os.fsync(saida.fileno())
        os.replace(temporario, destino)
        # Melhor esforço: originais não devem ser graváveis depois de prontos.
        try:
            os.chmod(destino, 0o444)
        except OSError:
            pass
    return digest, str(destino.relative_to(diretorio_dados()))


def ler(caminho_relativo: str) -> Optional[bytes]:
    """Lê um objeto pelo caminho relativo registrado no banco.

    Recusa qualquer caminho que escape da raiz de dados (path traversal).
    """
    base = diretorio_dados().resolve()
    alvo = (base / caminho_relativo).resolve()
    if base not in alvo.parents:
        return None
    if not alvo.is_file():
        return None
    return alvo.read_bytes()
