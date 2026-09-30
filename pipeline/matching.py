"""Systembolaget -> Vivino matching, following the rules in docs/plan.md.

1. Normalise both sides (case, diacritics, apostrophes, hyphens, corporate noise).
2. Query with fallbacks: producer + name, name only, producer + grape.
3. Score producer and name separately; style words never carry a match alone;
   country and colour must not contradict.
4. Identity before similarity: numbers (Bin 28, 10 years), sparkling style, sweetness and tier
   words must agree, and so must Systembolaget's measured sugar with a sweetness word in Vivino's name
   (a 56 g/l wine isn't the Brut). A conflict rejects the candidate; a detail on one side only, or a
   country or type we can't check, means it can't be auto-accepted.
5. Three bands: accept / review / reject. Thresholds here are provisional until
   the hand-checked sample calibrates them.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rapidfuzz import fuzz

# Changes whenever this file changes, so stored matches made by other rules are re-matched.
MATCHER_VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

# Provisional thresholds, to be calibrated against the hand-checked sample.
ACCEPT_PRODUCER = 80
ACCEPT_NAME = 80
REVIEW_TOTAL = 60
AMBIGUOUS_MARGIN = 3
RIVAL_SHARE = 0.05  # a runner-up with fewer ratings than this share of the best's doesn't make a match ambiguous

# Corporate noise: dropped from producer comparison and from names on both sides.
NOISE = {
    "domaine", "domaines", "dom", "chateau", "bodegas", "bodega", "weingut", "weinguter", "wines", "wine",
    "winery", "family", "pty", "ltd", "limited", "sa", "srl", "spa", "sas", "sarl", "scea", "gmbh", "ag", "ab",
    "inc", "llc", "co", "cie", "bv", "sl", "slu", "lda", "vineyards", "vineyard", "estate", "estates", "cellars",
    "azienda", "agricola", "vitivinicola", "societa", "company", "group", "vins", "vinos", "vini", "vinhos",
    # Generic "winery" words in other languages; the distinctive part of the name remains.
    "cantina", "cantine", "tenuta", "tenute", "fattoria", "podere", "poderi", "quinta", "herdade", "adega", "finca",
    "casa", "cave", "caves", "cellar", "celler", "kellerei", "weinkellerei", "winzer", "winzergenossenschaft",
    "cooperative", "cooperativa", "vignerons", "vignobles", "vignoble", "maison", "vina", "vinedos", "weinbau",
}
# Words that change the wine; present on one side only costs name points (plan: keep Reserva, Riserva, ...).
QUALITY = {
    "reserva", "riserva", "reserve", "reservado", "gran", "grande", "grand", "crianza", "classico", "especial",
    "special", "selection", "selezione", "seleccion", "vieilles", "old", "single", "limited", "prestige", "cru",
    "premier", "premiere", "1er", "icon", "barrel", "barrique",
}
SWEETNESS = {"demi", "doux", "dolce", "dulce", "sweet", "semi", "amabile", "moelleux", "extra", "nature", "zero",
             "sec", "seco", "secco", "trocken", "halbtrocken", "feinherb"}
# Words that describe colour/style/appellation class; they never carry a match alone.
STYLE = {
    "blanc", "blanco", "bianco", "branco", "weiss", "weisser", "white", "rouge", "rosso", "tinto", "red", "rot",
    "rose", "rosado", "rosato", "brut", "nature", "extra", "sec", "demi", "dry", "semi", "seco", "dolce", "doux",
    "prosecco", "cava", "champagne", "cremant", "spumante", "frizzante", "sparkling", "sekt", "vin", "vino",
    "vinho", "doc", "docg", "aoc", "aop", "igt", "igp", "do", "doca", "ava", "organic", "eko", "ekologisk",
    "bio", "biologico", "non", "vintage", "nv", "cuvee", "millesime", "superiore",
}
ROSE_MARKERS = {"rose", "rosado", "rosato", "rosa"}
# Organic markers in wine names. Vivino often leaves them out, but some producers have a regular and an organic
# wine as separate entries (Lurton's Les Fumées Blanches), so an organic entry for a non-organic wine is suspect.
ORGANIC = {"organic", "eko", "ekologisk", "bio", "biologico", "organico", "biologique", "biologisch", "ecologico",
           "ecologica", "okologisch"}
# Tier and cuvée-class words: any difference between the two names needs a human.
TIER = QUALITY | {"superiore", "millesime", "millesimato", "vintage", "nv"}
# Semi-sparkling vs fully sparkling. "Spumante" alone only restates the type, so it may be missing on one side.
SPARKLING_STYLE = {"frizzante", "spumante", "petillant", "perlant", "perlwein"}
SEMI_SPARKLING = SPARKLING_STYLE - {"spumante"}
# Sweetness levels, checked in order on the folded name; the first hit wins.
SWEET_LEVELS = (
    ("nature", r"brut nature|pas dose|dosage zero|zero dosage|brut zero|nature"),
    ("extra brut", r"extra brut"),
    ("extra dry", r"extra dry|extra sec|extra seco"),
    ("brut", r"brut"),
    ("medium", r"demi sec|semi seco|semi secco|semi dry|medium dry|off dry|halbtrocken|feinherb|abboccato|amabile"),
    ("sweet", r"dolce|dulce|doux|sweet|moelleux|lieblich"),
    ("dry", r"sec|seco|secco|dry|trocken"),
)
DEFAULT_SWEETNESS = {"brut"}  # sparkling names often leave it out; still-wine dryness (Vouvray Sec) matters
# Sugar (g/l) that fits a sweetness word in Vivino's name: the EU bands for sparkling wine, widened for measuring
# and rounding. "Dry" covers both sparkling sec (17-32) and still trocken, so only a sweet wine contradicts it.
RANGE_LEVEL = {"reserva", "reserve", "riserva", "reservado"}  # "(Reserva)" in a Vivino name: see score()
ABV_GAP = 4.5  # alcohol points apart that make two wines different (a 5 % Moscato d'Asti is not its 42 % grappa)
POP_WEIGHT, POP_CAP = 1.5, 7.5  # ranking only: a much more rated entry is more likely the one a shop sells
SUGAR_FITS = {"nature": (0, 8), "extra brut": (0, 9), "brut": (0, 18), "extra dry": (8, 25), "dry": (0, 40),
              "medium": (8, 70), "sweet": (25, 1000)}
STOP = {"de", "del", "della", "delle", "di", "da", "do", "dos", "das", "du", "des", "la", "le", "les", "el", "los",
        "las", "il", "lo", "the", "of", "and", "et", "y", "e", "und", "d", "l", "st", "ste", "san", "santa", "sankt",
        "dal", "dei", "degli", "al", "alla", "am", "an", "im", "a", "en", "in", "vom", "von",
        "och", "med", "av", "fran", "for", "till",  # Swedish filler in thin names ("carmenère och syrah")
        "by"}  # "Mooi by Org de Rac"
# Wine-type words Vivino adds to names ("Bristol Cream Sherry", "Late Bottled Vintage Port").
GENERIC = {"port", "porto", "sherry", "jerez", "madeira", "marsala", "vermouth", "sake", "wein", "vinho"}
# Grape synonyms, so "Shiraz" on one side and "Syrah" in Systembolaget's grape list agree.
GRAPE_SYNONYMS = [{"shiraz", "syrah"}, {"garnacha", "grenache", "cannonau"}, {"monastrell", "mourvedre", "mataro"},
                  {"zinfandel", "primitivo"}, {"grigio", "gris"}, {"tempranillo", "roriz", "fino", "tinto"},
                  {"carmenere", "carmenère"}, {"sauvignon", "fume"}]

COUNTRIES = {
    "Argentina": "ar", "Armenien": "am", "Australien": "au", "Azerbajdzjan": "az", "Belgien": "be",
    "Bosnien och Hercegovina": "ba", "Brasilien": "br", "Bulgarien": "bg", "Chile": "cl", "Cypern": "cy",
    "Danmark": "dk", "Finland": "fi", "Folkrepubliken Kina": "cn", "Frankrike": "fr", "Georgien": "ge",
    "Grekland": "gr", "Israel": "il", "Italien": "it", "Japan": "jp", "Kanada": "ca", "Kosovo": "xk",
    "Kroatien": "hr", "Libanon": "lb", "Litauen": "lt", "Luxemburg": "lu", "Marocko": "ma", "Mexiko": "mx",
    "Moldavien": "md", "Montenegro": "me", "Nederländerna": "nl", "Nordmakedonien": "mk", "Norge": "no",
    "Nya Zeeland": "nz", "Peru": "pe", "Polen": "pl", "Portugal": "pt", "Rumänien": "ro", "Schweiz": "ch",
    "Serbien": "rs", "Slovakien": "sk", "Slovenien": "si", "Spanien": "es", "Storbritannien": "gb",
    "England": "gb", "Sverige": "se", "Sydafrika": "za", "Tjeckien": "cz", "Turkiet": "tr", "Tyskland": "de",
    "USA": "us", "Ukraina": "ua", "Ungern": "hu", "Uruguay": "uy", "Österrike": "at",
}
# Systembolaget categoryLevel2 -> allowed Vivino type_id (1 red, 2 white, 3 sparkling, 4 rosé, 7 dessert, 24 fortified).
TYPES = {
    "Rött vin": {1, 7}, "Vitt vin": {2, 7}, "Mousserande vin": {3, 7}, "Rosévin": {4}, "Starkvin": {7, 24},
}


def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'").replace("´", "'")
    s = s.replace("œ", "oe").replace("æ", "ae").replace("ø", "o").replace("ß", "ss").replace("&", " and ")
    s = re.sub(r"(?<=\b[a-z])'(?=[a-z])", " ", s)  # d'arenberg -> d arenberg
    s = s.replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()  # hyphens and punctuation -> space


# Noise when comparing producers, but a real designation inside a wine's name ("Estate Pinot Noir" is not
# the same wine as the estate's single-vineyard "La Côte Pinot Noir").
NAME_KEEP = frozenset({"estate", "estates"})


def tokens(s: str | None, keep: frozenset = frozenset()) -> list[str]:
    return [t for t in fold(s).split() if t not in NOISE or t in keep]


def significant(toks: list[str]) -> list[str]:
    return [t for t in toks if t not in STYLE and t not in STOP and not re.fullmatch(r"\d{1,2}|(19|20)\d\d", t)]


def _numbers(toks: list[str]) -> set[str]:
    """Line numbers and age statements (Bin 28, No 7, 10 years); vintages are 4 digits and excluded."""
    return {t.lstrip("0") or "0" for t in toks if re.fullmatch(r"\d{1,3}", t)}


def _sweetness(toks: list[str]) -> str | None:
    text = f" {' '.join(toks)} "
    return next((level for level, rx in SWEET_LEVELS if re.search(rf" (?:{rx}) ", text)), None)


def identity(a: list[str], b: list[str]) -> tuple[list[str], list[str]]:
    """What makes each name a specific product: (conflicts, details present on one side only)."""
    conflicts: list[str] = []
    one_sided: list[str] = []
    na, nb = _numbers(a), _numbers(b)
    if na and nb and na != nb:
        conflicts.append(f"number {'/'.join(sorted(na))}≠{'/'.join(sorted(nb))}")
    elif na != nb:
        one_sided.append(f"number {'/'.join(sorted(na | nb))}")
    sa, sb = SPARKLING_STYLE & set(a), SPARKLING_STYLE & set(b)
    if sa and sb and sa != sb:
        conflicts.append(f"style {'/'.join(sorted(sa))}≠{'/'.join(sorted(sb))}")
    elif SEMI_SPARKLING & (sa ^ sb):
        one_sided.append(f"style {'/'.join(sorted(SEMI_SPARKLING & (sa ^ sb)))}")
    wa, wb = _sweetness(a), _sweetness(b)
    if wa and wb and wa != wb:
        conflicts.append(f"sweetness {wa}≠{wb}")
    elif wa != wb and (wa or wb) not in DEFAULT_SWEETNESS:
        one_sided.append(f"sweetness {wa or wb}")
    tiers = TIER & (set(a) ^ set(b))
    if tiers:
        one_sided.append(f"tier {','.join(sorted(tiers))}")
    return conflicts, one_sided


def _tok_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) <= 3 or len(b) <= 3:
        return False
    # Spelling variants keep their first letter, but for a silent h (Hermitage, Ermitage); a different one is a
    # different name (Tacchino, Facchino).
    return a.removeprefix("h")[:1] == b.removeprefix("h")[:1] and fuzz.ratio(a, b) >= 85


def coverage(a: list[str], b: list[str]) -> float:
    """Share of a's characters (by token length) whose token has a fuzzy match in b."""
    if not a:
        return 0.0
    total = sum(len(t) for t in a)
    hit = sum(len(t) for t in a if any(_tok_eq(t, u) for u in b))
    return hit / total


