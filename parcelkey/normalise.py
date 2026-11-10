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
from dataclasses import dataclass, field

STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
STATE_NAMES = {
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD", "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA",
    "TASMANIA": "TAS", "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}

# Street type spellings (left) to the G-NAF style code (right).
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
UNIT_MARKERS = frozenset({"UNIT", "U", "APT", "APARTMENT", "FLAT", "SUITE", "STE", "SHOP", "VILLA", "OFFICE", "TOWNHOUSE", "TH", "ROOM", "#"})
LEVEL_MARKERS = frozenset({"LEVEL", "LVL", "L", "FLOOR", "FLR"})

_ID = re.compile(r"^\d+[A-Z]?$")
_NUMBER = re.compile(r"^(\d+[A-Z]?)(?:-(\d+[A-Z]?))?$")
_SLASH = re.compile(r"^(\d+[A-Z]?)/(\d+[A-Z]?)(?:-(\d+[A-Z]?))?$")
_GLUED = re.compile("^(" + "|".join(re.escape(m) for m in sorted(UNIT_MARKERS, key=len, reverse=True)) + r")(\d+[A-Z]?)(?:/(.+))?$")          # no-mutate: alternation backtracks, so the order of the markers only changes speed
_DISALLOWED = re.compile(r"[^A-Z0-9/,\- #]")
_POSTCODE = re.compile(r"^\d{4}$")
_EMAIL = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$")


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
    def number_key(self) -> str:
        if self.number_last and self.number_last != self.number_first:
            return f"{self.number_first}-{self.number_last}"
        return self.number_first

    @property
    def canon_key(self) -> str:
        """The key a derived property id comes from. The postcode is left out on purpose: a feed that drops or mistypes it must not mint a second id."""
        return "|".join((self.level, self.unit, self.lot, self.number_key, self.street_key, self.locality, self.state))

    def line(self) -> str:
        """The canonical one line spelling. Normalising it again returns the same parts."""
        parts: list[str] = []
        if self.building:
            parts.append(self.building)
        front = []
        if self.level:
            front.append(f"LEVEL {self.level}")
        if self.unit:
            front.append(f"UNIT {self.unit}")
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


def canonical_street_type(word: str) -> str:
    """The G-NAF style street type for a spelling, or '' when the word is not a street type."""
    return STREET_TYPE_SPELLINGS.get(word.upper(), "")


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


def _prep(text: str, rules: list[str]) -> str:
    folded = unicodedata.normalize("NFKC", text)
    if folded != folded.upper():
        _add(rules, "CASE_FOLDED")
    t = folded.upper().replace("'", "").replace("’", "")
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
        if (t in UNIT_MARKERS or t in LEVEL_MARKERS or t == "LOT") and (_ID.match(nxt) or _SLASH.match(nxt) or (t in LEVEL_MARKERS and nxt in ("G", "GROUND"))):
            return i
    return None


def _split_tail(words: list[str], rules: list[str]) -> tuple[list[str], str, str]:
    """Take `STATE [POSTCODE]` off the end of a word list. Returns (words left, state, postcode)."""
    postcode = ""
    if words and re.fullmatch(r"\d{3,5}", words[-1]):
        postcode = words.pop()
        if not _POSTCODE.match(postcode):
            _add(rules, "POSTCODE_BAD_SHAPE")
            postcode = ""
    state = ""
    for n in range(max(len(k.split()) for k in STATE_NAMES), 0, -1):
        if len(words) >= n and " ".join(words[-n:]) in STATE_NAMES:
            state = STATE_NAMES[" ".join(words[-n:])]
            del words[-n:]
            _add(rules, "STATE_NAME_TO_CODE")
            break
    if not state and words and words[-1] in STATES:
        state = words.pop()
    return words, state, postcode


def _suburb_split(words: list[str]) -> tuple[list[str], list[str]]:
    """No comma told us where the street ends. The street ends at the first street type word that has a name before it and something after it."""
    for i, w in enumerate(words):
        if i < 1 or not canonical_street_type(w):
            continue
        if i < len(words) - 1:          # no-mutate: a split at the last word gives the same two lists as no split
            return words[: i + 1], words[i + 1:]
    return words, []


def normalise_address(line: str | None, suburb: str | None = None, state: str | None = None, postcode: str | None = None) -> Addr:
    """Parse one address.

    `line` is the street part (`Unit 5, 12 Smith St`) or the whole address (`5/12 Smith St, Fakeville NSW 2000`). `suburb`, `state` and `postcode` are the
    separate fields of a feed, used when the feed has them; otherwise the tail of the line is read. Nothing is guessed: a part that is not there stays empty
    and the rule that says so is recorded.
    """
    rules: list[str] = []
    text = _prep(line or "", rules)
    chunks = [c.strip() for c in text.split(",") if c.strip()]
    tail_words: list[str] = []
    st, pc, locality_words = "", "", []
    if suburb is not None or state is not None or postcode is not None:
        locality_words = _prep(suburb or "", rules).replace("-", " ").split()
        if state:
            raw_state = " ".join(_prep(state, rules).split())
            st = STATE_NAMES.get(raw_state, raw_state if raw_state in STATES else "")
            if raw_state in STATE_NAMES:
                _add(rules, "STATE_NAME_TO_CODE")
        pc = (postcode or "").strip()
        if pc and not _POSTCODE.match(pc):
            _add(rules, "POSTCODE_BAD_SHAPE")
            pc = ""
    elif len(chunks) >= 2:
        trial: list[str] = []
        last = chunks[-1].split()
        words, st2, pc2 = _split_tail(list(last), trial)
        suburb_only = not any(ch.isdigit() for ch in chunks[-1]) and last[0] not in UNIT_MARKERS | LEVEL_MARKERS | {"LOT"} and not canonical_street_type(last[-1])
        if st2 or pc2 or suburb_only:
            st, pc, tail_words, locality_words = st2, pc2, words, words
            for r in (*trial, "SUBURB_FROM_LINE"):
                _add(rules, r)
            chunks.pop()
    body = " ".join(chunks)
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
            unit = glued.group(2)
            _add(rules, "UNIT_MARKER_GLUED")
            if glued.group(3):
                tokens[i] = glued.group(3)
                _add(rules, "UNIT_SLASH_FORM")
            else:
                i += 1
            continue
        if t in UNIT_MARKERS and not unit and nxt:
            slash = _SLASH.match(nxt)
            if slash:
                unit, first, last_n = slash.group(1), slash.group(2), slash.group(3) or ""
                _add(rules, "UNIT_MARKER_WORD")
                _add(rules, "UNIT_SLASH_FORM")
                i += 2
                break
            if _ID.match(nxt):
                unit = nxt
                _add(rules, "UNIT_MARKER_WORD")
                i += 2
                continue
        if t in LEVEL_MARKERS and not level and nxt and (_ID.match(nxt) or nxt in ("G", "GROUND")):
            level = "G" if nxt == "GROUND" else nxt
            _add(rules, "LEVEL_MARKER")
            i += 2
            continue
        if t == "LOT" and not lot and nxt and _ID.match(nxt):
            lot = nxt
            _add(rules, "LOT_MARKER")
            i += 2
            continue
        slash = _SLASH.match(t)
        if slash and not unit:
            unit, first, last_n = slash.group(1), slash.group(2), slash.group(3) or ""
            _add(rules, "UNIT_SLASH_FORM")
            i += 1
            break
        num = _NUMBER.match(t)
        if num and not first:
            first, last_n = num.group(1), num.group(2) or ""
            i += 1
        break
    words = tokens[i:]
    if suburb is None and state is None and postcode is None and not tail_words and not locality_words and words:
        # one chunk, no comma: read `... STATE POSTCODE` off the end, then split the suburb from the street
        w2, st2, pc2 = _split_tail(list(words), rules)
        if st2 or pc2:
            street_w, locality_w = _suburb_split(w2)
            words, st, pc, locality_words = street_w, st2, pc2, locality_w
            _add(rules, "SUBURB_FROM_LINE")
            if locality_w:
                _add(rules, "SUBURB_SPLIT_HEURISTIC")
    words = [w for part in words for w in part.replace("-", " ").split()]
    street_type = ""
    if len(words) >= 2 and canonical_street_type(words[-1]):
        street_type = canonical_street_type(words[-1])
        if words[-1] != street_type:
            _add(rules, "STREET_TYPE_EXPANDED")
        words = words[:-1]
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


def addr_from_gnaf(row: dict) -> Addr:
    """An `Addr` from one flattened G-NAF row (the ADDRESS_DETAIL parts joined with STREET_LOCALITY, LOCALITY and STATE)."""
    def part(prefix, number, suffix) -> str:
        num = "" if number in (None, "") else str(int(number))
        return f"{prefix or ''}{num}{suffix or ''}".upper()

    rules: list[str] = []
    street_words = _prep(row.get("street_name") or "", rules).replace("-", " ").split()
    type_code = (row.get("street_type_code") or "").upper()
    street_type = canonical_street_type(type_code) or type_code
    locality_words = _prep(row.get("locality_name") or "", rules).replace("-", " ").split()
    building = " ".join(_prep(row.get("building_name") or "", rules).replace("-", " ").split())
    lot = part(row.get("lot_number_prefix"), None, "") + (row.get("lot_number") or "").upper() + (row.get("lot_number_suffix") or "").upper()
    return Addr(
        unit=part(row.get("flat_number_prefix"), row.get("flat_number"), row.get("flat_number_suffix")),
        level=part(row.get("level_number_prefix"), row.get("level_number"), row.get("level_number_suffix")),
        building=building,
        lot=lot,
        number_first=part(row.get("number_first_prefix"), row.get("number_first"), row.get("number_first_suffix")),
        number_last=part(row.get("number_last_prefix"), row.get("number_last"), row.get("number_last_suffix")),
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
        _add(rules, "PHONE_E164")
        return "+61" + digits, tuple(rules)
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
    text = text.strip("<> ")
    lowered = text.lower()
    if lowered != text:
        _add(rules, "EMAIL_LOWERCASED")
    if not _EMAIL.match(lowered):
        _add(rules, "EMAIL_INVALID")
        return "", tuple(rules)
    return lowered, tuple(rules)


def normalise_name(raw: str | None) -> str:
    """A display name with the spaces collapsed. It is shown, never matched on."""
    return " ".join((raw or "").split())

