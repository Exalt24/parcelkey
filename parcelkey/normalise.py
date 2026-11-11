"""Normalisation of Australian style addresses, phones and emails.

Every function here is pure: no database, no clock. Each returns what it found and the ids of the rules that fired, so a surprising result can be
explained from one stored row. The rules are listed in docs/normalisation.md.

An address becomes an `Addr`: unit, level, building, lot, street number (a first and an optional last), street name, street type, locality, state and postcode.
Street types are written in the G-NAF way (the full word, `STREET`, not `ST`). The same functions run on G-NAF rows and on messy source text, so both
sides of a match are in one vocabulary.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

from .gnaf_codes import FLAT_TYPES, LEVEL_TYPES, STREET_SUFFIXES, STREET_TYPES

STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT")
STATE_NAMES = {
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD", "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA",
    "TASMANIA": "TAS", "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT", "OTHER TERRITORIES": "OT",
}

# The common street type spellings (left) to the G-NAF style code (right). They find where a street ends in text with no comma; every type G-NAF lists is added below.
STREET_TYPE_SPELLINGS = {
    "ST": "STREET", "STR": "STREET", "STREET": "STREET",
    "RD": "ROAD", "ROAD": "ROAD",
    "AVE": "AVENUE", "AV": "AVENUE", "AVENUE": "AVENUE",
    "DR": "DRIVE", "DRV": "DRIVE", "DRIVE": "DRIVE",
    "CT": "COURT", "COURT": "COURT",
    "PL": "PLACE", "PLACE": "PLACE",
    "CRES": "CRESCENT", "CR": "CRESCENT", "CRESCENT": "CRESCENT",
    "LN": "LANE", "LANE": "LANE",
    "TCE": "TERRACE", "TER": "TERRACE", "TERRACE": "TERRACE",
    "PDE": "PARADE", "PARADE": "PARADE",
    "CL": "CLOSE", "CLOSE": "CLOSE",
    "BLVD": "BOULEVARD", "BVD": "BOULEVARD", "BOULEVARD": "BOULEVARD",
    "HWY": "HIGHWAY", "HIGHWAY": "HIGHWAY",
    "CCT": "CIRCUIT", "CIRCUIT": "CIRCUIT",
    "ESP": "ESPLANADE", "ESPLANADE": "ESPLANADE",
    "GR": "GROVE", "GRV": "GROVE", "GROVE": "GROVE",
    "WAY": "WAY",
    "SQ": "SQUARE", "SQUARE": "SQUARE",
}
# Every spelling G-NAF lists for a street type (the code, which is the full word, and its short name), then the common spellings above.
_ALL_STREET_TYPES = {**{code: code for code in STREET_TYPES}, **{short: code for code, short in STREET_TYPES.items()}, **STREET_TYPE_SPELLINGS}
# A street suffix after the street type: the NAME in full, and the CODE only for the compass points (a code such as IN or ON would read ordinary words as suffixes).
_COMPASS_CODES = ("N", "S", "E", "W", "NE", "NW", "SE", "SW")
STREET_SUFFIX_WORDS = {**{name: name for name in STREET_SUFFIXES.values()}, **{code: STREET_SUFFIXES[code] for code in _COMPASS_CODES}}
_SUFFIX_NAMES = frozenset(STREET_SUFFIXES.values())

UNIT_MARKERS = frozenset({"UNIT", "U", "APT", "APARTMENT", "FLAT", "SUITE", "STE", "SHOP", "VILLA", "OFFICE", "TOWNHOUSE", "TH", "ROOM", "#"})
LEVEL_MARKERS = frozenset({"LEVEL", "LVL", "L", "FLOOR", "FLR"})
# FLAT_TYPE_AUT codes for a space that is not a home or a shop: carspace 3 is not unit 3, so these keep their type in the unit. Every other flat type reads as an ordinary unit.
DISTINCT_FLAT_TYPES = frozenset({"ANT", "ATM", "BBQ", "BTSD", "CAGE", "CARP", "CARS", "COOL", "GRGE", "MBTH", "SHED", "SIGN", "STOR", "SUBS", "VLT"})
# The words a feed writes for the commonest of those, and for the level types that are not a plain level (LEVEL_TYPE_AUT): basement 1 is not level 1.
OTHER_FLAT_WORDS = {"CARSPACE": "CARS", "CARPARK": "CARP", "GARAGE": "GRGE", "STORE": "STOR"}
OTHER_LEVEL_WORDS = {"BASEMENT": "B", "MEZZANINE": "M", "PODIUM": "PDM"}
# G-NAF flat types a feed writes as a word and a number (Building 2, Tower 3, Block 3, Penthouse 1, Duplex 2). They are read as an ordinary unit, like Shop 3, but they are not in UNIT_MARKERS: a suburb
# can start with one (Tower Hill, Cottage Point) and UNIT_MARKERS decides what cannot be a suburb. They count only with a number or a letter after them.
NUMBERED_UNIT_WORDS = frozenset({"BUILDING", "BLDG", "TOWER", "TWR", "BLOCK", "BLCK", "PENTHOUSE", "DUPLEX", "MAISONETTE", "COTTAGE", "STUDIO", "LOFT", "BUNGALOW", "TENANCY"})
_PLAIN_UNIT_WORDS = UNIT_MARKERS | frozenset(OTHER_FLAT_WORDS)
_UNIT_WORDS = _PLAIN_UNIT_WORDS | NUMBERED_UNIT_WORDS
_COUNTRY_WORDS = frozenset({"AUSTRALIA", "AUS", "AU"})
_FLAT_WORDS = {code: word for word, code in OTHER_FLAT_WORDS.items()}
_LEVEL_WORDS = {code: word for word, code in OTHER_LEVEL_WORDS.items()}

_ID = re.compile(r"^\d+[A-Z]?$")
_LETTER = re.compile(r"^[A-Z]$")                                  # a flat that is only a letter (Unit B): G-NAF holds it in FLAT_NUMBER_PREFIX or FLAT_NUMBER_SUFFIX with no number
_UID = re.compile(r"^[A-Z]{0,2}\d+[A-Z]?$")                      # a unit id may carry a letter prefix: G1, G01 (FLAT_NUMBER_PREFIX)
_PARTS = re.compile(r"^([A-Z]{0,2})(\d+)([A-Z]?)$")
_NUMBER = re.compile(r"^(\d+[A-Z]?)(?:-(\d+[A-Z]?))?$")
_SLASH = re.compile(r"^([A-Z]{0,2}\d+[A-Z]?|[A-Z])/(\d+[A-Z]?)(?:-(\d+[A-Z]?))?$")
_LEVEL_PREFIX = re.compile(r"^L(\d+[A-Z]?)$")                    # L3/12 is level 3 of number 12
_GROUND_FLOOR = re.compile(r"\bGROUND (?:FLOOR|LEVEL|FLR|LVL)\b")
_ORDINAL_FLOOR = re.compile(r"\b(\d+)(?:ST|ND|RD|TH) (?:FLOOR|LEVEL|FLR|LVL)\b")
_POSTAL = re.compile(r"(?:^|[\s,])(?:(?:G\s*)?P\s*O\s*BOX|LOCKED\s+BAG|PRIVATE\s+BAG)\b")
_GLUED = re.compile("^(" + "|".join(re.escape(m) for m in sorted(UNIT_MARKERS, key=len, reverse=True)) + r")(\d+[A-Z]?)(?:/(.+))?$")          # no-mutate: alternation backtracks, so the order of the markers only changes speed
# a state written with dots (N.S.W., A.C.T., Q.L.D., W.A.): the dots come out before the state is read, so `St` after them is not a street and the suburb is not eaten by the state
_DOTTED_STATE = re.compile(r"(?<![A-Z0-9])(?:" + "|".join(r"\.\s?".join(code) for code in STATES if len(code) > 1) + r")\.?(?![A-Z])")
_DISALLOWED = re.compile(r"[^A-Z0-9/,\- #]")
_POSTCODE = re.compile(r"^\d{4}$")
_SPACED_SLASH = re.compile(r"(?<=[A-Z0-9])\s+/\s*(?=\d)|(?<=[A-Z0-9])\s*/\s+(?=\d)")          # `4 / 18`, `4 /18` and `4/ 18` are the unit slash
_DASH_LETTER = re.compile(r"^(\d+)-([A-Z])$")                    # 4-A is number 4A
_NOT_A_LOCALITY_START = UNIT_MARKERS | LEVEL_MARKERS | {"LOT"}
# the addresses a form is filled with when the person gave none: they are not evidence that two records are one person
PLACEHOLDER_EMAILS = frozenset({"noemail@noemail.com", "noemail@email.com", "noemail@gmail.com", "nomail@nomail.com", "no@email.com", "none@none.com", "no@no.com", "na@na.com", "n@n.com",
                                "a@a.com", "test@test.com", "email@email.com", "unknown@unknown.com"})
# every character RFC 5322 allows in an unquoted local part (the apostrophe of O'Brien among them); the domain stays letters, digits and hyphens
_EMAIL = re.compile(r"^[a-z0-9._%+\-!#$&'*/=?^`{|}~]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$")
_EMAIL_NAMED = re.compile(r"^([^<>]*)<([^<>]*)>$")                   # Kate Smith <kate@example.com>
_EMAIL_TAIL_NOTE = re.compile(r"\s*\([^()]*\)\s*$")                  # kate@example.com (Kate)
_BRACKET_NOTE = re.compile(r"\([^()]*\)|\([^(),]*(?=,|$)")          # 12 Smith St (rear): the note and its brackets; one never closed ends at the next comma or the end of the line
_UNIT_WORD_ALT = "|".join(sorted((w for w in _UNIT_WORDS if w != "#"), key=len, reverse=True))          # no-mutate: alternation backtracks, so the order of the words only changes speed
# a unit number written with a dot or a dash between its level and its flat (Unit 3.02, Apt 12-05, 3.02/12 Smith St): G-NAF holds a flat as one integer (FLAT_NUMBER, up to five digits), so the parts are joined (302, 1205)
_UNIT_DOTTED = re.compile(rf"((?<![A-Za-z0-9])(?:{_UNIT_WORD_ALT})\s*|#\s*)(\d{{1,4}})(?:\.(\d{{1,4}})|-(0\d{{1,3}}))(?![\d.\-])", re.IGNORECASE)
_DOTTED_SLASH = re.compile(r"(?<![A-Za-z0-9.])(\d{1,4})\.(\d{1,4})(?=/\d)")
_UNIT_NO_WORD = re.compile(rf"(?<![A-Z0-9])({_UNIT_WORD_ALT})\s+(?:NO|NUMBER|NUM|NR)\s+(?=[A-Z]{{0,2}}\d)")          # Unit No. 5, Apartment Number 5: the word is a label, the number is the unit
# a street number range written with spaces (12 - 14), another dash (an en or em dash, the minus sign), an ampersand or the word to is the range 12-14. Not after a unit, level or lot word, where it is a unit or a level
_RANGE = re.compile(r"(?<![A-Za-z0-9.#])(\d+[A-Za-z]?)(?:\s*[-\u2010-\u2015\u2212&]\s*|\s+TO\s+)(\d+[A-Za-z]?)(?![A-Za-z0-9/])(?!\.\d)", re.IGNORECASE)
_RANGE_BLOCKERS = (_UNIT_WORDS | LEVEL_MARKERS | frozenset(OTHER_LEVEL_WORDS) | {"LOT", "NO", "NUMBER", "NUM", "NR", "SPACE", "#"})
_PLAN = re.compile(r"(?<![A-Z0-9])(LOT\s+\d+[A-Z]?)(?:[\s,]+ON(?![A-Z0-9]))?(?:[\s,/]*(?:DP|SP|LP|RP|CP|PS)\s*\d+[A-Z]?(?![A-Z0-9]))?")          # Lot 5 DP 12345, Lot 12 on Smith Rd: the lot, and the plan or the word on dropped
_CAR_WORD = re.compile(r"(?<![A-Z0-9])CAR[ \-](SPACE|PARK)(?= +(?:[A-Z]{0,2}\d|[A-Z]\b))")          # Car Space 12 and Car Park 3 are Carspace and Carpark


def is_street_suffix(text: str) -> bool:
    """True when `text` is a street suffix name as `Addr.street_suffix` reads one (`NORTH`, `SOUTH WEST`)."""
    return text in _SUFFIX_NAMES


@dataclass(frozen=True)
class Addr:
    """One address in normalised parts. `rules` is not part of equality: two spellings of one address compare equal."""
    unit: str = ""
    level: str = ""
    building: str = ""
    lot: str = ""
    number_first: str = ""
    number_last: str = ""
    street_name: str = ""
    street_type: str = ""
    locality: str = ""
    state: str = ""
    postcode: str = ""
    rules: tuple[str, ...] = field(default=(), compare=False)

    @property
    def street_key(self) -> str:
        return f"{self.street_name} {self.street_type}".strip()

    @property
    def street_suffix(self) -> str:
        """The street suffix kept after the street type (NORTH in `STREET NORTH`), or an empty string. Two streets that differ only here are two streets."""
        return max((n for n in _SUFFIX_NAMES if f" {self.street_type}".endswith(f" {n}")), key=len, default="")

    def without_suffix(self) -> "Addr":
        """The same address with the street suffix left out (`STREET` for `STREET NORTH`), or this address when it has none."""
        suffix = self.street_suffix
        return replace(self, street_type=self.street_type[: len(self.street_type) - len(suffix)].strip()) if suffix else self

    @property
    def street_base(self) -> str:
        """The street name and type without the suffix: `SMITH STREET` for `SMITH` and `STREET NORTH`."""
        suffix = self.street_suffix
        return f"{self.street_name} {self.street_type[: len(self.street_type) - len(suffix)]}".strip()

    @property
    def number_key(self) -> str:
        if self.number_last and self.number_last != self.number_first:
            return f"{self.number_first}-{self.number_last}"
        return self.number_first

    @property
    def canon_key(self) -> str:
        """The key a derived property id comes from. The postcode is left out on purpose: a feed that drops or mistypes it must not mint a second id. A blank state is taken from
        the postcode's range (`state_for_postcode`), so a feed that leaves the state column empty reaches the same key as one that fills it; with neither, the state stays blank."""
        return "|".join((self.level, self.unit, self.lot, self.number_key, self.street_key, self.locality, self.state or state_for_postcode(self.postcode)))

    def line(self) -> str:
        """The canonical one line spelling. Normalising it again returns the same parts."""
        parts: list[str] = []
        if self.building:
            parts.append(self.building)
        front = []
        if self.level:
            front.append(_typed_text(self.level, "LEVEL", _LEVEL_WORDS, LEVEL_TYPES))
        if self.unit:
            front.append(_typed_text(self.unit, "UNIT", _FLAT_WORDS, FLAT_TYPES))
        if self.lot:
            front.append(f"LOT {self.lot}")
        if self.number_key:
            front.append(self.number_key)
        front.append(self.street_key)
        parts.append(" ".join(f for f in front if f))
        tail = " ".join(x for x in (self.locality, self.state, self.postcode) if x)
        if tail:
            parts.append(tail)
        return ", ".join(parts)


