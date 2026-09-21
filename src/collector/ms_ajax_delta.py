"""Extração de dados da resposta parcial do ASP.NET AJAX (MS AJAX "delta").

Usado pelas páginas legadas do DocReader (Telerik/UpdatePanel) quando um
postback assíncrono (`__ASYNCPOST=true`) atualiza só um pedaço da página —
em vez de HTML normal, o servidor responde com blocos no formato
`<tamanho>|<tipo>|<id>|<conteudo>|...`, onde <tamanho> deveria ser o
comprimento do <conteudo>. Na prática, o comprimento declarado por esse
servidor específico não bate com o conteúdo real (provavelmente uma
normalização de quebra de linha feita depois de calcular o tamanho), então
em vez de confiar nele, localizamos os campos por âncoras conhecidas:
`__VIEWSTATE` e outros campos de controle são base64/texto simples sem `|`,
então capturamos até o próximo `|` literal; o HTML do grid é isolado pelo
bloco `hiddenField|__EVENTTARGET` que sempre o sucede.
"""

from __future__ import annotations

import re

_FIELD_RE_CACHE: dict[str, re.Pattern] = {}


def _field_pattern(field_id: str) -> re.Pattern:
    pattern = _FIELD_RE_CACHE.get(field_id)
    if pattern is None:
        pattern = re.compile(r"\|hiddenField\|" + re.escape(field_id) + r"\|([^|]*)\|")
        _FIELD_RE_CACHE[field_id] = pattern
    return pattern


def get_hidden_field(text: str, field_id: str) -> str | None:
    """Extrai o valor de um campo oculto (ex: __VIEWSTATE) da resposta."""
    match = _field_pattern(field_id).search(text)
    return match.group(1) if match else None


def get_update_panel(text: str, panel_id: str) -> str | None:
    """Extrai o HTML de um updatePanel da resposta.

    Assume que o bloco termina onde começa `|N|hiddenField|__EVENTTARGET|`
    (verdadeiro para as páginas do DocReader observadas até agora).
    """
    start_marker = f"|updatePanel|{panel_id}|"
    start = text.find(start_marker)
    if start == -1:
        return None
    start += len(start_marker)
    end_match = re.search(r"\|\d+\|hiddenField\|__EVENTTARGET\|", text[start:])
    if not end_match:
        return None
    return text[start : start + end_match.start()]