def contains_all(needle: list[str], hay: list[str]) -> bool:
    return bool(needle) and all(any(_tok_eq(t, u) for u in hay) for t in needle)


@dataclass
class Wine:
    """The Systembolaget fields matching needs."""

    article: str  # productNumber
    product_id: str
    name_bold: str
    name_thin: str
    producer: str
    vintage: str
    country: str
    category: str  # categoryLevel2
    grapes: list[str] = field(default_factory=list)
    origin: str = ""  # originLevel1 and originLevel2, e.g. "Piemonte Langhe"
    organic: bool | None = None  # Systembolaget's isOrganic; None when unknown
    sugar: float | None = None  # g/l as Systembolaget measured it; None when unknown or listed as 0
    abv: float | None = None  # alcohol %, None when unknown

    @property
    def name(self) -> str:
        return f"{self.name_bold} {self.name_thin}".strip()

    @classmethod
    def from_sb(cls, p: dict) -> "Wine":
        return cls(
            article=str(p.get("productNumber") or ""),
            product_id=str(p.get("productId") or ""),
            name_bold=p.get("productNameBold") or "",
            name_thin=p.get("productNameThin") or "",
            producer=p.get("producerName") or "",
            vintage=str(p.get("vintage") or ""),
            country=p.get("country") or "",
            category=p.get("categoryLevel2") or "",
            grapes=[g for g in (p.get("grapes") or []) if isinstance(g, str)],
            origin=" ".join(x for x in (p.get("originLevel1"), p.get("originLevel2")) if x),
            organic=p.get("isOrganic"),
            sugar=round(p["sugarContentGramPer100ml"] * 10, 1) if p.get("sugarContentGramPer100ml") else None,
            abv=p.get("alcoholPercentage") or None,
        )