def _add(rules: list[str], rule: str) -> None:
    if rule not in rules:
        rules.append(rule)


def _typed_text(key: str, default_word: str, words: dict[str, str], names: dict[str, str]) -> str:
    """A unit or level key as words: `5` is `UNIT 5`, and `CARS 5` (a type kept in the key) is `CARSPACE 5`."""
    code, _, ident = key.partition(" ")
    if not ident:
        return f"{default_word} {key}"
    return f"{words.get(code) or names.get(code, code)} {ident}"


def canonical_street_type(word: str) -> str:
    """The G-NAF style street type for a spelling (any type G-NAF lists, by code or short name), or '' when the word is not a street type."""
    return _ALL_STREET_TYPES.get(word.upper(), "")


def common_street_type(word: str) -> str:
    """Like `canonical_street_type` for the common spellings only. Used to find where a street ends in text with no comma, where a word such as BEACH or PARK may be a suburb."""
    return STREET_TYPE_SPELLINGS.get(word.upper(), "")


def _ident(raw: str, rules: list[str]) -> str:
    """A unit or level id the G-NAF way: a leading zero is dropped (01 is 1, G01 is G1). A letter on its own (the B of Unit B) stays as it is."""
    if _LETTER.match(raw):
        return raw
    prefix, digits, tail = _PARTS.match(raw).groups()
    kept = digits.lstrip("0") or "0"
    if kept != digits:
        _add(rules, "LEADING_ZERO_DROPPED")
    return prefix + kept + tail


