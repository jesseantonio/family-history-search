"""Extração de relações explícitas — regras, sem modelo treinado.

Relações suportadas: `filho_de`, `casou_com`, `viuvo_de`, `residente_em`,
`faleceu_em`. Cada uma carrega evidência (o trecho que a originou),
confiança fixa por padrão de gatilho, e `status="extraida"` sempre — cabe
à revisão humana promover pra "confirmada" ou rejeitar (README exige isso
explicitamente: "o sistema não deve afirmar parentescos sem evidências").

Limitação conhecida e deliberada: só captura o padrão "SUJEITO gatilho
OBJETO" (ex: "José, filho de Maria"). O padrão inverso, comum em notícias
antigas — "Fulano, acompanhado de seus filhos A, B e C" — exigiria
resolver a quem "seus" se refere (correferência). Não foi implementado:
o risco de atribuir uma relação errada com confiança indevida pesou mais
que a cobertura extra que isso traria.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.extraction.dates import extract_dates
from src.extraction.entities import _CONNECTORS, _ends_list_item, _looks_like_name_token, _strip_accents

# "filho de criação" (adotivo), "filho de Deus" (expressão religiosa),
# "filho da terra" (natural de um lugar) — não são relação de parentesco
# biológico, mas o padrão "filh[oa] de X" bate neles do mesmo jeito.
_FILHO_DE_IDIOM_EXCLUSIONS = {"criacao", "deus", "terra", "leite"}

# pronomes/conjunções capitalizados (início de frase) que passam pelo
# teste de "parece nome" mas claramente não são um sujeito válido —
# a validação de incompatibilidade temporal da Fase 6 pegou esses casos
# na prática (ex: "Ele, filho de..." virando sujeito="Ele").
_NOT_A_NAME = {"ele", "ela", "este", "esta", "isso", "isto", "que", "mas", "e", "a", "o", "os", "as"}

# palavras que abrem a frase com maiúscula antes do nome ("Nasceu Pedro Effting, filho de")
_LEADING_NOISE = {
    "nasceu", "faleceu", "falleceu", "morreu", "casou", "casou-se", "chegou", "seguiu", "partiu", "regressou",
    "esteve", "consta", "noticia-se", "acha-se", "sepultou-se", "realizou-se", "hoje", "hontem", "ontem",
    "amanha", "anteontem", "faleceu-lhe", "ainda", "tambem", "foi", "era", "com", "para", "pela", "pelo",
}
# relações cujo alvo é uma pessoa (o resto aponta para lugar ou data)
_PERSON_TARGET = {"filho_de", "viuvo_de", "casou_com"}

RELATION_PATTERNS: tuple[tuple[str, re.Pattern, float], ...] = (
    ("filho_de", re.compile(r"\bfilh[oa]\s+de\s+", re.IGNORECASE), 0.7),
    ("viuvo_de", re.compile(r"\bvi[uú]v[oa]\s+de\s+", re.IGNORECASE), 0.75),
    ("casou_com", re.compile(r"\b(?:casou(?:-se)?|consorciou(?:-se)?)\s+com\s+", re.IGNORECASE), 0.7),
    ("residente_em", re.compile(r"\b(?:residente|morador[a]?)\s+em\s+", re.IGNORECASE), 0.6),
    ("faleceu_em", re.compile(r"\bfaleceu\s+em\s+", re.IGNORECASE), 0.7),
)

_TARGET_STOP_RE = re.compile(r"[,;.\n]")
_MAX_TARGET_CHARS = 80
_MAX_SUBJECT_TOKENS = 5


@dataclass(frozen=True)
class RelationMention:
    relation: str
    subject_raw: str | None
    target_raw: str
    target_kind: str | None  # "data" | "lugar_ou_outro" | None (quando não se aplica)
    evidence: str
    confidence: float
    status: str  # sempre "extraida" nesta fase


# abreviações cujo ponto não encerra o nome ("filho de José e de d. Maria")
_ABBREVIATIONS = {"d", "dr", "dra", "sr", "sra", "srta", "sr3", "dna", "exmo", "exma", "revmo", "cel", "cap",
                  "ten", "maj", "gal", "prof", "profa", "pe", "fr", "sto", "sta", "s"}


def _is_abbreviation(token: str) -> bool:
    bare = token.strip("(),;:").rstrip(".")
    return token.rstrip().endswith(".") and _strip_accents(bare).lower() in _ABBREVIATIONS


def _ends_sentence(token: str) -> bool:
    return token.rstrip().endswith(".") and not _is_abbreviation(token)


def _capture_target(text: str, start_pos: int) -> tuple[str, int]:
    window = text[start_pos : start_pos + _MAX_TARGET_CHARS]
    end = start_pos + len(window)
    for stop in _TARGET_STOP_RE.finditer(window):
        if stop.group() == ".":
            last_word = re.search(r"\S*$", window[: stop.start() + 1]).group()
            if _is_abbreviation(last_word):
                continue
        end = start_pos + stop.start()
        break
    return text[start_pos:end].strip(), end


def _trim_person(target: str) -> str:
    """Alvo que deveria ser um nome: corta na primeira palavra que não parece parte de
    nome ("Carlos Souza na igreja de São Bento" -> "Carlos Souza")."""
    kept = []
    for tok in target.split():
        if not _looks_like_name_token(tok):
            break
        kept.append(tok)
    while kept and kept[-1].lower() in _CONNECTORS:
        kept.pop()
    return " ".join(kept)


def _capture_subject(text: str, trigger_start: int) -> str | None:
    tokens = list(re.finditer(r"\S+", text[:trigger_start]))
    if not tokens:
        return None
    end_i = start_i = len(tokens) - 1
    if not _looks_like_name_token(tokens[end_i].group()):
        return None
    steps = 0
    while start_i > 0 and steps < _MAX_SUBJECT_TOKENS:
        candidate = tokens[start_i - 1].group()
        # "...Stella Bassani. Juvenal Pedro, filho de": o ponto fecha a frase anterior;
        # "Antonio Effting e Ana Kraus": o "e" separa duas pessoas
        if (_ends_list_item(candidate) or _ends_sentence(candidate) or candidate.lower() == "e"
                or not _looks_like_name_token(candidate)):
            break
        start_i -= 1
        steps += 1
    # "e Samantha Effting", "Nasceu Pedro Effting": conector ou verbo no começo não é nome
    while start_i < end_i and (tokens[start_i].group().lower() in _CONNECTORS
                               or _strip_accents(tokens[start_i].group()).lower().strip(",;:") in _LEADING_NOISE):
        start_i += 1
    span_start, span_end = tokens[start_i].start(), tokens[end_i].end()
    subject_raw = text[span_start:span_end].strip(" ,;:.")
    normalized = _strip_accents(subject_raw).lower()
    if normalized in _NOT_A_NAME or len(normalized) < 3:
        return None
    return subject_raw


def extract_relations(text: str) -> list[RelationMention]:
    mentions: list[RelationMention] = []
    for relation, pattern, confidence in RELATION_PATTERNS:
        for match in pattern.finditer(text):
            target_raw, target_end = _capture_target(text, match.end())
            if not target_raw:
                continue
            subject_raw = _capture_subject(text, match.start())
            ev_start = max(0, match.start() - 40)
            if ev_start > 0 and not text[ev_start - 1].isspace():   # não começa no meio de uma palavra
                space = text.find(" ", ev_start, match.start())
                ev_start = space + 1 if space != -1 else ev_start
            evidence = text[ev_start:target_end].strip()

            if relation == "filho_de" and _strip_accents(target_raw).lower().split()[0] in _FILHO_DE_IDIOM_EXCLUSIONS:
                continue

            targets = [target_raw]
            if relation == "filho_de":
                # "filho de José e Maria" -> dois pais separados
                # "de José e de d. Maria": o "de" repetido não faz parte do nome
                targets = [p.strip() for p in re.split(r"\s+e\s+(?:d[aoe]\s+)?", target_raw) if p.strip()]
            if relation in _PERSON_TARGET:
                targets = [t for t in map(_trim_person, targets) if len(t) >= 3]

            for target in targets:
                target_kind = None
                if relation == "faleceu_em":
                    target_kind = "data" if extract_dates(target) else "lugar_ou_outro"
                mentions.append(RelationMention(
                    relation=relation, subject_raw=subject_raw, target_raw=target,
                    target_kind=target_kind, evidence=evidence,
                    confidence=confidence, status="extraida",
                ))
    return mentions
