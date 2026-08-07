"""Contrato e registro dos adaptadores de parsing.

Cada formato/instituição vive em seu próprio módulo dentro de
``engine/extratos/parsers/`` e expõe uma lista ``ADAPTADORES``. O registro
central importa os módulos de uma lista fixa — módulo novo entra aqui uma vez
e nunca há dois agentes editando o mesmo arquivo.

Um adaptador é qualquer objeto com:

- ``nome: str`` — identificador estável (vai para ``arquivos.versao_parser``);
- ``versao: str``;
- ``detecta(contexto) -> float`` — confiança 0..1 de que sabe ler o arquivo;
- ``parse(contexto) -> ResultadoParse``.

``parse`` NUNCA levanta exceção por conteúdo ruim: problemas viram itens em
``ResultadoParse.erros`` (com ``origem_ref`` apontando linha/aba/página) ou,
quando nada é aproveitável, um resultado vazio com ``avisos`` explicando.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from ..detect import FormatoDetectado
from ..modelos import ResultadoParse

#: Módulos que participam do registro (ordem = desempate de confiança igual).
MODULOS_ADAPTADORES = (
    "engine.extratos.parsers.planilha_config",
    "engine.extratos.parsers.ofx",
    "engine.extratos.parsers.pdf_texto",
    "engine.extratos.parsers.xlsx_generico",
    "engine.extratos.parsers.xls_legado",
    "engine.extratos.parsers.xml_generico",
    "engine.extratos.parsers.csv_generico",
)


@dataclass
class ContextoParse:
    """Tudo que um adaptador pode saber sobre o arquivo e o pedido."""

    conteudo: bytes
    formato: FormatoDetectado
    nome_original: str = ""
    tipo_documento: str = "extrato_conta"  # extrato_conta | fatura_cartao | planilha_config
    moeda: str = "BRL"
    rotulo_origem: str = ""  # "conta:Itaú CC" | "cartao:XP Infiniti" — vai no fingerprint
    senha: Optional[str] = None  # senha de PDF — usada e descartada, nunca gravada
    mapeamento: Optional[Dict[str, str]] = None  # coluna origem -> campo destino (2ª passada)
    opcoes: Dict[str, Any] = field(default_factory=dict)


class Adaptador(Protocol):
    nome: str
    versao: str

    def detecta(self, contexto: ContextoParse) -> float: ...

    def parse(self, contexto: ContextoParse) -> ResultadoParse: ...


def todos() -> List[Adaptador]:
    """Carrega os adaptadores registrados (módulos ausentes são pulados)."""
    adaptadores: List[Adaptador] = []
    for caminho in MODULOS_ADAPTADORES:
        try:
            modulo = importlib.import_module(caminho)
        except ImportError:
            continue
        adaptadores.extend(getattr(modulo, "ADAPTADORES", []))
    return adaptadores


def escolher(contexto: ContextoParse) -> Optional[Adaptador]:
    """Adaptador de maior confiança (> 0) para o arquivo; ``None`` se nenhum."""
    melhor: Optional[Adaptador] = None
    melhor_nota = 0.0
    for adaptador in todos():
        try:
            nota = float(adaptador.detecta(contexto))
        except Exception:  # noqa: BLE001 - detecção jamais derruba o pipeline
            nota = 0.0
        if nota > melhor_nota:
            melhor, melhor_nota = adaptador, nota
    return melhor