def _flat(marker: str, ident: str, rules: list[str]) -> str:
    """The unit key for a marker word and an id: the id alone, or the flat type code first for a carspace, a garage and the like."""
    cleaned = _ident(ident, rules)
    if marker in OTHER_FLAT_WORDS:
        _add(rules, "FLAT_TYPE_KEPT")
        return f"{OTHER_FLAT_WORDS[marker]} {cleaned}"
    return cleaned


def _level_slash(token: str) -> re.Match | None:
    """`2/45` or `G/12` straight after a level word (`Level 2/45 Pitt St`): the part before the slash is the level, not a unit. Anything else before the slash (`Level B2/12`) is not read as a level."""
    found = _SLASH.match(token)
    return found if found and (_ID.match(found.group(1)) or found.group(1) == "G") else None


def _takes_id(marker: str, nxt: str) -> bool:
    """True when `marker` is a unit, level or lot word that the next token completes."""
    if marker in _PLAIN_UNIT_WORDS:          # Building 2 and Tower 3 are decided by `_numbered_word_unit`, which needs the words behind them
        return bool(_UID.match(nxt) or _SLASH.match(nxt))
    if marker in LEVEL_MARKERS:
        return bool(_ID.match(nxt) or nxt in ("G", "GROUND") or _level_slash(nxt))
    return (marker == "LOT" or marker in OTHER_LEVEL_WORDS) and bool(_ID.match(nxt))


def _numbered_word_unit(tokens: list[str], i: int) -> bool:
    """True when tokens[i] is a word such as Building, Tower or Block with a number after it, and another number stands behind that one: `Tower 3 100 Pacific Hwy` is unit 3 of number 100.
    With no number behind it the first number is the house number and the word is part of a building name (`Skyline Tower 12 Smith St`), as it always was."""
    return tokens[i] in NUMBERED_UNIT_WORDS and bool(_ID.match(_peek(tokens, i))) and any(_NUMBER.match(t) or _SLASH.match(t) for t in tokens[i + 2:])


