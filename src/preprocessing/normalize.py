"""Normalização de texto para PLN — nunca sobrescreve o `ocr_raw` original.

Aplica só as normalizações que o README autoriza: espaços, hifenização de
fim de linha, e nada além disso. Correção de caracteres "obviamente
confundidos" (ex: 1/l, 0/O) foi deixada de fora — no nosso corpus real, o
problema dominante é perda de caractere acentuado (sai como "�", não uma
confusão com sinal claro de correção), e tentar "adivinhar" isso criaria
erros novos sem uma fonte de verdade pra validar contra.
"""

from __future__ import annotations

import re

_HYPHEN_LINEBREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def normalize_text(text: str) -> str:
    """Produz uma versão de `text` para leitura/PLN: remove caracteres de
    controle (artefato de extração, nunca conteúdo real), junta palavras
    hifenizadas quebradas em fim de linha, e colapsa espaços/quebras de
    linha em um único espaço. `text` original não é alterado.
    """
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    joined = _HYPHEN_LINEBREAK_RE.sub(r"\1\2", cleaned)
    return _WHITESPACE_RE.sub(" ", joined).strip()