@dataclass
class Candidate:
    vivino_id: int
    name: str
    winery: str
    country: str
    type_id: int | None
    ratings_average: float
    ratings_count: int
    producer_score: float
    name_score: float
    total: float
    contradictions: list[str]
    notes: list[str]
    query: str
    unconfirmed: list[str] = field(default_factory=list)  # why it can't be auto-accepted
    evidence: dict = field(default_factory=dict)  # for the hand check: region, alcohol, vintages


def score(w: Wine, hit: dict, query: str) -> Candidate:
    winery = (hit.get("winery") or {}).get("name") or ""
    v_name = hit.get("name") or ""
    v_country = (hit.get("region") or {}).get("country") or ((hit.get("winery") or {}).get("region") or {}).get("country") or ""
    type_id = hit.get("type_id")
    stats = hit.get("statistics") or {}
    notes: list[str] = []

    sb_prod = tokens(w.producer)
    sb_name = tokens(w.name, NAME_KEEP)
    v_win = tokens(winery)
    # Vivino writes a range's reserve level in parentheses ("Cabernet Sauvignon (Reserva)"), which shops often leave
    # out: it counts only when Systembolaget's name has it too. Anything else in parentheses names a cuvée and stays.
    optional = [t for t in re.findall(r"\(([^)]*)\)", v_name) if set(tokens(t)) <= RANGE_LEVEL]
    v_nm = tokens(re.sub("|".join(r"\(" + re.escape(t) + r"\)" for t in optional) or "$^", " ", v_name), NAME_KEEP)
    v_nm += [t for t in tokens(" ".join(optional)) if t in sb_name]

    # Producer: the producer field vs Vivino winery, or the winery named inside the SB name
    # (Systembolaget's producer is sometimes the importer or a parent company).
    sp, vw = significant(sb_prod) or sb_prod, significant(v_win) or v_win
    # A place in a producer name ("El Coto de Rioja" for the winery "El Coto") says nothing about who made the wine.
    place = set(tokens(w.origin))
    if [t for t in sp if t not in place] and [t for t in vw if t not in place]:
        sp, vw = [t for t in sp if t not in place], [t for t in vw if t not in place]
    direct = 0.5 * coverage(sp, vw) + 0.5 * coverage(vw, sp) if sp and vw else 0.0
    in_name = 0.0
    if contains_all(vw, sb_name) and sum(len(t) for t in vw) >= 5:
        in_name = 1.0
        notes.append("winery in SB name")
    elif sp and contains_all(sp, v_nm) and sum(len(t) for t in sp) >= 5:
        in_name = 0.95
        notes.append("SB producer in Vivino name")
    producer_score = 100 * max(direct, in_name)

    # Name: significant tokens, with producer/winery words removed from both sides.
    def strip(toks: list[str]) -> list[str]:
        drop = set(sb_prod) | set(v_win) | set(fold(w.producer).split()) | set(fold(winery).split())
        return [t for t in toks if not any(_tok_eq(t, d) for d in drop)]

    a = significant(strip(sb_name))
    b = significant(strip(v_nm))
    if a and b:
        name_score = 100 * (0.6 * coverage(a, b) + 0.4 * coverage(b, a))
    elif not a and not b:
        # Nothing but style words on either side, e.g. "Brut" vs "Brut": weak, never accept.
        same = [t for t in strip(sb_name) if t in STYLE] == [t for t in strip(v_nm) if t in STYLE]
        name_score = 70.0 if same else 40.0
        notes.append("name is style words only")
    elif not a:
        name_score = 35.0
        notes.append("SB name is style words only")
    else:
        # Vivino wine name adds nothing beyond the winery ("Penfolds" / "Penfolds"): judge on SB name alone.
        name_score = 45.0
        notes.append("Vivino name is winery/style only")

    # Rosé markers change the wine even inside the same Vivino type (sparkling).
    if bool(ROSE_MARKERS & set(sb_name)) != bool(ROSE_MARKERS & set(v_nm)):
        name_score = max(0.0, name_score - 25)
        notes.append("rosé marker mismatch")
    # Quality and sweetness words on one side only (Reserva vs none, Brut vs Extra Brut), after removing
    # producer and winery words ("Grand Sud" is a brand, not a tier). Lowers the rank of the wrong cuvée.
    sa, sv = strip(sb_name), strip(v_nm)
    odd = (QUALITY | SWEETNESS) & (set(sa) ^ set(sv))
    if odd:
        name_score = max(0.0, name_score - min(30, 15 * len(odd)))
        notes.append("one-sided: " + ",".join(sorted(odd)))

    contradictions, unconfirmed = identity(sa, sv)
    v_abv = _float(hit.get("alcohol"))
    if w.abv and v_abv and abs(w.abv - v_abv) > ABV_GAP:
        contradictions.append(f"alcohol {w.abv:g}≠{v_abv:g}")
    level = _sweetness(sv)
    if w.sugar is not None and level in SUGAR_FITS and not SUGAR_FITS[level][0] <= w.sugar <= SUGAR_FITS[level][1]:
        contradictions.append(f"sugar {w.sugar:g} g/l≠{level}")
    # Organic: Systembolaget's flag (or its name when the flag is unknown) against Vivino's name, after removing
    # winery words ("Ecologica" is a brand). An organic entry for a non-organic wine can't auto-accept and ranks
    # below the regular one. An organic wine may match a plain entry (Vivino often omits the word); between
    # otherwise equal candidates, rank() prefers the one whose organic status agrees.
    sb_organic = w.organic if w.organic is not None else bool(ORGANIC & set(sa))
    v_organic = bool(ORGANIC & set(sv))
    if v_organic and not sb_organic:
        name_score = max(0.0, name_score - 10)
        unconfirmed.append("organic on Vivino only")
    # Distinctive words on one side only ("Leyenda", "Creamy Oak", "Magic Mountain") can name a different wine
    # from the same producer. Grapes, regions, style and wine-type words may appear on one side only.
    explained = set(GENERIC)
    for g in w.grapes:
        explained |= set(tokens(g))
    for group in GRAPE_SYNONYMS:
        if group & explained:
            explained |= group
    region = hit.get("region") or {}
    for place in (w.origin, region.get("name"), ((hit.get("winery") or {}).get("region") or {}).get("name")):
        explained |= set(tokens(place))
    extra = [t for t in a if not any(_tok_eq(t, u) for u in b)] + [t for t in b if not any(_tok_eq(t, u) for u in a)]
    extra = [t for t in extra if t not in explained and t not in TIER and t not in SWEETNESS and t not in ORGANIC
             and not any(_tok_eq(t, x) for x in explained) and not re.fullmatch(r"\d+", t)]
    if a and b and extra:
        unconfirmed.append("name words " + ",".join(sorted(set(extra))))
    sb_cc = COUNTRIES.get(w.country)
    if sb_cc and v_country and sb_cc != v_country:
        contradictions.append(f"country {sb_cc}≠{v_country}")
    elif not (sb_cc and v_country):
        unconfirmed.append("country unknown")  # missing evidence is not agreement
    allowed = TYPES.get(w.category)
    # Semi-sparkling and low-alcohol wines (Moscato d'Asti, 5 %) are still wine to Systembolaget, sparkling to Vivino.
    if allowed and w.category in ("Vitt vin", "Rosévin", "Rött vin") and \
            ((w.abv is not None and w.abv <= 8.5) or SEMI_SPARKLING & set(sb_name)):
        allowed = allowed | {3}
    if allowed and type_id and type_id not in allowed:
        contradictions.append(f"type {w.category}≠{type_id}")
    elif not (allowed and type_id):
        unconfirmed.append("type not checked")

    total = 0.4 * producer_score + 0.6 * name_score
    # Whether a Vivino word only matched by spelling (Cocobon ~ Cocoon), not word for word.
    sb_words, v_words = set(sb_prod) | set(sb_name), set(v_win) | set(v_nm)
    fuzzy = any(t not in sb_words and any(_tok_eq(t, u) for u in sb_words) for t in v_words)
    return Candidate(
        vivino_id=int(hit.get("id") or hit.get("objectID") or 0),
        name=v_name,
        winery=winery,
        country=v_country,
        type_id=type_id,
        ratings_average=float(stats.get("ratings_average") or 0.0),
        ratings_count=int(stats.get("ratings_count") or 0),
        producer_score=round(producer_score, 1),
        name_score=round(name_score, 1),
        total=round(total, 1),
        contradictions=contradictions,
        notes=notes,
        query=query,
        unconfirmed=unconfirmed,
        evidence={"organic_agrees": sb_organic == v_organic, "fuzzy": fuzzy,
                  "region": region.get("name") or "", "alcohol": hit.get("alcohol") or "",
                  "non_vintage": hit.get("non_vintage"),
                  "years": " ".join(str(v.get("year")) for v in (hit.get("vintages") or [])[:12])},
    )