def _letter_unit_follows(tokens: list[str], i: int) -> bool:
    """True when tokens[i] is a unit word followed by one letter and a number stands somewhere behind the letter (`Unit B 12 Smith St`): the letter is the unit. A letter with no number
    behind it is left alone, because it could start the street."""
    return tokens[i] in _UNIT_WORDS and bool(_LETTER.match(_peek(tokens, i))) and any(ch.isdigit() for t in tokens[i + 2:] for ch in t)


def _unit_written_later(tokens: list[str], start: int) -> bool:
    """True when a unit of its own (a unit word with an id, a glued `U5`, or `5/12`) is written at or after position `start`."""
    return any(_SLASH.match(t) or _GLUED.match(t) or (t in UNIT_MARKERS and _takes_id(t, _peek(tokens, j))) for j, t in enumerate(tokens[start:], start))


def _unit_after_street(words: list[str]) -> tuple[int, str, str] | None:
    """Find a unit written after the street type (`Smith St Unit 5`, `Smith Street Apt 6`, `Smith St #5`). Returns (index of the unit word, the word, the id as written), the word being ''
    for a glued `#5`, or None when there is none. The word must stand straight after a street type (or the suffix behind one) that has a name before it, so a word inside a
    street name is never taken."""
    for j in range(2, len(words)):
        before = words[j - 1]
        if not (canonical_street_type(before) or (j >= 3 and before in STREET_SUFFIX_WORDS and canonical_street_type(words[j - 2]))):
            continue
        glued = _GLUED.match(words[j])
        if glued and not glued.group(3):
            return j, "", glued.group(2)
        nxt = _peek(words, j)
        if words[j] in _UNIT_WORDS and (_takes_id(words[j], nxt) or _LETTER.match(nxt) or (words[j] in NUMBERED_UNIT_WORDS and _ID.match(nxt))) and not _SLASH.match(nxt):
            return j, words[j], nxt
    return None


def _drop_country(chunks: list[str]) -> list[str]:
    """Drop a country written at the end (`, Australia`, `NSW 2000 AU`): a last comma chunk that is only the country, or a last word that is the country straight after a state or a postcode."""
    if len(chunks) >= 2 and chunks[-1] in _COUNTRY_WORDS:
        return chunks[:-1]
    words = chunks[-1].split() if chunks else []
    if len(words) >= 2 and words[-1] in _COUNTRY_WORDS:
        _, state, postcode = _split_tail(words[:-1], [])
        if state or postcode:
            return [*chunks[:-1], " ".join(words[:-1])]
    return chunks


def _take_suffix(words: list[str]) -> tuple[list[str], str]:
    """Split a street suffix (NORTH, N, UPPER, NORTH EAST) off the end of the street words. It counts only straight after a street type that has a name before it."""
    for n in (2, 1):
        if len(words) >= n + 2 and " ".join(words[-n:]) in STREET_SUFFIX_WORDS and canonical_street_type(words[-n - 1]):
            return words[:-n], STREET_SUFFIX_WORDS[" ".join(words[-n:])]
    return words, ""


def _postcode(raw: str, state: str, rules: list[str]) -> str:
    """A four digit postcode. An NT postcode that lost its leading zero (800 for 0800) gets it back; any other odd shape is dropped and recorded."""
    if not raw:
        return ""
    if len(raw) == 3 and raw.isdigit() and state == "NT":
        _add(rules, "POSTCODE_ZERO_RESTORED")
        return "0" + raw
    if not _POSTCODE.match(raw):
        _add(rules, "POSTCODE_BAD_SHAPE")
        return ""
    return raw


def _name_words(words: list[str], rules: list[str], *, saint_everywhere: bool) -> str:
    """Expand Mt and St inside a street or suburb name. A leading St is Saint; inside a suburb name every St is Saint."""
    out: list[str] = []
    for i, w in enumerate(words):
        if w == "MT":
            _add(rules, "MOUNT_EXPANDED")
            w = "MOUNT"
        elif w == "ST" and (saint_everywhere or i < len(words) - 1):
            _add(rules, "SAINT_EXPANDED")
            w = "SAINT"
        out.append(w)
    return " ".join(out)


def _join_ranges(raw: str) -> str:
    """Write a street number range as `12-14`: `12 - 14`, `12 - 14` with an en or em dash, `12 & 14` and `12 to 14`. A pair of numbers right after a unit, level or lot word is not a range of house numbers and is left as written."""
    def one(m: re.Match) -> str:
        word = re.search(r"([A-Za-z#]+)\s*$", raw[:m.start()])
        before = word.group(1).upper() if word else ""
        if before in _RANGE_BLOCKERS or before[:-1] in _RANGE_BLOCKERS:
            return m.group(0)
        return f"{m.group(1)}-{m.group(2)}"
    return _RANGE.sub(one, raw)


def _prep(text: str, rules: list[str]) -> str:
    folded = unicodedata.normalize("NFKC", text)
    if folded != folded.upper():
        _add(rules, "CASE_FOLDED")
    t = folded.upper().replace("'", "").replace("’", "")
    undotted = _DOTTED_STATE.sub(lambda m: re.sub(r"[^A-Z]", "", m.group(0)) + (" " if m.string[m.end():m.end() + 1].isdigit() else ""), t)
    if undotted != t:
        _add(rules, "STATE_DOTS_REMOVED")
        t = undotted
    t = t.replace(";", ",").replace(":", ",")
    cleaned = _DISALLOWED.sub(" ", t)
    if cleaned != t:
        _add(rules, "PUNCT_STRIPPED")
    if re.search(r"\s{2,}", cleaned.strip()):
        _add(rules, "WHITESPACE_COLLAPSED")
    return cleaned


def _peek(tokens: list[str], i: int) -> str:
    """The token after position i, or an empty string at the end."""
    return (tokens[i + 1:i + 2] or [""])[0]


def _core_start(tokens: list[str]) -> int | None:
    """Index of the first token that starts the unit, level, lot or street number part of an address, or None."""
    for i, t in enumerate(tokens):
        nxt = _peek(tokens, i)
        if _GLUED.match(t) or _SLASH.match(t) or _NUMBER.match(t):
            return i
        if _takes_id(t, nxt) or _letter_unit_follows(tokens, i) or _numbered_word_unit(tokens, i):
            return i
    return None


