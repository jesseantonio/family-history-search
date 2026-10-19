"""Carrega e tokeniza o corpus do trabalho: os `context_excerpt` das
ocorrências já coletadas pelo projeto (Unidade 1) na busca pelo sobrenome
"Effting" em jornais catarinenses.

Cada documento é o trecho de texto ao redor da menção ao sobrenome numa
página de jornal digitalizado — texto curto, com ruído de OCR típico de
jornais antigos (acentos perdidos viram "�", erros de reconhecimento de
caractere).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "data" / "processed" / "occurrences_sc_effting_extracted.jsonl"

_TOKEN_RE = re.compile(r"\b\w+\b", re.UNICODE)


@dataclass(frozen=True)
class Documento:
    id: str
    texto: str
    periodico: str
    ano: int
    event_type: str


def limpar_texto(texto: str) -> str:
    """Remove o caractere de substituição do OCR e normaliza espaços."""
    texto = texto.replace("�", " ")
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def tokenizar(texto: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(texto)]


def carregar_corpus(path: Path = DEFAULT_DATASET) -> list[Documento]:
    docs: list[Documento] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            texto = limpar_texto(rec.get("context_excerpt", ""))
            if not texto:
                continue
            docs.append(
                Documento(
                    id=rec["id"],
                    texto=texto,
                    periodico=rec.get("periodical", ""),
                    ano=rec.get("year", 0),
                    event_type=rec.get("event_type", "outro"),
                )
            )
    return docs


if __name__ == "__main__":
    docs = carregar_corpus()
    print(f"{len(docs)} documentos carregados")
    tipos = {}
    for d in docs:
        tipos[d.event_type] = tipos.get(d.event_type, 0) + 1
    print("distribuição por event_type:", tipos)