def band(c: Candidate | None, runner_up: Candidate | None = None) -> str:
    if c is None or c.contradictions:
        return "reject"
    weak = any("style words only" in n or "winery/style only" in n for n in c.notes)
    if c.producer_score >= ACCEPT_PRODUCER and c.name_score >= ACCEPT_NAME and not weak and not c.unconfirmed:
        # Two near-identical candidates: a human decides. A runner-up with a sliver of the ratings (an empty duplicate
        # entry, a misspelt copy) is no rival: a shop sells the wine people rate. Unless it fits at least as well and
        # word for word where the pick only fits by spelling ("Cocobon" for Systembolaget's "Cocoon", whose own entry
        # is rare).
        exact_rival = runner_up and runner_up.total >= c.total and c.evidence.get("fuzzy") and \
            not runner_up.evidence.get("fuzzy")
        rival = runner_up and not runner_up.contradictions and (
            runner_up.ratings_count >= RIVAL_SHARE * c.ratings_count or exact_rival)
        if rival and c.total - runner_up.total < AMBIGUOUS_MARGIN:
            return "review"
        return "accept"
    if c.total >= REVIEW_TOTAL:
        return "review"
    return "reject"


# Dropped from query text: Algolia requires every word by default, so noise costs recall.
QUERY_DROP = NOISE | {"organic", "eko", "ekologisk", "bio", "och", "and", "figli", "fils", "spirits", "the"}