def _split_tail(words: list[str], rules: list[str]) -> tuple[list[str], str, str]:
    """Take `STATE [POSTCODE]` off the end of a word list. Returns (words left, state, postcode)."""
    raw_postcode = words.pop() if words and re.fullmatch(r"\d{3,5}", words[-1]) else ""
    state = ""
    for n in range(max(len(k.split()) for k in STATE_NAMES), 0, -1):
        if len(words) >= n and " ".join(words[-n:]) in STATE_NAMES:
            state = STATE_NAMES[" ".join(words[-n:])]
            del words[-n:]
            _add(rules, "STATE_NAME_TO_CODE")
            break
    if not state and words and words[-1] in STATES:
        state = words.pop()
    return words, state, _postcode(raw_postcode, state, rules)


def _suburb_split(words: list[str]) -> tuple[list[str], list[str]]:
    """No comma told us where the street ends. The street ends at the first common street type word that has a name before it and something after it; a type that only
    G-NAF lists (GARDENS, HEIGHTS) is looked for next, when no common one split the words."""
    for lookup in (common_street_type, canonical_street_type):
        for i, w in enumerate(words):
            if i < 1 or not lookup(w):
                continue
            if i < len(words) - 1:          # no-mutate: a split at the last word gives the same two lists as no split
                return words[: i + 1], words[i + 1:]
    return words, []


def _is_state_postcode(chunk: str) -> bool:
    """True for a comma chunk that holds only a state, a postcode or both (`NSW 2557`, `2557`)."""
    words, state, postcode = _split_tail(chunk.split(), [])
    return not words and bool(state or postcode)


def _locality_like(chunk: str) -> bool:
    """True for a comma chunk that can be a suburb name: no digit, not a unit, level or lot word first, and not ending in a common street type."""
    words = chunk.split()
    return bool(words) and not any(ch.isdigit() for ch in chunk) and words[0] not in _NOT_A_LOCALITY_START and not common_street_type(words[-1])


def _read_comma_tail(chunks: list[str], trial: list[str]) -> tuple[list[str], str, str, list[str]] | None:
    """Read the locality, state and postcode off the end of a comma separated address. Returns (chunks left, state, postcode, locality words), or None when the end holds none.

    A comma between locality, state and postcode (`Fakeville, NSW 2000`, `Fakeville, NSW, 2000`) is a separator the same as a space. A last chunk that also holds the
    street (`12 Smith St Fakeville NSW 2000`, after a comma behind the unit) is not a tail chunk: it is left for the no comma reading of the street words."""
    k = len(chunks)
    while k > 1 and _is_state_postcode(chunks[k - 1]):
        k -= 1
    if k < len(chunks):
        tail_chunks = chunks[k:]
        if len(tail_chunks) >= 2 and re.fullmatch(r"\d{3,5}", tail_chunks[-2]) and not any(ch.isdigit() for ch in tail_chunks[-1]):
            tail_chunks[-2:] = tail_chunks[-1], tail_chunks[-2]          # the postcode written before the state (`Fakeville, 2000, NSW`) reads as `NSW 2000`
        rest, tail_words = chunks[:k], " ".join(tail_chunks).split()
        if len(rest) >= 2 and _locality_like(rest[-1]):          # the chunk before may be the suburb alone (`Fakeville`) or with the state (`Fakeville NSW`)
            tail_words, rest = rest[-1].split() + tail_words, rest[:-1]
        locality, state, postcode = _split_tail(tail_words, trial)
        return rest, state, postcode, locality
    if len(chunks) < 2:
        return None
    words, state, postcode = _split_tail(chunks[-1].split(), trial)
    street_part = any(ch.isdigit() for w in words for ch in w) or (bool(words) and words[0] in _NOT_A_LOCALITY_START)
    if (state or postcode) and not street_part or _locality_like(chunks[-1]):
        return chunks[:-1], state, postcode, words
    return None


def _same_words(a: list[str], b: list[str]) -> bool:
    return " ".join(" ".join(a).replace("-", " ").split()) == " ".join(" ".join(b).replace("-", " ").split())


def _tail_agrees(state: str, postcode: str, locality: list[str], state2: str, postcode2: str, locality2: list[str]) -> bool:
    """True when what the line says does not contradict what the columns say: each part is either missing on one side or equal on both."""
    return ((not state2 or not state or state2 == state) and (not postcode2 or not postcode or postcode2 == postcode)
            and (not locality2 or not locality or _same_words(locality2, locality)))


_STATE_BY_POSTCODE = (          # (first, last, state): the ranges Australia Post publishes; ACT and NT sit inside the NSW and SA ranges they were cut from
    (200, 299, "ACT"), (800, 999, "NT"), (1000, 1999, "NSW"), (2000, 2599, "NSW"), (2600, 2618, "ACT"), (2619, 2899, "NSW"), (2900, 2920, "ACT"), (2921, 2999, "NSW"),
    (3000, 3999, "VIC"), (4000, 4999, "QLD"), (5000, 5999, "SA"), (6000, 6999, "WA"), (7000, 7999, "TAS"), (8000, 8999, "VIC"), (9000, 9999, "QLD"),
)


def state_for_postcode(postcode: str) -> str:
    """The state a postcode belongs to by its range, or '' for a postcode that is not four digits or is in no range. Used only to complete a derived key, never to fill a column."""
    if not _POSTCODE.match(postcode or ""):
        return ""
    value = int(postcode)
    return next((state for lo, hi, state in _STATE_BY_POSTCODE if lo <= value <= hi), "")


