"""Edições listadas pelo memoria.bn e o CSV de listagem (`listagem_<s>.csv`).

Fica separado de `browser_bn` (que dirige o Chrome) e de `pdf_collect` (que
baixa e lê os PDFs) para que as duas pontas possam ser usadas e testadas
sem abrir navegador.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from src.collector.bn_client import PDF_HOST

LISTAGEM_FIELDS = ["termo", "bib", "jornal", "ano", "edicao", "ocorrencias_edicao_BN", "pasta",
                   "link_pdf", "link_docreader", "status", "paginas_pdf"]

# status finais: a edição já foi processada e não precisa ser refeita ao retomar
STATUS_CONCLUIDOS = {"ok", "nao_achado_no_texto_do_pdf", "pdf_ausente_no_servidor"}


@dataclass
class EditionHit:
    bib: str
    jornal: str
    ano: int
    edicao: str            # mantém sufixos, ex. "00138a"
    ocorrencias_bn: int    # contagem da coluna OCORRÊNCIAS do "Listar ocorrências"
    status: str = "pendente"
    pasta: str = ""        # texto exato da coluna PASTA (ex. "ano 1923\\edicao 00212")
    paginas_pdf: list[int] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.bib, self.ano, self.edicao)

    @property
    def pdf_name(self) -> str:
        return f"per{self.bib}_{self.ano}_{self.edicao}.pdf"

    @property
    def pdf_url(self) -> str:
        return f"{PDF_HOST}/{self.bib}/{self.pdf_name}"

    def docreader_url(self, surname: str) -> str:
        pasta = (self.pasta or f"ano {self.ano}\\edicao {self.edicao}").replace(" ", "%20")
        return (f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={self.bib}"
                f"&pesq={quote(surname)}&pasta={pasta}")


def filtrar_periodo(editions: list[EditionHit], ano_inicio: int | None,
                    ano_fim: int | None) -> list[EditionHit]:
    return [e for e in editions
            if (ano_inicio is None or e.ano >= ano_inicio) and (ano_fim is None or e.ano <= ano_fim)]


def save_listagem(editions: list[EditionHit], surname: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LISTAGEM_FIELDS)
        for e in editions:
            w.writerow([surname, e.bib, e.jornal, e.ano, e.edicao, e.ocorrencias_bn, e.pasta, e.pdf_url,
                        e.docreader_url(surname), e.status, ",".join(map(str, e.paginas_pdf))])


def load_listagem(path: Path) -> list[EditionHit]:
    """Lê o CSV de `save_listagem` preservando pasta, status e páginas. Aceita CSVs
    antigos, sem a coluna `pasta`."""
    editions = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            paginas = [int(p) for p in (r.get("paginas_pdf") or "").split(",") if p.strip().isdigit()]
            editions.append(EditionHit(
                bib=r["bib"], jornal=r["jornal"], ano=int(r["ano"]), edicao=r["edicao"],
                ocorrencias_bn=int(r.get("ocorrencias_edicao_BN") or 1),
                status=r.get("status") or "pendente", pasta=r.get("pasta") or "",
                paginas_pdf=paginas,
            ))
    return editions