def _query_text(s: str) -> str:
    return " ".join(t for t in fold(s).split() if t not in QUERY_DROP)


def queries(w: Wine) -> list[str]:
    """Producer + name (producer only if not already in the name), name only, producer + grape, and a short one: the
    producer's longest word + the name without grape and tier words (or + the grape). Algolia requires every word, so
    the short one finds entries whose name leaves out the grape or a tier ("Les Fumées Blanches"), or whose winery
    is named more briefly than Systembolaget's producer ("Ruppertsberger" for "Ruppertsberger Weinkeller Hoheburg")."""
    name = _query_text(w.name)
    producer = _query_text(w.producer)
    distinctive = [t for t in producer.split() if len(t) >= 4 and t not in STOP]
    in_name = any(t in name.split() for t in distinctive)
    q1 = name if in_name or not producer else f"{producer} {name}"
    q3 = f"{producer} {_query_text(w.grapes[0])}" if w.grapes else producer
    grape_words = {t for g in w.grapes for t in fold(g).split()}
    lead = max(distinctive, key=len) if distinctive else ""
    core = [t for t in name.split() if t not in grape_words and t not in TIER and t not in STOP and len(t) > 1
            and t != lead]
    rest = core or ([t for t in _query_text(w.grapes[0]).split()] if w.grapes else [])
    q4 = " ".join(([lead] if lead else []) + rest) if rest else ""
    out: list[str] = []
    for q in (q1, name, q3, q4):
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out