def normalise_address(line: str | None, suburb: str | None = None, state: str | None = None, postcode: str | None = None) -> Addr:
    """Parse one address.

    `line` is the street part (`Unit 5, 12 Smith St`) or the whole address (`5/12 Smith St, Fakeville NSW 2000`). `suburb`, `state` and `postcode` are the
    separate fields of a feed, used when the feed has them; otherwise the tail of the line is read. Nothing is guessed: a part that is not there stays empty
    and the rule that says so is recorded.
    """
    rules: list[str] = []
    raw = line or ""
    unbracketed = _BRACKET_NOTE.sub(" ", raw)
    if unbracketed != raw:
        _add(rules, "BRACKET_NOTE_DROPPED")
        raw = unbracketed
    joined = _DOTTED_SLASH.sub(r"\1\2", _UNIT_DOTTED.sub(lambda m: m.group(1) + m.group(2) + (m.group(3) or m.group(4)), raw))
    if joined != raw:
        _add(rules, "UNIT_NUMBER_JOINED")
        raw = joined
    ranged = _join_ranges(raw)
    if ranged != raw:
        _add(rules, "NUMBER_RANGE_READ")
        raw = ranged
    text = _prep(raw, rules)
    if _POSTAL.search(text):
        _add(rules, "POSTAL_ADDRESS")
        return Addr(rules=tuple(rules))
    for pattern, replacement, rule in ((_SPACED_SLASH, "/", "UNIT_SLASH_SPACED"), (_UNIT_NO_WORD, r"\1 ", "UNIT_NO_WORD_DROPPED"), (_CAR_WORD, r"CAR\1", "CAR_WORD_JOINED"),
                                       (_PLAN, r"\1", "LOT_PLAN_DROPPED")):
        changed = pattern.sub(replacement, text)
        if changed != text:
            _add(rules, rule)
            text = changed
    written = [c.strip() for c in text.split(",") if c.strip()]
    chunks = _drop_country(written)
    if chunks != written:
        _add(rules, "COUNTRY_DROPPED")
    st, pc, locality_words = "", "", []
    have_columns = any((v or "").strip() for v in (suburb, state, postcode))          # blank columns are a feed that has none: the line is the whole address
    if have_columns:
        locality_words = _prep(suburb or "", rules).replace("-", " ").split()
        if state:
            raw_state = " ".join(_prep(state, rules).split())
            st = STATE_NAMES.get(raw_state, raw_state if raw_state in STATES else "")
            if raw_state in STATE_NAMES:
                _add(rules, "STATE_NAME_TO_CODE")
        pc = _postcode((postcode or "").strip(), st, rules)
    tail_locality = comma_state = False
    trial: list[str] = []
    tail = _read_comma_tail(chunks, trial)
    if tail is not None:
        rest, st2, pc2, loc2 = tail
        if _tail_agrees(st, pc, locality_words, st2, pc2, loc2):
            filled = (not st and bool(st2)) or (not pc and bool(pc2)) or (not locality_words and bool(loc2))
            st, pc, locality_words = st or st2, pc or pc2, locality_words or loc2
            tail_locality = bool(loc2)
            comma_state = bool(st2 or pc2)          # the state or postcode came from comma chunks: with no suburb chunk before them the suburb may still end the street words
            chunks = rest
            for r in ((*trial, "SUBURB_FROM_LINE") if filled else ("LINE_TAIL_REPEATED",)):
                _add(rules, r)
    body = " ".join(chunks)
    floors = _ORDINAL_FLOOR.sub(r"LEVEL \1", _GROUND_FLOOR.sub("LEVEL G", body))
    if floors != body:
        _add(rules, "FLOOR_WORDS_READ")
        body = floors
    # a building name is the words before the first unit, level, lot or number, when none of them has a digit (a comma after it makes no difference)
    building = ""
    tokens = body.split()
    start = _core_start(tokens)
    if start and not any(ch.isdigit() for t in tokens[:start] for ch in t):
        building = " ".join(tokens[:start]).replace("-", " ")
        tokens = tokens[start:]
        _add(rules, "BUILDING_NAME_PREFIX")
    unit = level = lot = first = last_n = ""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = _peek(tokens, i)
        glued = _GLUED.match(t)
        if glued and not unit:
            unit = _ident(glued.group(2), rules)
            _add(rules, "UNIT_MARKER_GLUED")
            if glued.group(3):
                tokens[i] = glued.group(3)
                _add(rules, "UNIT_SLASH_FORM")
            else:
                i += 1
            continue
        numbered = _numbered_word_unit(tokens, i)
        if numbered and (unit or _unit_written_later(tokens, i + 2)):
            building = f"{building} {t} {nxt}".strip()          # `Tower 3, Unit 5, 12 Smith St` and `Unit 5, Tower 3, 12 Smith St`: Tower 3 is the building, the unit is the one written as a unit
            _add(rules, "BUILDING_NAME_PREFIX")
            i += 2
            continue
        if numbered and not unit:
            unit = _flat(t, nxt, rules)
            _add(rules, "UNIT_MARKER_WORD")
            i += 2
            continue
        if t in _UNIT_WORDS and not unit and nxt:
            slash = _SLASH.match(nxt) if t in _PLAIN_UNIT_WORDS else None
            if slash:
                unit, first, last_n = _flat(t, slash.group(1), rules), slash.group(2), slash.group(3) or ""
                _add(rules, "UNIT_MARKER_WORD")
                _add(rules, "UNIT_SLASH_FORM")
                i += 2
                break
            if _takes_id(t, nxt) or _letter_unit_follows(tokens, i):
                unit = _flat(t, nxt, rules)
                _add(rules, "UNIT_MARKER_WORD")
                i += 2
                continue
        level_slash = _level_slash(nxt) if t in LEVEL_MARKERS and not level else None
        if level_slash:
            level = _ident(level_slash.group(1), rules)
            first, last_n = level_slash.group(2), level_slash.group(3) or ""
            for r in ("LEVEL_MARKER", "LEVEL_PREFIX_FORM", "UNIT_SLASH_FORM"):
                _add(rules, r)
            i += 2
            break
        if t in LEVEL_MARKERS and not level and nxt and (_ID.match(nxt) or nxt in ("G", "GROUND")):
            level = "G" if nxt in ("G", "GROUND") else _ident(nxt, rules)
            _add(rules, "LEVEL_MARKER")
            i += 2
            continue
        if t in OTHER_LEVEL_WORDS and not level and nxt and _ID.match(nxt):
            level = f"{OTHER_LEVEL_WORDS[t]} {_ident(nxt, rules)}"
            _add(rules, "LEVEL_TYPE_KEPT")
            i += 2
            continue
        if t == "LOT" and not lot and nxt and _ID.match(nxt):
            lot = nxt
            _add(rules, "LOT_MARKER")
            i += 2
            continue
        slash = _SLASH.match(t)
        if slash and not unit:
            head, first, last_n = slash.group(1), slash.group(2), slash.group(3) or ""
            if _LEVEL_PREFIX.match(head) and not level:
                level = _ident(head[1:], rules)
                _add(rules, "LEVEL_PREFIX_FORM")
            else:
                unit = _ident(head, rules)
                if head[0].isalpha():
                    _add(rules, "UNIT_PREFIX_FORM")
            _add(rules, "UNIT_SLASH_FORM")
            i += 1
            break
        num = _NUMBER.match(t)
        dashed = _DASH_LETTER.match(t)
        street_words_left = len(tokens) - i - 1          # words after this one: a street name and a type at least, or the letter is part of the street
        if num and not first:
            first, last_n = num.group(1), num.group(2) or ""
            i += 1
            if t.isdigit() and re.fullmatch(r"[A-Z]", nxt) and street_words_left >= 3:
                first += nxt          # `4 A St Ingowick Street` is number 4A, never number 4
                _add(rules, "NUMBER_SUFFIX_SPACED")
                i += 1
        elif dashed and not first and street_words_left >= 2:
            first = dashed.group(1) + dashed.group(2)
            _add(rules, "NUMBER_SUFFIX_SPACED")
            i += 1
        break
    words = tokens[i:]
    found = _unit_after_street(words)
    if found and (not unit or unit == _flat(found[1], found[2], [])):          # a second, different unit is not guessed between: it stays where it was
        unit = _flat(found[1], found[2], rules)
        _add(rules, "UNIT_AFTER_STREET")
        del words[found[0]:found[0] + (2 if found[1] else 1)]
    if not tail_locality:
        # no comma told where the street ends: read `... STATE POSTCODE` off the end, then take the suburb from the street words
        trial = []
        w2, st2, pc2 = _split_tail(list(words), trial)
        n = len(locality_words)
        repeats = n > 0 and len(w2) > n + 1 and _same_words(w2[-n:], locality_words)
        street_w, loc2, heuristic = None, [], False
        if st2 or pc2:
            if not locality_words:
                street_w, loc2 = _suburb_split(w2)
                heuristic = bool(loc2)
            elif repeats:
                street_w, loc2 = w2[:-n], list(locality_words)
            elif w2 and (_take_suffix(list(w2))[1] or canonical_street_type(w2[-1])):
                street_w = w2
        elif repeats and common_street_type(w2[-n - 1]):
            street_w, loc2 = w2[:-n], list(locality_words)          # the columns give the suburb and the line ends with it straight after a street type: said twice
        elif not locality_words and not _take_suffix(list(w2))[1]:
            split_w, split_loc = _suburb_split(w2)
            if split_loc and (comma_state or _locality_like(" ".join(split_loc))):          # with no state or postcode behind it the words must also look like a suburb: no digit, no unit word first, no street type last
                street_w, loc2, heuristic = split_w, split_loc, True
        if street_w is not None and _tail_agrees(st, pc, locality_words, st2, pc2, loc2):
            filled = (not st and bool(st2)) or (not pc and bool(pc2)) or (not locality_words and bool(loc2))
            words, st, pc, locality_words = street_w, st or st2, pc or pc2, locality_words or loc2
            for r in ((*trial, "SUBURB_FROM_LINE") if filled else ("LINE_TAIL_REPEATED",)):
                _add(rules, r)
            if heuristic:
                _add(rules, "SUBURB_SPLIT_HEURISTIC")
    words = [w for part in words for w in part.replace("-", " ").split()]
    words, suffix = _take_suffix(words)
    street_type = ""
    if len(words) >= 2 and canonical_street_type(words[-1]):
        street_type = canonical_street_type(words[-1])
        if words[-1] != street_type:
            _add(rules, "STREET_TYPE_EXPANDED")
        words = words[:-1]
    if suffix:
        street_type = f"{street_type} {suffix}".strip()
        _add(rules, "STREET_SUFFIX_KEPT")
    street_name = _name_words(words, rules, saint_everywhere=False)
    locality = _name_words(locality_words, rules, saint_everywhere=True)
    if not pc:
        _add(rules, "POSTCODE_MISSING")
    if not st:
        _add(rules, "STATE_MISSING")
    if not locality:
        _add(rules, "SUBURB_MISSING")
    return Addr(unit=unit, level=level, building=building, lot=lot, number_first=first, number_last=last_n, street_name=street_name,
                street_type=street_type, locality=locality, state=st, postcode=pc, rules=tuple(rules))


