"""Pessoas de uma busca: todos os nomes com o sobrenome, com as grafias do OCR agrupadas.

Exemplo real (Westrupp): "Gregorio Westrupp", "Gregório Westrupp", "Gresorio Westrupp",
"GregO' rio Westrupp" e "Jregorio Westrupp" são a mesma pessoa; "Sérgio Westrupp" e
"Sérgio Luiz Westrupp" também. O agrupamento é feito em três passos:

1. **Menções** (`find_surname_people`): cada vez que o sobrenome aparece no trecho
   (também com 1 letra trocada pelo OCR, em sobrenomes de 6+ letras), os prenomes à
   esquerda viram o nome — parando em pontuação, "e", tratamentos ("Sr.", "Pe.", "Dom")
   e cargos ("Professor", "Titular"). Pedaços partidos pelo OCR ("GregO' rio") são
   juntados de volta. Sufixos ("Filho", "Júnior", "Neto") entram no nome: distinguem
   pessoas.
2. **Grafias**: prenomes comparados sem acento, com a ortografia antiga normalizada
   ("Luiz" = "Luis", "Theodoro" = "Teodoro") e, em nomes de 6+ letras, 1 letra de
   diferença por erro de OCR ("Gresorio" = "Gregorio") — exceto quando a diferença é
   só o gênero ("Antonio" ≠ "Antonia").
3. **Nome curto e nome completo**: "Sérgio Westrupp" junta com "Sérgio Luiz Westrupp"
   quando ele é o único nome completo compatível (mesmo primeiro nome). Se houver mais
   de um ("Antônio Getúlio" e "Antônio Carlos"), o nome curto fica separado.

Nada disso prova identidade: são hipóteses. Ajustes manuais (`splits` e `merges`)
sobrepõem o automático.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

# tratamentos e cargos que antecedem um nome (encerram a busca do prenome à esquerda)
TITLES = {
    "sr", "sra", "sr3", "srta", "snr", "snra", "senhor", "senhora", "d", "dna", "dom", "dona", "dr", "dra",
    "doutor", "doutora", "pe", "padre", "frei", "irma", "irmao", "madre", "revmo", "rev", "mons", "monsenhor",
    "bispo", "cardeal", "maestro", "prof", "profa", "professor", "professora", "exmo", "exma", "cel", "coronel",
    "cap", "capitao", "ten", "tenente", "maj", "major", "sgt", "sargento", "gal", "general", "vereador",
    "vereadora", "deputado", "deputada", "senador", "senadora", "prefeito", "prefeita", "governador",
    "presidente", "diretor", "diretora", "secretario", "secretaria", "juiz", "juiza", "promotor", "delegado",
    "titular", "oficial", "escrevente", "superintendente", "adjunto", "administrativo", "comerciante",
    "commerciante", "agricultor", "lavrador", "engenheiro", "advogado", "vigario", "vigário", "conego",
    "sargento", "soldado", "cabo", "veterinario", "medico", "farmaceutico", "contador", "tabeliao",
    "familia", "casal", "viuva", "viuvo", "menino", "menina", "jovem", "sta", "sto", "sao", "santo", "santa",
    "maior", "menor", "chefe", "membro", "socio", "socia", "aluno", "aluna", "noivo", "noiva", "filho", "filha",
    "pai", "mae", "avo", "tio", "tia", "esposo", "esposa", "dia", "missa", "sepultamento", "sr.", "fone",
}
# palavras que indicam instituição ou lugar com nome de pessoa ("Lar Zoê Lacerda Westrupp")
INSTITUTIONS = {
    "lar", "escola", "colegio", "grupo", "escolar", "rua", "avenida", "av", "praca", "travessa", "rodovia",
    "edificio", "fundacao", "instituto", "hospital", "ginasio", "sala", "loteamento", "conjunto", "residencial",
    "creche", "centro", "biblioteca", "auditorio", "estadio", "clube", "associacao", "sociedade", "empresa",
    "comercial", "industria", "ltda", "cia", "firma", "loja", "casa", "eeb", "ee", "nucleo", "premio", "bairro",
}
_GENERIC_TITLES = {"sr", "sra", "sr3", "srta", "snr", "snra", "senhor", "senhora", "d", "dna", "dona", "maior",
                   "menor", "familia", "casal", "jovem", "menino", "menina", "dia", "missa", "sepultamento", "fone",
                   "exmo", "exma", "sta", "sto", "sao", "santo", "santa"}
SUFFIXES = {"filho": "Filho", "junior": "Júnior", "jr": "Júnior", "neto": "Neto", "sobrinho": "Sobrinho",
            "segundo": "Segundo"}
CONNECTORS = {"de", "da", "do", "das", "dos", "d"}
# palavras que abrem frase com maiúscula e não são nome ("Também Carlos Barletta...")
STARTERS = {
    "tambem", "hoje", "ontem", "hontem", "amanha", "ainda", "ja", "mas", "porem", "assim", "entao", "entre",
    "para", "com", "sem", "pelo", "pela", "pelos", "pelas", "os", "as", "o", "a", "no", "na", "nos", "nas",
    "em", "ao", "aos", "por", "sobre", "segundo", "conforme", "apos", "quando", "como", "onde", "que", "se",
    "este", "esta", "esse", "essa", "aquele", "aquela", "seu", "sua", "seus", "suas", "nosso", "nossa",
    "um", "uma", "uns", "umas", "foi", "era", "sera", "ha", "nao", "sim", "muito", "todos", "todas",
    "recebemos", "visitou", "visitaram", "esteve", "estiveram", "chegou", "chegaram", "partiu", "seguiu",
    "faleceu", "nasceu", "casou", "parabens", "felicitamos", "agradecem", "agradece", "convidam", "convida",
    # títulos de seção do jornal que antecedem listas de nomes ("Requerimentos Caetano Barletta")
    "requerimentos", "requerimento", "despachos", "despacho", "editais", "edital", "avisos", "aviso", "notas",
    "nota", "convite", "convites", "agradecimento", "agradecimentos", "falecimento", "falecimentos",
    "nascimentos", "casamentos", "aniversarios", "aniversario", "anniversarios", "anniversario", "enlace",
    "noivado", "obituario", "necrologia", "nomeacoes", "nomeacao", "exoneracoes", "licencas", "promocoes",
    "portaria", "decreto", "decretos", "atos", "actos", "expediente", "classe", "sociais", "social", "viajantes",
    "hospedes", "chegados", "partidos", "passageiros", "lista", "relacao", "resultado", "aprovados", "eleitos",
}
MAX_GIVEN = 4
MIN_LEN_OCR_TOLERANT = 6

_TOKEN_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['’`´])*", re.UNICODE)
_BREAK_RE = re.compile(r"[,;:.!?()\[\]{}\"“”«»|/\\—–]|\s-\s|\n\s*\n")
_FRAGMENT_JOINERS = ("'", "’", "`", "´", "-")


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", fold(text))


def ortho(token: str) -> str:
    """Ortografia antiga e variações comuns: ph->f, th->t, y->i, letras dobradas,
    z final -> s ("Luiz" = "Luis")."""
    t = letters(token)
    t = t.replace("ph", "f").replace("th", "t").replace("y", "i").replace("w", "v").replace("k", "c")
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"z$", "s", t)


def _gender_pair(a: str, b: str) -> bool:
    """Antonio/Antonia, Mario/Maria, Rafael/Rafaela: nomes diferentes, não erro de OCR."""
    if len(a) == len(b) and a[:-1] == b[:-1] and {a[-1], b[-1]} == {"a", "o"}:
        return True
    return a + "a" == b or b + "a" == a


def same_token(a: str, b: str) -> bool:
    """Dois prenomes (já passados por `ortho`) podem ser a mesma grafia?"""
    if a == b:
        return True
    if min(len(a), len(b)) < MIN_LEN_OCR_TOLERANT or _gender_pair(a, b):
        return False
    limit = 2 if min(len(a), len(b)) >= 10 else 1
    return Levenshtein.distance(a, b, score_cutoff=limit) <= limit


def _is_surname_token(token: str, needle: str, extra_tolerant: bool = False) -> bool:
    t = letters(token)
    if t == needle:
        return True
    # grafia normalizada igual ("Barleta" = "Barletta", "Krauss" = "Kraus"), mesmo em sobrenomes curtos
    if extra_tolerant and t and ortho(t) == ortho(needle):
        return True
    return (len(needle) >= MIN_LEN_OCR_TOLERANT and t[:1] == needle[:1] and abs(len(t) - len(needle)) <= 1
            and Levenshtein.distance(t, needle, score_cutoff=1) <= 1)


# --------------------------------------------------------------------------- menções
@dataclass(frozen=True)
class Mention:
    raw: str                    # como está escrito no trecho, ex. "GregO' rio Westrupp"
    given: tuple[str, ...]      # prenomes normalizados (ortho), ex. ("gregorio",)
    given_display: tuple[str, ...]   # prenomes como escritos, sem pontuação de OCR
    suffix: str | None          # "Filho", "Júnior"...
    title: str | None           # tratamento à esquerda, se houver ("Pe.", "Dom")
    start: int
    end: int
    given_letters: tuple[str, ...] = ()   # prenomes só sem acento/minúsculos (busca literal)
    surname_raw: str = ""                  # o sobrenome como está escrito

    @property
    def key(self) -> str:
        return " ".join(self.given) + (f"|{self.suffix.lower()}" if self.suffix else "")


def _clean_display(token: str) -> str:
    """"GregO'rio" -> "Gregorio", "CELSO" -> "Celso"."""
    t = re.sub(r"['’`´]", "", token)
    return t[:1].upper() + t[1:].lower()


def find_surname_people(text: str, surname: str, *, extra_tolerant: bool = False) -> list[Mention]:
    """Todas as menções a pessoas com o sobrenome no texto. Menções só com o sobrenome
    ("Westrupp afirmou", "família Westrupp") voltam com `given` vazio."""
    needle = letters(surname)
    if not needle:
        return []
    tokens = list(_TOKEN_RE.finditer(text))
    out = []
    for i, tok in enumerate(tokens):
        if not _is_surname_token(tok.group(), needle, extra_tolerant):
            continue
        # prenomes à esquerda
        given_rev, display_rev, title = [], [], None
        j, right_edge = i - 1, tok.start()
        while j >= 0 and len(given_rev) < MAX_GIVEN:
            cand = tokens[j]
            between = text[cand.end():right_edge]
            word = cand.group()
            low = letters(word)
            if low in TITLES and between.strip() in ("", "."):   # "Pe. Gregório", "Sr Apolonio"
                title = word + ("." if between.strip() == "." else "")
                break
            if _BREAK_RE.search(between) or len(between) > 3 or not low:
                break
            if low in INSTITUTIONS:
                given_rev, display_rev = [], []     # "Lar Zoê Lacerda ...": nome de instituição
                break
            if low == "e" or low in STARTERS:
                break
            # pedaço partido pelo OCR: "GregO' rio" -> "Gregorio"
            if word[:1].islower() and j > 0 and len(low) <= 4 and low not in CONNECTORS:
                prev = tokens[j - 1]
                gap = text[prev.end():cand.start()]
                if prev.group().endswith(_FRAGMENT_JOINERS) or (gap.strip() in _FRAGMENT_JOINERS and gap.strip()):
                    if prev.group()[:1].isupper():
                        joined = prev.group() + word
                        given_rev.append(ortho(joined))
                        display_rev.append(_clean_display(joined))
                        right_edge = prev.start()
                        j -= 2
                        continue
                break
            if low in CONNECTORS:
                # "Maria da Silva Westrupp": conector só entre dois nomes
                nxt = tokens[j - 1] if j > 0 else None
                if nxt is None or not nxt.group()[:1].isupper() or _BREAK_RE.search(text[nxt.end():cand.start()]):
                    break
                right_edge = cand.start()
                j -= 1
                continue
            if not word[:1].isupper():
                break
            given_rev.append(ortho(word))
            display_rev.append(_clean_display(word))
            right_edge = cand.start()
            j -= 1
        # sufixo à direita: "Westrupp Filho"
        suffix = None
        if i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if not _BREAK_RE.search(text[tok.end():nxt.start()]) and letters(nxt.group()) in SUFFIXES:
                suffix = SUFFIXES[letters(nxt.group())]
        start = tokens[j + 1].start() if given_rev else tok.start()
        end = tokens[i + 1].end() if suffix else tok.end()
        out.append(Mention(raw=text[start:end], given=tuple(reversed(given_rev)),
                           given_display=tuple(reversed(display_rev)), suffix=suffix,
                           title=title, start=start, end=end,
                           given_letters=tuple(letters(d) for d in reversed(display_rev)),
                           surname_raw=tok.group()))
    return out


# --------------------------------------------------------------------------- busca de uma pessoa
def _smart_token(a: str, b: str) -> bool:
    """Busca inteligente de um prenome (já em `ortho`): mais tolerante que `same_token`,
    porque aqui a pessoa digitou o nome e queremos achar as grafias do jornal ("Carlos" acha
    "Carlo"). Gênero continua separando ("Carlos" não acha "Carla")."""
    if a == b:
        return True
    if _gender_pair(a, b):
        return False
    shortest = min(len(a), len(b))
    if shortest >= 8:
        return Levenshtein.distance(a, b, score_cutoff=2) <= 2
    return shortest >= 4 and Levenshtein.distance(a, b, score_cutoff=1) <= 1


def _query_tokens(given: str) -> list[str]:
    return [t for t in (letters(w) for w in given.split()) if t and t not in CONNECTORS]


def find_person(text: str, given: str, surname: str, *, smart: bool = True) -> list[Mention]:
    """Menções a uma pessoa específica ("Carlos" + "Barletta") no texto.

    - literal (`smart=False`): o nome aparece como digitado, ignorando só acentos,
      maiúsculas e conectores ("Carlos Barletta" acha "CARLOS BARLETTA" e
      "João Carlos Barletta", mas não "Carlo Barleta" nem "Carlos Alberto Barletta");
    - inteligente: aceita erros de OCR e variações de grafia no prenome e no sobrenome
      ("Carlo Barleta") e nomes do meio ("Carlos Alberto Barletta")."""
    q = _query_tokens(given)
    if not q:
        return []
    from src.preprocessing.normalize import normalize_text   # junta "Bar-\nletta" e espaços

    flat = normalize_text(text)
    sur = letters(surname)
    out = []
    for m in find_surname_people(flat, surname, extra_tolerant=smart):
        if not m.given:
            continue
        if smart:
            qo, rest = [ortho(t) for t in q], iter(m.given)
            if all(any(_smart_token(t, u) for u in rest) for t in qo):
                out.append(m)
        elif letters(m.surname_raw) == sur and m.given_letters[-len(q):] == tuple(q):
            out.append(m)
    return out


_DOUBLABLE = set("bcdfglmnprst")


def surname_variants(surname: str, limit: int = 3) -> list[str]:
    """Grafias prováveis do sobrenome para pesquisar no site da BN, que só acha o termo
    exato: "Barletta" -> ["Barletta", "Barleta"]; "Barleta" -> ["Barleta", "Barletta"];
    "Theodoro" -> ["Theodoro", "Teodoro"]. O original vem primeiro."""
    base = surname.strip()
    low = fold(base)
    cands = [base, re.sub(r"([^\W\d_])\1", r"\1", base)]      # letras dobradas: "Barletta" -> "Barleta"
    for a, b in (("th", "t"), ("ph", "f"), ("y", "i"), ("w", "v"), ("k", "c")):   # ortografia antiga
        if a in low:
            cands.append(re.sub(a, b, base, flags=re.IGNORECASE))
    # consoante simples entre vogais no fim ("Barleta") -> dobrada ("Barletta")
    m = re.search(r"[aeiou]([a-z])[aeiou]$", low)
    if cands[1] == base and m and m.group(1) in _DOUBLABLE:
        i = len(base) - 2
        cands.append(base[:i] + base[i] + base[i:])
    out, seen = [], set()
    for c in cands:
        c = c[:1].upper() + c[1:] if base[:1].isupper() else c
        key = fold(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:limit]


# --------------------------------------------------------------------------- agrupamento
@dataclass
class Form:
    """Uma grafia normalizada (ex. "gregorio"), com as escritas originais e onde aparece."""
    key: str
    given: tuple[str, ...]
    suffix: str | None
    raws: Counter = field(default_factory=Counter)
    displays: Counter = field(default_factory=Counter)
    occurrences: list = field(default_factory=list)
    titles: Counter = field(default_factory=Counter)

    @property
    def count(self) -> int:
        return sum(self.raws.values())


def _forms_equal(a: Form, b: Form) -> bool:
    if a.suffix != b.suffix or len(a.given) != len(b.given):
        return False
    if all(same_token(x, y) for x, y in zip(a.given, b.given)):
        return True
    # nomes compostos: o nome inteiro com 1 letra de diferença ("Sérgio Euiz" = "Sérgio Luiz"),
    # desde que o primeiro nome bata
    ja, jb = "".join(a.given), "".join(b.given)
    return (len(a.given) > 1 and min(len(ja), len(jb)) >= 9 and same_token(a.given[0], b.given[0])
            and Levenshtein.distance(ja, jb, score_cutoff=1) <= 1)


def _sub_name(short: Form, long: Form) -> bool:
    """"Sérgio" cabe em "Sérgio Luiz": mesmo primeiro nome e os demais em ordem."""
    if short.suffix != long.suffix or len(short.given) >= len(long.given):
        return False
    if not same_token(short.given[0], long.given[0]):
        return False
    rest = iter(long.given[1:])
    return all(any(same_token(t, u) for u in rest) for t in short.given[1:])


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def group_people(occurrences: list[dict], surname: str, *, splits=(), merges=()) -> dict:
    """Agrupa as menções do sobrenome em pessoas.

    `occurrences`: dicts com id, context_excerpt, year, periodical, event_type.
    `splits`: chaves de grafia que não devem ser juntadas automaticamente a nada.
    `merges`: pares de chaves de grafia que devem ficar na mesma pessoa.
    Devolve {"people": [...], "surname_only": n, "by_occurrence": {occ_id: [person_id]}}."""
    forms: dict[str, Form] = {}
    surname_only = 0
    for occ in occurrences:
        for m in find_surname_people(occ.get("context_excerpt") or "", surname):
            if not m.given:
                surname_only += 1
                continue
            f = forms.get(m.key)
            if f is None:
                f = forms[m.key] = Form(m.key, m.given, m.suffix)
            f.raws[m.raw] += 1
            f.displays[m.given_display] += 1
            f.occurrences.append(occ)
            if m.title:
                f.titles[_clean_display(m.title.rstrip("."))] += 1

    splits = set(splits)
    uf = _UF()
    order = sorted(forms.values(), key=lambda f: (-f.count, f.key))
    # passo 2: grafias equivalentes, cada uma juntando-se ao grupo mais frequente compatível
    # (compara com o representante do grupo, não com todos: evita encadear A~B~C)
    reps: list[Form] = []
    for f in order:
        uf.find(f.key)
        if f.key in splits:
            continue
        rep = next((r for r in reps if r.key not in splits and _forms_equal(r, f)), None)
        if rep is not None:
            uf.union(rep.key, f.key)
        else:
            reps.append(f)
    # passo 3: nome curto -> único nome completo compatível
    groups: dict[str, list[Form]] = defaultdict(list)
    for f in forms.values():
        groups[uf.find(f.key)].append(f)
    rep_of = {root: max(fs, key=lambda f: (len(f.given), f.count)) for root, fs in groups.items()}
    for root in sorted(groups, key=lambda r: len(rep_of[r].given)):
        short = min(groups[root], key=lambda f: len(f.given))
        if short.key in splits:
            continue
        cands = {uf.find(r) for r, rep in rep_of.items()
                 if uf.find(r) != uf.find(root) and rep.key not in splits and _sub_name(short, rep)}
        if not cands:
            continue
        # vários nomes completos compatíveis: só junta se um deles concentra as menções
        weight = {c: sum(f.count for f in forms.values() if uf.find(f.key) == c) for c in cands}
        top = max(weight, key=weight.get)
        if weight[top] >= 0.8 * sum(weight.values()):
            uf.union(top, root)
    for a, b in merges:
        if a in forms and b in forms:
            uf.union(a, b)

    groups = defaultdict(list)
    for f in forms.values():
        groups[uf.find(f.key)].append(f)
    sur_display = surname.strip()[:1].upper() + surname.strip()[1:]
    people, by_occ = [], defaultdict(list)
    for fs in groups.values():
        # nome exibido: o mais completo, ponderado por quantas vezes aparece ("Sérgio Luiz"
        # vence "Sérgio"; "Maria Helena" vence um "Maria Maria Helena" duplicado pelo OCR)
        best = max(fs, key=lambda f: (f.count * len(f.given), len(f.given), f.key))
        given = best.displays.most_common(1)[0][0]
        name = " ".join(given + (sur_display,) + ((best.suffix,) if best.suffix else ()))
        pid = "p-" + re.sub(r"[^a-z0-9]+", "-", best.key).strip("-")
        occs = {}
        for f in fs:
            for o in f.occurrences:
                occs[o["id"]] = o
        years = [o.get("year") for o in occs.values() if o.get("year")]
        titles = Counter()
        for f in fs:
            titles.update(f.titles)
        people.append({
            "id": pid, "name": name,
            "mentions": sum(f.count for f in fs),
            "occurrences": sorted(occs),
            "years": [min(years), max(years)] if years else None,
            "periodicals": len({o.get("periodical") for o in occs.values()}),
            "events": dict(Counter(o.get("event_type") or "outro" for o in occs.values())),
            # tratamentos que dizem algo sobre a pessoa (Pe., Dom, Maestro...), sem os genéricos
            "titles": [t for t, _ in titles.most_common() if letters(t) not in _GENERIC_TITLES][:3],
            "variants": [{"key": f.key, "count": f.count,
                          "raws": [r for r, _ in f.raws.most_common(6)]}
                         for f in sorted(fs, key=lambda f: -f.count)],
        })
        for oid in occs:
            by_occ[oid].append(pid)
    people.sort(key=lambda p: (-p["mentions"], p["name"]))
    return {"people": people, "surname_only": surname_only, "by_occurrence": dict(by_occ)}


def person_of_name(name: str, surname: str, people: dict) -> str | None:
    """Pessoa (id) de um nome solto, como o rótulo de um nó da árvore."""
    ms = [m for m in find_surname_people(name, surname) if m.given]
    if not ms:
        return None
    key = ms[0].key
    for p in people["people"]:
        if any(v["key"] == key for v in p["variants"]):
            return p["id"]
    return None