def _float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 80 else None


def popularity(c: Candidate) -> float:
    return min(POP_CAP, POP_WEIGHT * math.log10(1 + c.ratings_count))


def rank(cands: list[Candidate]) -> list[Candidate]:
    # Non-contradicting candidates first, then by total plus a little for being much rated (the plain wine a shop sells
    # over a rarer cuvée of the same name) when the organic status agrees, then organic status agreeing; one entry per
    # Vivino id. band() still compares raw totals, so a popular runner-up never makes a match look safer.
    seen: dict[int, Candidate] = {}
    for c in cands:
        if c.vivino_id not in seen or c.total > seen[c.vivino_id].total:
            seen[c.vivino_id] = c
    def key(c: Candidate) -> tuple:
        agrees = c.evidence.get("organic_agrees", True)  # never boost the regular entry of an organic wine
        return not c.contradictions, c.total + (popularity(c) if agrees else 0.0), agrees

    return sorted(seen.values(), key=key, reverse=True)


def match(w: Wine, search: Callable[[str], list[dict]], max_queries: int = 4,
          stop_on_accept: bool = False) -> dict:
    """Pool the candidates of every query fallback, then decide. A close competitor found by a later
    query must be in the runner-up comparison, so stopping at the first accept is opt-in."""
    pool: list[Candidate] = []
    used: list[str] = []
    for q in queries(w)[:max_queries]:
        used.append(q)
        pool += [score(w, h, q) for h in search(q)]
        ranked = rank(pool)
        if stop_on_accept and ranked and band(ranked[0], ranked[1] if len(ranked) > 1 else None) == "accept":
            break
    ranked = rank(pool)
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    return {"wine": w, "best": best, "second": second, "band": band(best, second), "queries": used}