_FLAT_CODES = {**{name: code for code, name in FLAT_TYPES.items()}, **{code: code for code in FLAT_TYPES}}
_LEVEL_CODES = {**{name: code for code, name in LEVEL_TYPES.items()}, **{code: code for code in LEVEL_TYPES}}


def gnaf_part(prefix, number, suffix) -> str:
    """A G-NAF number with its prefix and suffix written together: `G`, 1, `A` is `G1A`."""
    num = "" if number in (None, "") else str(int(number))
    return f"{prefix or ''}{num}{suffix or ''}".upper()


def _gnaf_unit(row: dict) -> str:
    """The unit key of a G-NAF row: the flat number, with the flat type code first for a carspace, a garage and the other types in DISTINCT_FLAT_TYPES."""
    ident = gnaf_part(row.get("flat_number_prefix"), row.get("flat_number"), row.get("flat_number_suffix"))
    code = _FLAT_CODES.get((row.get("flat_type_code") or "").upper(), "")
    return f"{code} {ident}" if ident and code in DISTINCT_FLAT_TYPES else ident


def _gnaf_level(row: dict) -> str:
    """The level key of a G-NAF row: the number, with the level type code first for any type but a plain level or floor (B 1 is basement 1, G is ground)."""
    ident = gnaf_part(row.get("level_number_prefix"), row.get("level_number"), row.get("level_number_suffix"))
    code = _LEVEL_CODES.get((row.get("level_type_code") or "").upper(), "")
    if code in ("", "L", "FL"):
        return ident
    return f"{code} {ident}".strip()


def gnaf_display_line(row: dict) -> str:
    """The address as G-NAF itself spells it (ST KILDA stays ST KILDA, not SAINT KILDA), upper case, for the master property list. A property made from a source record uses `Addr.line`."""
    def typed(ident: str, code: str, default_word: str, names: dict[str, str]) -> str:
        word = names.get(code, default_word) if code else default_word
        return f"{word} {ident}".strip() if ident or code else ""

    level = typed(gnaf_part(row.get("level_number_prefix"), row.get("level_number"), row.get("level_number_suffix")),
                  _LEVEL_CODES.get((row.get("level_type_code") or "").upper(), ""), "LEVEL", LEVEL_TYPES)
    flat_ident = gnaf_part(row.get("flat_number_prefix"), row.get("flat_number"), row.get("flat_number_suffix"))
    unit = typed(flat_ident, _FLAT_CODES.get((row.get("flat_type_code") or "").upper(), ""), "UNIT", FLAT_TYPES) if flat_ident else ""
    lot = gnaf_part(row.get("lot_number_prefix"), None, "") + (row.get("lot_number") or "").upper() + (row.get("lot_number_suffix") or "").upper()
    first = gnaf_part(row.get("number_first_prefix"), row.get("number_first"), row.get("number_first_suffix"))
    last = gnaf_part(row.get("number_last_prefix"), row.get("number_last"), row.get("number_last_suffix"))
    suffix_code = (row.get("street_suffix_code") or "").upper()
    street = " ".join(x for x in ((row.get("street_name") or "").strip().upper(), (row.get("street_type_code") or "").strip().upper(),
                                  STREET_SUFFIXES.get(suffix_code, suffix_code)) if x)
    front = " ".join(x for x in (level, unit, f"LOT {lot}" if lot else "", f"{first}-{last}" if first and last else first, street) if x)
    tail = " ".join(x for x in ((row.get("locality_name") or "").strip().upper(), (row.get("state_abbreviation") or "").upper(), (row.get("postcode") or "").strip()) if x)
    return ", ".join(x for x in ((row.get("building_name") or "").strip().upper(), front, tail) if x)


def addr_from_gnaf(row: dict) -> Addr:
    """An `Addr` from one flattened G-NAF row (the ADDRESS_DETAIL parts joined with STREET_LOCALITY, LOCALITY and STATE)."""
    rules: list[str] = []
    street_words = _prep(row.get("street_name") or "", rules).replace("-", " ").split()
    type_code = (row.get("street_type_code") or "").upper()
    street_type = canonical_street_type(type_code) or type_code
    suffix_code = (row.get("street_suffix_code") or "").upper()
    if suffix_code:
        street_type = f"{street_type} {STREET_SUFFIXES.get(suffix_code, suffix_code)}".strip()
    locality_words = _prep(row.get("locality_name") or "", rules).replace("-", " ").split()
    building = " ".join(_prep(row.get("building_name") or "", rules).replace("-", " ").split())
    lot = gnaf_part(row.get("lot_number_prefix"), None, "") + (row.get("lot_number") or "").upper() + (row.get("lot_number_suffix") or "").upper()
    return Addr(
        unit=_gnaf_unit(row),
        level=_gnaf_level(row),
        building=building,
        lot=lot,
        number_first=gnaf_part(row.get("number_first_prefix"), row.get("number_first"), row.get("number_first_suffix")),
        number_last=gnaf_part(row.get("number_last_prefix"), row.get("number_last"), row.get("number_last_suffix")),
        street_name=_name_words(street_words, [], saint_everywhere=False),
        street_type=street_type,
        locality=_name_words(locality_words, [], saint_everywhere=True),
        state=(row.get("state_abbreviation") or "").upper(),
        postcode=(row.get("postcode") or "").strip(),
    )


def normalise_phone(raw: str | None) -> tuple[str, tuple[str, ...]]:
    """An Australian phone number as E.164 (`+61412345678`), or '' when it cannot be read as one. Returns (number, rules)."""
    rules: list[str] = []
    text = (raw or "").strip()
    if not text:
        return "", ()
    had_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if digits.startswith("0061"):
        digits = digits[4:]
        _add(rules, "PHONE_COUNTRY_CODE_STRIPPED")
    elif digits.startswith("61") and (had_plus or len(digits) >= 11):
        digits = digits[2:]
        _add(rules, "PHONE_COUNTRY_CODE_STRIPPED")
    if digits.startswith("0"):
        digits = digits[1:]
        _add(rules, "PHONE_TRUNK_ZERO_STRIPPED")
    if re.fullmatch(r"4\d{8}|[2378]\d{8}", digits):
        if len(set(digits[1:])) > 1:
            _add(rules, "PHONE_E164")
            return "+61" + digits, tuple(rules)
        _add(rules, "PHONE_PLACEHOLDER")          # 0400 000 000, 0411 111 111: one digit eight times is a form filled with a placeholder, not one person's number
    _add(rules, "PHONE_INVALID")
    return "", tuple(rules)


def normalise_email(raw: str | None) -> tuple[str, tuple[str, ...]]:
    """A lower case email, or '' when it is not one. The local part is never edited beyond case: no dots removed, no plus tags cut."""
    rules: list[str] = []
    text = (raw or "").strip()
    if not text:
        return "", ()
    if text != (raw or ""):
        _add(rules, "EMAIL_TRIMMED")
    if text.lower().startswith("mailto:"):
        text = text[7:]
        _add(rules, "EMAIL_MAILTO_STRIPPED")
    while True:          # a note in brackets and the ; , . a list or a sentence leaves at the end are not part of the address, in whatever order they come
        trimmed = _EMAIL_TAIL_NOTE.sub("", text).rstrip(" ;,.")
        if trimmed == text:
            break
        text = trimmed
        _add(rules, "EMAIL_TAIL_DROPPED")
    named = _EMAIL_NAMED.match(text)
    if named and named.group(1).strip():
        text = named.group(2)          # a display name in front of the angle brackets is dropped, the address kept
        _add(rules, "EMAIL_NAME_DROPPED")
    text = text.strip("<> ")
    lowered = text.lower()
    if lowered != text:
        _add(rules, "EMAIL_LOWERCASED")
    if lowered in PLACEHOLDER_EMAILS:
        _add(rules, "EMAIL_PLACEHOLDER")
    if lowered in PLACEHOLDER_EMAILS or not _EMAIL.match(lowered):
        _add(rules, "EMAIL_INVALID")
        return "", tuple(rules)
    return lowered, tuple(rules)


def normalise_name(raw: str | None) -> str:
    """A display name with the spaces collapsed. It is shown, never matched on."""
    return " ".join((raw or "").split())

