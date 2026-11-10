#!/usr/bin/env python
"""Seeded generator of made-up data in the public G-NAF table layout, plus three messy feeds per agency and a hidden answer key.

    python scripts/seed.py --out out/seed --seed 7 --addresses 4000

Everything is invented: the suburb and street names are built from syllables, the addresses are laid out on a made-up grid near a few city centres,
the people are common given names with invented surnames, emails are on example.com (reserved for documentation), phones are landline numbers in the
(0x) 5550 xxxx style that ACMA reserves for fiction. Nothing here comes from a real address file, a real person, or any licensed dataset.

Output folder:
  gnaf/Standard/<STATE>_<TABLE>_psv.psv      ADDRESS_DETAIL, ADDRESS_DEFAULT_GEOCODE, STREET_LOCALITY, LOCALITY, STATE (pipe delimited, header row)
  gnaf/Authority Code/Authority_Code_<TABLE>_psv.psv   FLAT_TYPE_AUT, STREET_TYPE_AUT, LEVEL_TYPE_AUT
  <agency>/crm_export.csv, listing_feed.csv, sales_history.csv     the messy feeds
  answer_key.csv, truth_properties.csv     the hidden truth; the pipeline never reads these, only `parcelkey evaluate` does
  manifest.json                            seed, counts, the defect class list

The same seed always writes the same bytes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from parcelkey.normalise import Addr  # noqa: E402  (the canonical key function is shared, so a derived property's truth is the pipeline's own key for the clean parts)

STATE_SPEC = {
    "NSW": dict(name="NEW SOUTH WALES", pc=(2000, 2599), box=(-34.2, -33.6, 150.7, 151.1), share=0.275, area="02"),
    "VIC": dict(name="VICTORIA", pc=(3000, 3999), box=(-38.1, -37.6, 144.6, 145.2), share=0.25, area="03"),
    "QLD": dict(name="QUEENSLAND", pc=(4000, 4999), box=(-27.7, -27.2, 152.8, 153.1), share=0.2, area="07"),
    "WA": dict(name="WESTERN AUSTRALIA", pc=(6000, 6797), box=(-32.2, -31.7, 115.8, 116.0), share=0.15, area="08"),
    "SA": dict(name="SOUTH AUSTRALIA", pc=(5000, 5799), box=(-35.1, -34.7, 138.5, 138.7), share=0.125, area="08"),
}
AGENCIES = ("agency-a", "agency-b")
ONSETS = ["Bar", "Bel", "Bren", "Cal", "Dun", "Ever", "Fair", "Glen", "Har", "Kel", "Lin", "Mar", "Nor", "Orm", "Pen", "Quin", "Rav", "Stan", "Tal", "Ul", "Val", "Wex", "Yar", "Zen",
          "Ash", "Brook", "Corr", "Dal", "Elm", "Fen", "Gar", "Hol", "Ing", "Jun", "Kin", "Lor"]
MIDS = ["", "a", "e", "o", "en", "ing", "wel", "mor"]
ENDS = ["ville", "by", "dale", "wick", "ton", "ham", "field", "brook", "mere", "stead", "cliff", "vale", "ridge", "wood", "bury", "port"]
STREET_TYPES = ["STREET", "ROAD", "AVENUE", "DRIVE", "COURT", "PLACE", "CRESCENT", "LANE", "TERRACE", "PARADE", "CLOSE", "BOULEVARD"]
TYPE_WEIGHTS = [30, 18, 10, 10, 8, 6, 5, 4, 3, 3, 2, 1]
ABBREVS = {"STREET": ["St", "Str", "St."], "ROAD": ["Rd", "Rd."], "AVENUE": ["Ave", "Av"], "DRIVE": ["Dr"], "COURT": ["Ct"], "PLACE": ["Pl"], "CRESCENT": ["Cres", "Cr"],
           "LANE": ["Ln"], "TERRACE": ["Tce"], "PARADE": ["Pde"], "CLOSE": ["Cl"], "BOULEVARD": ["Blvd"]}
BUILDING_WORDS = ["Tower", "Heights", "Apartments", "Residences", "Gardens", "Court", "Plaza", "House"]
FIRST_NAMES = [("Katherine", ["Kate", "Katie"]), ("Michael", ["Mike", "Mick"]), ("Elizabeth", ["Liz", "Beth"]), ("Alexander", ["Alex"]), ("Christopher", ["Chris"]), ("Jennifer", ["Jen", "Jenny"]),
               ("William", ["Will", "Bill"]), ("Rebecca", ["Bec", "Becky"]), ("Benjamin", ["Ben"]), ("Samantha", ["Sam"]), ("Nicholas", ["Nick"]), ("Jessica", ["Jess"]),
               ("Matthew", ["Matt"]), ("Daniel", ["Dan"]), ("Stephanie", ["Steph"]), ("Andrew", ["Andy"]), ("Joshua", ["Josh"]), ("Amanda", ["Mandy"]), ("Thomas", ["Tom"]),
               ("Priya", []), ("Wei", []), ("Aroha", []), ("Tane", []), ("Mei", []), ("Sione", []), ("Anh", []), ("Olivia", []), ("Hamish", []), ("Imogen", []), ("Declan", [])]
SURNAME_A = ["Kel", "Mar", "Quin", "Tor", "Vel", "Bren", "Hal", "Dov", "Fen", "Lor", "Sav", "Pel"]
SURNAME_B = ["wick", "son", "ley", "man", "ner", "wood", "stein", "ova", "ini", "ford", "worth", "ard"]
EMAIL_DOMAINS = ["example.com", "example.net", "example.org"]

CLASSES = [
    "clean", "street_abbrev", "mount_saint", "case_punct", "unit_word", "unit_slash", "unit_apt", "unit_u_slash", "unit_flat", "level_suite", "lot_exact", "lot_geocoded", "lot_no_geo", "lot_far",
    "postcode_missing", "postcode_wrong", "postcode_transposed", "number_transposed", "street_typo", "tower_no_unit_parent", "tower_no_unit_noparent", "unit_unknown", "not_in_gnaf", "combined",
]
CONTACT_CLASSES = ["contact_clean", "name_variant", "email_case", "phone_format", "dup_new_email", "dup_no_shared_id", "same_name_different_person", "sales_buyer"]


@dataclass
class Prop:
    """A property in the made-up world: the clean structured address and where it is."""
    kind: str                       # house | tower_flat | tower_parent | block_flat | suite | lot_only | lot_numbered | new_build
    street: "Street"
    number: str = ""
    unit: str = ""
    level: str = ""
    building: str = ""
    lot: str = ""
    lat: float = 0.0
    lon: float = 0.0
    pid: str = ""                   # empty for a property that is not in the G-NAF set
    flat_type: str = ""
    level_type: str = ""
    building_id: str = ""
    row: dict = field(default_factory=dict)

    @property
    def addr(self) -> Addr:
        s = self.street
        return Addr(unit=self.unit, level=self.level, building="", lot=self.lot, number_first=self.number, number_last="", street_name=s.name.upper(),
                    street_type=s.type_code, locality=s.loc.name.upper(), state=s.loc.state, postcode=s.loc.postcode)


@dataclass
class Locality:
    pid: str
    name: str
    state: str
    postcode: str
    lat: float
    lon: float


@dataclass
class Street:
    pid: str
    name: str
    type_code: str
    loc: Locality
    angle: float
    used: set = field(default_factory=set)
    props: list = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.name.title()

    @property
    def type_title(self) -> str:
        return self.type_code.title()


class World:
    """The made-up G-NAF set and the properties in it."""

    def __init__(self, rng: random.Random, total: int) -> None:
        self.rng = rng
        self.localities: list[Locality] = []
        self.streets: list[Street] = []
        self.props: list[Prop] = []
        self.new_builds: list[Prop] = []
        self.counters = {s: 900000000 for s in STATE_SPEC}
        self.names_used: set[str] = set()
        self.build(total)

    # -- naming
    def _word(self, parts_a, parts_b, mids=("",)) -> str:
        r = self.rng
        return r.choice(parts_a) + r.choice(mids) + r.choice(parts_b)

    def _suburb_name(self) -> str:
        r = self.rng
        for _ in range(100):
            name = self._word(ONSETS, ENDS, MIDS)
            roll = r.random()
            if roll < 0.08:
                name = "Mount " + name
            elif roll < 0.14:
                name = "Saint " + name
            if name not in self.names_used:
                self.names_used.add(name)
                return name
        raise RuntimeError("out of suburb names")

    def _street_name(self, loc: Locality, taken: set) -> str:
        r = self.rng
        for _ in range(200):
            name = self._word(ONSETS, ["wick", "field", "brook", "hurst", "lea", "gate", "mont", "dene", "ley", "stone"], MIDS)
            roll = r.random()
            if roll < 0.07:
                name = "Mount " + name
            elif roll < 0.11:
                name = "Saint " + name
            if name not in taken:
                return name
        raise RuntimeError("out of street names")

    def _pid(self, state: str) -> str:
        self.counters[state] += 1
        return f"GA{state}{self.counters[state]:09d}"

    # -- layout
    def _xy(self, street: Street, along: float, side: int) -> tuple[float, float]:
        """Metres along the street and to its side, as a latitude and longitude."""
        loc = street.loc
        ang = street.angle
        dx = along * math.cos(ang) - side * 14.0 * math.sin(ang)
        dy = along * math.sin(ang) + side * 14.0 * math.cos(ang)
        lat = loc.lat + dy / 111320.0
        lon = loc.lon + dx / (111320.0 * math.cos(math.radians(loc.lat)))
        return round(lat, 6), round(lon, 6)

    def build(self, total: int) -> None:
        r = self.rng
        for state, spec in STATE_SPEC.items():
            quota = round(total * spec["share"])
            made = 0
            prev_postcode = None
            lcount = 0
            while made < quota:
                lcount += 1
                lo, hi = spec["pc"]
                postcode = prev_postcode if (prev_postcode and r.random() < 0.15) else f"{r.randint(lo, hi):04d}"
                prev_postcode = postcode
                b = spec["box"]
                loc = Locality(f"{state}L{lcount:05d}", self._suburb_name(), state, postcode, r.uniform(b[0], b[1]), r.uniform(b[2], b[3]))
                self.localities.append(loc)
                taken: set[str] = set()
                for _ in range(r.randint(4, 8)):
                    name = self._street_name(loc, taken)
                    if r.random() < 0.25 and self.streets:
                        donor = r.choice([s for s in self.streets if s.loc.state == state] or self.streets)      # the same street name in two suburbs, a classic matching trap
                        if donor.loc is not loc and (donor.name, donor.type_code) not in {(s.name, s.type_code) for s in self.streets if s.loc is loc}:
                            name, type_code = donor.name, donor.type_code
                        else:
                            type_code = r.choices(STREET_TYPES, TYPE_WEIGHTS)[0]
                    else:
                        type_code = r.choices(STREET_TYPES, TYPE_WEIGHTS)[0]
                    taken.add(name)
                    street = Street(f"{state}{len(self.streets) + 1:06d}", name, type_code, loc, r.uniform(0, math.pi))
                    self.streets.append(street)
                    made += self._populate(street)
                if r.random() < 0.2:
                    # a look-alike street in the same suburb: one letter different, same type (Harwick Street next to Hardwick Street)
                    base = r.choice([s for s in self.streets if s.loc is loc])
                    for _ in range(60):
                        words = base.name.split(" ")
                        w = words[-1]
                        i = r.randint(1, len(w) - 2)
                        words[-1] = w[:i] + r.choice("abcdefghijklmnoprstuvwy".replace(w[i], "")) + w[i + 1:]
                        twin = " ".join(words)
                        if twin not in taken:
                            taken.add(twin)
                            street = Street(f"{state}{len(self.streets) + 1:06d}", twin, base.type_code, loc, r.uniform(0, math.pi))
                            self.streets.append(street)
                            made += self._populate(street)
                            break
        self._ensure_variety()
        self._new_builds()

    def _free_number(self, street: Street, lo: int = 1, hi: int = 120) -> int:
        r = self.rng
        for _ in range(500):
            n = r.randint(lo, hi)
            if n not in street.used:
                street.used.add(n)
                return n
        raise RuntimeError("street full")

    def _add(self, p: Prop, side: int, along: float) -> None:
        r = self.rng
        p.lat, p.lon = self._xy(p.street, along + r.uniform(-3, 3), side)
        p.pid = self._pid(p.street.loc.state)
        self.props.append(p)
        p.street.props.append(p)

    def _populate(self, street: Street) -> int:
        r = self.rng
        count = 0
        for _ in range(r.randint(8, 20)):
            n = self._free_number(street)
            p = Prop("house", street, number=str(n))
            self._add(p, 1 if n % 2 else -1, n * 12.0)
            count += 1
        if r.random() < 0.09:
            count += self._tower(street)
        for _ in range(r.randint(0, 2)):
            if r.random() < 0.35:
                count += self._block(street)
        if r.random() < 0.025:
            count += self._suites(street)
        if r.random() < 0.06:
            for _ in range(r.randint(1, 3)):
                lot = str(r.randint(1, 80))
                p = Prop("lot_only", street, lot=lot)
                n = self._free_number(street, 1, 200)
                self._add(p, 1, n * 6.0)
                count += 1
        if r.random() < 0.06:
            for _ in range(r.randint(1, 2)):
                n = self._free_number(street)
                p = Prop("lot_numbered", street, number=str(n), lot=str(r.randint(1, 80)))
                self._add(p, 1 if n % 2 else -1, n * 12.0)
                count += 1
        return count

    def _building_name(self) -> str:
        return self._word(ONSETS, ["view", "crest", "side", "mont"], ("",)).title() + " " + self.rng.choice(BUILDING_WORDS)

    def _tower(self, street: Street, force_parent: bool = False) -> int:
        r = self.rng
        n = self._free_number(street)
        name = self._building_name()
        bid = f"B-{street.pid}-{n}"
        side, along = (1 if n % 2 else -1), n * 12.0
        count = 0
        levelled = r.random() < 0.5
        levels = r.randint(4, 14)
        per = r.randint(2, 5)
        if force_parent or r.random() < 0.5:
            parent = Prop("tower_parent", street, number=str(n), building=name, building_id=bid)
            self._add(parent, side, along)
            count += 1
        seq = 0
        for lv in range(1, levels + 1):
            for j in range(1, per + 1):
                seq += 1
                unit = str(lv * 100 + j) if levelled else str(seq)
                p = Prop("tower_flat", street, number=str(n), unit=unit, level=str(lv), building=name, building_id=bid, flat_type=r.choice(["UNIT", "APARTMENT"]), level_type="L")
                self._add(p, side, along)
                p.lat, p.lon = self.props[-1].lat, self.props[-1].lon
                count += 1
        return count

    def _block(self, street: Street) -> int:
        r = self.rng
        n = self._free_number(street)
        bid = f"B-{street.pid}-{n}"
        k = r.randint(2, 8)
        letters = r.random() < 0.2
        count = 0
        base = None
        for u in range(1, k + 1):
            unit = f"{u}A" if letters and u % 2 else str(u)
            p = Prop("block_flat", street, number=str(n), unit=unit, building_id=bid, flat_type=r.choice(["UNIT", "FLAT", "VILLA"]))
            self._add(p, 1 if n % 2 else -1, n * 12.0)
            if base is None:
                base = (p.lat, p.lon)
            p.lat, p.lon = base
            count += 1
        return count

    def _suites(self, street: Street) -> int:
        r = self.rng
        n = self._free_number(street)
        name = self._building_name().replace("Apartments", "Offices").replace("Residences", "Offices")
        bid = f"B-{street.pid}-{n}"
        count = 0
        base = None
        for lv in range(1, r.randint(2, 4) + 1):
            for j in range(1, r.randint(2, 4) + 1):
                p = Prop("suite", street, number=str(n), unit=str(lv * 10 + j), level=str(lv), building=name, building_id=bid, flat_type="SUITE", level_type="L")
                self._add(p, 1 if n % 2 else -1, n * 12.0)
                if base is None:
                    base = (p.lat, p.lon)
                p.lat, p.lon = base
                count += 1
        return count

    def _ensure_variety(self) -> None:
        """A small world still gets every kind of address, so the tests and the demo can rely on them."""
        r = self.rng

        def count(kind: str) -> int:
            return sum(1 for p in self.props if p.kind == kind)

        while count("tower_parent") < 2:
            self._tower(r.choice(self.streets), force_parent=True)
        while count("block_flat") < 8:
            self._block(r.choice(self.streets))
        while count("suite") < 4:
            self._suites(r.choice(self.streets))
        while count("lot_only") < 4:
            street = r.choice(self.streets)
            self._add(Prop("lot_only", street, lot=str(r.randint(1, 80))), 1, self._free_number(street, 1, 200) * 6.0)
        while count("lot_numbered") < 4:
            street = r.choice(self.streets)
            n = self._free_number(street)
            self._add(Prop("lot_numbered", street, number=str(n), lot=str(r.randint(1, 80))), 1 if n % 2 else -1, n * 12.0)

    def _new_builds(self) -> None:
        """Properties that exist in the feeds and not in the G-NAF set (a new street number the file does not have yet)."""
        r = self.rng
        houses = [p for p in self.props if p.kind == "house"]
        target = max(6, round(len(houses) * 0.03))
        for street in r.sample(self.streets, min(len(self.streets), target * 2)):
            if len(self.new_builds) >= target:
                break
            n = self._free_number(street, 1, 160)
            p = Prop("new_build", street, number=str(n))
            p.lat, p.lon = self._xy(street, n * 12.0, 1 if n % 2 else -1)
            self.new_builds.append(p)

    # -- G-NAF files
    def table_rows(self) -> dict[str, dict[str, list[dict[str, str]]]]:
        """Rows by state and table, each a dict keyed by the G-NAF column name. A column left out is written empty."""
        out: dict[str, dict[str, list[dict[str, str]]]] = {s: {t: [] for t in HEADERS} for s in STATE_SPEC}
        for state, spec in STATE_SPEC.items():
            out[state]["STATE"].append({"STATE_PID": state, "DATE_CREATED": "2016-02-01", "STATE_NAME": spec["name"], "STATE_ABBREVIATION": state})
        for loc in self.localities:
            out[loc.state]["LOCALITY"].append({"LOCALITY_PID": loc.pid, "DATE_CREATED": "2016-02-01", "LOCALITY_NAME": loc.name.upper(), "PRIMARY_POSTCODE": loc.postcode,
                                               "LOCALITY_CLASS_CODE": "G", "STATE_PID": loc.state})
        for st in self.streets:
            out[st.loc.state]["STREET_LOCALITY"].append({"STREET_LOCALITY_PID": st.pid, "DATE_CREATED": "2016-02-01", "STREET_CLASS_CODE": "C", "STREET_NAME": st.name.upper(),
                                                         "STREET_TYPE_CODE": st.type_code, "LOCALITY_PID": st.loc.pid})
        d0 = date(2016, 2, 1)
        for i, p in enumerate(self.props):
            state = p.street.loc.state
            created = (d0 + timedelta(days=i % 900)).isoformat()
            flat_number = p.unit[:-1] if p.unit and p.unit[-1].isalpha() else p.unit
            flat_suffix = p.unit[-1] if p.unit and p.unit[-1].isalpha() else ""
            out[state]["ADDRESS_DETAIL"].append({
                "ADDRESS_DETAIL_PID": p.pid, "DATE_CREATED": created, "BUILDING_NAME": p.building.upper(), "LOT_NUMBER": p.lot, "FLAT_TYPE_CODE": p.flat_type, "FLAT_NUMBER": flat_number,
                "FLAT_NUMBER_SUFFIX": flat_suffix, "LEVEL_TYPE_CODE": p.level_type, "LEVEL_NUMBER": p.level, "NUMBER_FIRST": p.number, "STREET_LOCALITY_PID": p.street.pid,
                "LOCALITY_PID": p.street.loc.pid, "POSTCODE": p.street.loc.postcode, "CONFIDENCE": "2", "ADDRESS_SITE_PID": p.pid + "S", "LEVEL_GEOCODED_CODE": "7", "PRIMARY_SECONDARY": "P",
            })
            out[state]["ADDRESS_DEFAULT_GEOCODE"].append({"ADDRESS_DEFAULT_GEOCODE_PID": p.pid + "G", "DATE_CREATED": created, "ADDRESS_DETAIL_PID": p.pid, "GEOCODE_TYPE_CODE": "PC",
                                                          "LONGITUDE": f"{p.lon:.8f}", "LATITUDE": f"{p.lat:.8f}"})
        return out


HEADERS = {
    "ADDRESS_DETAIL": ["ADDRESS_DETAIL_PID", "DATE_CREATED", "DATE_LAST_MODIFIED", "DATE_RETIRED", "BUILDING_NAME", "LOT_NUMBER_PREFIX", "LOT_NUMBER", "LOT_NUMBER_SUFFIX", "FLAT_TYPE_CODE",
                       "FLAT_NUMBER_PREFIX", "FLAT_NUMBER", "FLAT_NUMBER_SUFFIX", "LEVEL_TYPE_CODE", "LEVEL_NUMBER_PREFIX", "LEVEL_NUMBER", "LEVEL_NUMBER_SUFFIX", "NUMBER_FIRST_PREFIX",
                       "NUMBER_FIRST", "NUMBER_FIRST_SUFFIX", "NUMBER_LAST_PREFIX", "NUMBER_LAST", "NUMBER_LAST_SUFFIX", "STREET_LOCALITY_PID", "LOCATION_DESCRIPTION", "LOCALITY_PID",
                       "ALIAS_PRINCIPAL", "POSTCODE", "PRIVATE_STREET", "LEGAL_PARCEL_ID", "CONFIDENCE", "ADDRESS_SITE_PID", "LEVEL_GEOCODED_CODE", "PROPERTY_PID", "GNAF_PROPERTY_PID",
                       "PRIMARY_SECONDARY"],
    "ADDRESS_DEFAULT_GEOCODE": ["ADDRESS_DEFAULT_GEOCODE_PID", "DATE_CREATED", "DATE_RETIRED", "ADDRESS_DETAIL_PID", "GEOCODE_TYPE_CODE", "LONGITUDE", "LATITUDE"],
    "STREET_LOCALITY": ["STREET_LOCALITY_PID", "DATE_CREATED", "DATE_RETIRED", "STREET_CLASS_CODE", "STREET_NAME", "STREET_TYPE_CODE", "STREET_SUFFIX_CODE", "LOCALITY_PID",
                        "GNAF_STREET_PID", "GNAF_STREET_CONFIDENCE", "GNAF_RELIABILITY_CODE"],
    "LOCALITY": ["LOCALITY_PID", "DATE_CREATED", "DATE_RETIRED", "LOCALITY_NAME", "PRIMARY_POSTCODE", "LOCALITY_CLASS_CODE", "STATE_PID", "GNAF_LOCALITY_PID", "GNAF_RELIABILITY_CODE"],
    "STATE": ["STATE_PID", "DATE_CREATED", "DATE_RETIRED", "STATE_NAME", "STATE_ABBREVIATION"],
}
AUTHORITY = {
    "FLAT_TYPE_AUT": [("UNIT", "UNIT"), ("APARTMENT", "APARTMENT"), ("FLAT", "FLAT"), ("SUITE", "SUITE"), ("VILLA", "VILLA")],
    "STREET_TYPE_AUT": [(t, t) for t in STREET_TYPES],
    "LEVEL_TYPE_AUT": [("L", "LEVEL")],
}


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Messy feeds
# ---------------------------------------------------------------------------------------------------------------------------------------------------

QUARANTINE_CLASSES = {"lot_no_geo", "lot_far", "number_transposed", "tower_no_unit_noparent", "unit_unknown"}


def _swap_digits(s: str, rng: random.Random) -> str | None:
    """Swap two neighbouring different digits; None when no swap changes the text."""
    options = [i for i in range(len(s) - 1) if s[i].isdigit() and s[i + 1].isdigit() and s[i] != s[i + 1]]
    if not options:
        return None
    i = rng.choice(options)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


def _typo(name: str, rng: random.Random) -> str:
    """One mistake in the last word of a name: a dropped, a swapped or a replaced letter."""
    words = name.split(" ")
    w = words[-1]
    i = rng.randint(1, len(w) - 2)
    op = rng.choice(["drop", "swap", "sub"])
    if op == "drop":
        w = w[:i] + w[i + 1:]
    elif op == "swap" and w[i] != w[i + 1]:
        w = w[:i] + w[i + 1] + w[i] + w[i + 2:]
    else:
        w = w[:i] + rng.choice("aeiou".replace(w[i].lower(), "")) + w[i + 1:]
    words[-1] = w
    return " ".join(words)


def _phone_forms(area: str, num8: str) -> list[str]:
    a = area[1:]
    return [f"{area} {num8[:4]} {num8[4:]}", f"({area}) {num8[:4]} {num8[4:]}", f"+61 {a} {num8[:4]} {num8[4:]}", f"{area}{num8}", f"{area}-{num8[:4]}-{num8[4:]}", f"+61{a}{num8}"]


@dataclass
class Person:
    pid: str
    first: str
    nicks: list
    last: str
    email: str
    area: str
    num8: str

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"


class AgencyFeeds:
    """One agency's three feeds and its share of the answer key."""

    def __init__(self, rng: random.Random, world: World, agency: str, per_agency: int) -> None:
        self.rng, self.world, self.agency = rng, world, agency
        self.rows: dict[str, list[dict]] = {"crm": [], "listing": [], "sales": []}
        self.key: list[dict] = []
        self.people: list[Person] = []
        self.person_seq = 0
        self.used_phones: set = set()
        self.n_crm, self.n_listing, self.n_sales = round(per_agency * 0.40), round(per_agency * 0.27), round(per_agency * 0.33)
        by_kind: dict[str, list[Prop]] = {}
        for p in world.props:
            by_kind.setdefault(p.kind, []).append(p)
        self.by_kind = by_kind
        self.buildings: dict[str, list[Prop]] = {}
        for p in world.props:
            if p.building_id and p.kind != "tower_parent":
                self.buildings.setdefault(p.building_id, []).append(p)
        self.tower_ids = sorted(b for b, m in self.buildings.items() if m[0].kind == "tower_flat")
        self.block_ids = sorted(b for b, m in self.buildings.items() if m[0].kind == "block_flat")
        self.unit_building_ids = self.tower_ids + self.block_ids
        self.parents = {p.building_id: p for p in world.props if p.kind == "tower_parent"}
        self.new_builds = list(world.new_builds)
        self.seq = {"crm": 0, "listing": 0, "sales": 0}
        self.pending_new_build: list[Prop] = []

    # -- people
    def new_person(self, name_from: Person | None = None) -> Person:
        r = self.rng
        self.person_seq += 1
        first, nicks = (name_from.first, name_from.nicks) if name_from else r.choice(FIRST_NAMES)
        last = name_from.last if name_from else r.choice(SURNAME_A) + r.choice(SURNAME_B)
        email = f"{first.lower()}.{last.lower()}{self.person_seq}@{r.choice(EMAIL_DOMAINS)}"
        area = r.choice(["02", "03", "07", "08"])
        while True:
            num8 = "5550" + f"{r.randint(0, 9999):04d}"
            if (area, num8) not in self.used_phones:
                self.used_phones.add((area, num8))
                break
        person = Person(f"{self.agency}-P{self.person_seq:05d}", first, nicks, last, email, area, num8)
        self.people.append(person)
        return person

    # -- address rendering
    def _street_text(self, p: Prop, rng: random.Random, abbrev: bool = False, mt: bool = False, name: str | None = None) -> str:
        name = name if name is not None else p.street.title
        if mt:
            name = name.replace("Mount ", "Mt ").replace("Saint ", "St ")
        typ = rng.choice(ABBREVS[p.street.type_code]) if abbrev else p.street.type_title
        return f"{name} {typ}"

    def _front(self, p: Prop, form: str, number: str, unit: str) -> str:
        return {
            "word": f"Unit {unit}, {number}", "slash": f"{unit}/{number}", "apt": f"Apt {unit} {number}", "u_slash": f"U{unit}/{number}", "flat": f"Flat {unit}, {number}",
            "suite": f"Level {p.level} Suite {unit}, {number}", "plain": f"{number}", "lot": f"Lot {p.lot}",
        }[form]

    def _messy(self, s: str, rng: random.Random) -> str:
        v = rng.choice(["upper", "lower", "commas", "dots", "spaces", "trail"])
        if v == "upper":
            return s.upper()
        if v == "lower":
            return s.lower()
        if v == "commas":
            return s.replace(", ", ",").replace(" ", "  ", 1)
        if v == "dots":
            return s.replace(",", ".") + "."
        if v == "spaces":
            return "  " + s.replace(" ", "  ") + " "
        return s + ","

    def _render(self, p: Prop, cls: str, unit_override: str | None = None) -> dict:
        """The address fields a feed would carry for this property with this defect class."""
        r, w = self.rng, self.world
        st = p.street
        suburb, state, postcode = st.loc.name, st.loc.state, st.loc.postcode
        form = "plain"
        abbrev = mt = False
        number, unit = p.number, (unit_override if unit_override is not None else p.unit)
        name = None
        if p.kind in ("tower_flat", "block_flat", "suite"):
            form = {"unit_word": "word", "unit_slash": "slash", "unit_apt": "apt", "unit_u_slash": "u_slash", "unit_flat": "flat", "level_suite": "suite"}.get(cls) \
                or ("plain" if cls in ("tower_no_unit_parent", "tower_no_unit_noparent") else r.choice(["word", "slash", "apt", "u_slash", "flat"]))
            if form == "suite" and p.kind != "suite":
                form = "word"
        if cls in ("lot_exact", "lot_geocoded", "lot_no_geo", "lot_far"):
            form = "lot"
        if cls == "street_abbrev":
            abbrev = True
        if cls == "mount_saint":
            mt, abbrev = True, r.random() < 0.5
        if cls == "combined":
            abbrev = True
            mt = "Mount " in st.title or "Saint " in st.title
        if cls == "postcode_missing":
            postcode = ""
        if cls == "postcode_wrong":
            others = sorted({loc.postcode for loc in w.localities if loc.state == state and loc.postcode != postcode})
            postcode = r.choice(others) if others else f"{int(postcode) + 1:04d}"
        if cls == "postcode_transposed":
            postcode = _swap_digits(postcode, r) or postcode
        if cls == "number_transposed":
            number = _swap_digits(number, r) or number
        if cls == "street_typo":
            name = _typo(st.title, r)
        if cls == "combined" and r.random() < 0.5:
            postcode = ""
        street = self._street_text(p, r, abbrev, mt, name=name)
        line = f"{self._front(p, form, number, unit)} {street}"
        if p.building and p.kind in ("tower_flat", "suite") and form != "plain" and r.random() < 0.12:
            line = f"{p.building}, {line}"
        out = {"line": line, "suburb": suburb, "state": state, "postcode": postcode}
        if cls in ("case_punct", "combined"):
            out["line"] = self._messy(out["line"], r)
            out["suburb"] = r.choice([suburb, suburb.upper(), suburb.lower()])
            out["state"] = r.choice([state, state.lower()])
        if cls in ("mount_saint", "combined"):
            out["suburb"] = out["suburb"].replace("Mount ", "Mt ").replace("Saint ", "St ")
        return out

    # -- one record
    def _coords(self, p: Prop, cls: str) -> tuple[str, str]:
        r = self.rng
        if cls == "lot_far":
            off = r.uniform(300, 600) / 111320.0
            return f"{p.lat + off:.6f}", f"{p.lon:.6f}"
        if r.random() < 0.15 and cls != "lot_geocoded":
            return "", ""
        j = 8 / 111320.0
        return f"{p.lat + r.uniform(-j, j):.6f}", f"{p.lon + r.uniform(-j, j):.6f}"

    def _key(self, source: str, ref: str, cls: str, p: Prop, truth: Prop | None, expected: str, *, person: str = "", contact_class: str = "", sibling: int = 0) -> None:
        truth_pid = truth.pid if truth is not None and truth.pid else ""
        truth_canon = truth.addr.canon_key if truth is not None and not truth.pid else ""
        self.key.append({
            "agency_id": self.agency, "record_id": f"{source}:{ref}", "class": cls, "expected": expected, "truth_pid": truth_pid, "truth_canon_key": truth_canon,
            "kind": p.kind, "building_id": p.building_id, "unit": p.unit, "sibling": sibling, "person_id": person, "contact_class": contact_class,
        })

    # -- draws
    def _unit_class(self) -> str:
        weights = [("unit_word", 18), ("unit_slash", 18), ("unit_apt", 12), ("unit_u_slash", 12), ("unit_flat", 12), ("street_abbrev", 6), ("postcode_missing", 5), ("postcode_wrong", 4),
                   ("combined", 10), ("case_punct", 3)]
        return self.rng.choices([c for c, _ in weights], [x for _, x in weights])[0]

    def _draw(self, source: str) -> list[tuple[str, Prop, Prop | None, int, str | None]]:
        """One draw can give a cluster (flats of one building). Each item: (class, property to render, truth property or None, sibling flag, unit override)."""
        r = self.rng
        if self.pending_new_build:
            nb = self.pending_new_build.pop()
            return [("not_in_gnaf", nb, nb, 0, None)]
        roll = r.random() * 100
        if roll < 52:
            p = r.choice(self.by_kind["house"])
            weights = [("clean", 28), ("street_abbrev", 20), ("case_punct", 10), ("postcode_missing", 7), ("postcode_wrong", 6), ("postcode_transposed", 4), ("number_transposed", 4),
                       ("street_typo", 6), ("combined", 8)]
            if "Mount " in p.street.title or "Saint " in p.street.title or "Mount " in p.street.loc.name or "Saint " in p.street.loc.name:
                weights.append(("mount_saint", 12))
            cls = r.choices([c for c, _ in weights], [x for _, x in weights])[0]
            if cls == "number_transposed":
                swapped = _swap_digits(p.number, r)
                if swapped is None or int(swapped) in p.street.used:
                    cls = "clean"
            if cls == "street_typo" and len(p.street.name.split(" ")[-1]) < 6:
                cls = "street_abbrev"
            return [(cls, p, p, 0, None)]
        if roll < 64:
            flats = self.buildings[r.choice(self.tower_ids)]
            picks = r.sample(flats, min(len(flats), r.choice([1, 1, 2, 3])))
            return [(self._unit_class(), p, p, 1 if len(picks) > 1 else 0, None) for p in picks]
        if roll < 72:
            flats = self.buildings[r.choice(self.block_ids)]
            picks = r.sample(flats, min(len(flats), r.choice([1, 1, 2])))
            return [(self._unit_class(), p, p, 1 if len(picks) > 1 else 0, None) for p in picks]
        if roll < 75:
            p = r.choice(self.by_kind["suite"])
            cls = r.choices(["level_suite", "unit_word", "unit_slash", "combined", "postcode_missing", "street_abbrev"], [40, 20, 10, 15, 8, 7])[0]
            return [(cls, p, p, 0, None)]
        if roll < 77.5:
            bid = r.choice(self.unit_building_ids)
            members = self.buildings[bid]
            if bid in self.parents:
                return [("tower_no_unit_parent", members[0], self.parents[bid], 0, None)]
            return [("tower_no_unit_noparent", members[0], None, 0, None)]
        if roll < 79:
            members = self.buildings[r.choice(self.unit_building_ids)]
            nums = [int("".join(ch for ch in x.unit if ch.isdigit())) for x in members]
            return [("unit_unknown", members[0], None, 0, str(max(nums) + 50))]
        if roll < 80.5 and self.by_kind.get("lot_only"):
            p = r.choice(self.by_kind["lot_only"])
            return [("lot_exact", p, p, 0, None)]
        if roll < 83 and self.by_kind.get("lot_numbered"):
            p = r.choice(self.by_kind["lot_numbered"])
            if source == "listing":
                cls = r.choices(["lot_geocoded", "lot_far"], [70, 30])[0]
            else:
                cls = "lot_no_geo"
            return [(cls, p, p if cls == "lot_geocoded" else None, 0, None)]
        if roll < 87 and self.new_builds:
            nb = r.choice(self.new_builds)
            if r.random() < 0.7:
                self.pending_new_build.extend([nb] * r.randint(1, 2))
            return [("not_in_gnaf", nb, nb, 0, None)]
        p = r.choice(self.by_kind["house"])
        return [("clean", p, p, 0, None)]

    def _prepare(self, source: str) -> list[tuple]:
        """(class, rendered address, property, expected, truth property, sibling flag) for every row of one source, in feed order."""
        n = {"crm": self.n_crm, "listing": self.n_listing, "sales": self.n_sales}[source]
        out: list[tuple] = []
        while len(out) < n:
            for cls, p, truth, sib, unit_override in self._draw(source):
                rendered = self._render(p, cls, unit_override)
                out.append((cls, rendered, p, "quarantine" if cls in QUARANTINE_CLASSES else "link", truth, sib))
        return out[:n]

    # -- contacts
    def _name_variant(self, person: Person) -> str:
        r = self.rng
        v = r.choice(["nick", "initial", "reversed", "nick"])
        if v == "nick" and person.nicks:
            return f"{r.choice(person.nicks)} {person.last}"
        if v == "reversed":
            return f"{person.last}, {person.first}"
        return f"{person.first[0]}. {person.last}"

    def _email_variant(self, email: str) -> str:
        return self.rng.choice([email.upper(), f"  {email}", f"{email}  ", email.capitalize(), email.title()])

    def _phone(self, person: Person) -> str:
        return self.rng.choice(_phone_forms(person.area, person.num8))

    def _contact_mentions(self) -> list[tuple[Person, str, dict]]:
        """(person, contact class, fields) for every CRM row. A person's first mention comes before its duplicates."""
        r = self.rng
        out: list[tuple[Person, str, dict]] = []
        while len(out) < self.n_crm:
            person = self.new_person()
            fields = {"name": person.full, "email": person.email, "phone": self._phone(person)}
            if r.random() < 0.04:
                fields["email"] = ""
            if r.random() < 0.03:
                fields["phone"] = "tbc"
            out.append((person, "contact_clean", fields))
            roll = r.random()
            if roll < 0.12:
                out.append((person, "name_variant", {"name": self._name_variant(person), "email": person.email, "phone": self._phone(person)}))
            elif roll < 0.20:
                out.append((person, "email_case", {"name": person.full, "email": self._email_variant(person.email), "phone": ""}))
            elif roll < 0.27:
                out.append((person, "phone_format", {"name": person.full, "email": "", "phone": self._phone(person)}))
            elif roll < 0.33:
                out.append((person, "dup_new_email", {"name": person.full, "email": person.email.replace("@", ".work@"), "phone": self._phone(person)}))
            elif roll < 0.37:
                out.append((person, "dup_no_shared_id", {"name": person.full, "email": person.email.replace("@", ".home@"), "phone": ""}))
            elif roll < 0.40:
                other = self.new_person(name_from=person)
                out.append((other, "same_name_different_person", {"name": other.full, "email": other.email, "phone": self._phone(other)}))
        return out[: self.n_crm]

    def _date(self) -> str:
        return (date(2024, 1, 1) + timedelta(days=self.rng.randint(0, 900))).isoformat()

    # -- build
    def build(self) -> None:
        r = self.rng
        crm_cases = self._prepare("crm")
        for (cls, rendered, p, expected, truth, sib), (person, cclass, fields) in zip(crm_cases, self._contact_mentions()):
            self.seq["crm"] += 1
            ref = f"C{self.seq['crm']:05d}"
            self.rows["crm"].append({"contact_ref": ref, "full_name": fields["name"], "email": fields["email"], "phone": fields["phone"], "address": rendered["line"],
                                     "suburb": rendered["suburb"], "state": rendered["state"], "postcode": rendered["postcode"], "created_on": self._date()})
            self._key("crm", ref, cls, p, truth, expected, person=person.pid, contact_class=cclass, sibling=sib)
        for cls, rendered, p, expected, truth, sib in self._prepare("listing"):
            self.seq["listing"] += 1
            ref = f"L{self.seq['listing']:05d}"
            lat, lon = self._coords(p, cls)
            self.rows["listing"].append({"listing_ref": ref, "campaign_id": f"CMP-{r.randint(1, 40):03d}", "address": rendered["line"], "suburb": rendered["suburb"],
                                         "state": rendered["state"], "postcode": rendered["postcode"], "latitude": lat, "longitude": lon, "list_price": r.randrange(350, 2400, 5) * 1000,
                                         "listed_on": self._date(), "status": r.choice(["active", "sold", "withdrawn"])})
            self._key("listing", ref, cls, p, truth, expected, sibling=sib)
        for cls, rendered, p, expected, truth, sib in self._prepare("sales"):
            self.seq["sales"] += 1
            ref = f"S{self.seq['sales']:05d}"
            tail = " ".join(x for x in (rendered["suburb"], rendered["state"], rendered["postcode"]) if x)
            buyer = {"name": "", "email": "", "phone": ""}
            person_id, cclass = "", ""
            roll = r.random()
            if roll < 0.40 and self.people:
                person = r.choice(self.people)
                buyer = {"name": person.full if r.random() < 0.7 else self._name_variant(person),
                         "email": self._email_variant(person.email) if r.random() < 0.8 else "", "phone": self._phone(person) if r.random() < 0.6 else ""}
                if not buyer["email"] and not buyer["phone"]:
                    buyer["email"] = person.email
                person_id, cclass = person.pid, "sales_buyer"
            elif roll < 0.85:
                person = self.new_person()
                buyer = {"name": person.full, "email": person.email, "phone": self._phone(person)}
                person_id, cclass = person.pid, "contact_clean"
            self.rows["sales"].append({"sale_ref": ref, "address_full": f"{rendered['line']}, {tail}", "sold_on": self._date(), "sale_price": r.randrange(350, 2400, 5) * 1000,
                                       "buyer_name": buyer["name"], "buyer_email": buyer["email"], "buyer_phone": buyer["phone"]})
            self._key("sales", ref, cls, p, truth, expected, person=person_id, contact_class=cclass, sibling=sib)


FEED_FILES = {"crm": "crm_export.csv", "listing": "listing_feed.csv", "sales": "sales_history.csv"}
KEY_COLUMNS = ["agency_id", "record_id", "class", "expected", "truth_pid", "truth_canon_key", "kind", "building_id", "unit", "sibling", "person_id", "contact_class"]


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_gnaf(world: World, out: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state, tables in world.table_rows().items():
        for table, rows in tables.items():
            path = out / "gnaf" / "Standard" / f"{state}_{table}_psv.psv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write("|".join(HEADERS[table]) + "\n")
                for row in rows:
                    f.write("|".join(str(row.get(c, "")) for c in HEADERS[table]) + "\n")
            counts[table] = counts.get(table, 0) + len(rows)
    for table, rows in AUTHORITY.items():
        path = out / "gnaf" / "Authority Code" / f"Authority_Code_{table}_psv.psv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write("CODE|NAME|DESCRIPTION\n")
            for code, name in rows:
                f.write(f"{code}|{name}|\n")
    return counts


FEED_COLUMNS = {
    "crm": ["contact_ref", "full_name", "email", "phone", "address", "suburb", "state", "postcode", "created_on"],
    "listing": ["listing_ref", "campaign_id", "address", "suburb", "state", "postcode", "latitude", "longitude", "list_price", "listed_on", "status"],
    "sales": ["sale_ref", "address_full", "sold_on", "sale_price", "buyer_name", "buyer_email", "buyer_phone"],
}


def generate(out: Path, seed: int = 7, addresses: int = 4000, per_agency: int | None = None) -> dict:
    """Write the whole made-up world under `out`. Returns the manifest (also written to manifest.json)."""
    out = Path(out)
    world = World(random.Random(seed), addresses)
    counts = write_gnaf(world, out)
    per_agency = per_agency if per_agency is not None else round(len(world.props) * 0.58)
    key: list[dict] = []
    manifest_agencies = {}
    for agency in AGENCIES:
        feeds = AgencyFeeds(random.Random(f"{seed}:{agency}"), world, agency, per_agency)
        feeds.build()
        for source, name in FEED_FILES.items():
            _write_csv(out / agency / name, feeds.rows[source], FEED_COLUMNS[source])
        key.extend(feeds.key)
        manifest_agencies[agency] = {src: len(rows) for src, rows in feeds.rows.items()}
    _write_csv(out / "answer_key.csv", key, KEY_COLUMNS)
    truth = [{"pid": p.pid, "building_id": p.building_id, "unit": p.unit, "kind": p.kind, "canon_key": ""} for p in world.props]
    truth += [{"pid": "", "building_id": "", "unit": "", "kind": "new_build", "canon_key": p.addr.canon_key} for p in world.new_builds]
    _write_csv(out / "truth_properties.csv", truth, ["pid", "building_id", "unit", "kind", "canon_key"])
    manifest = {"seed": seed, "addresses_requested": addresses, "gnaf_rows": counts, "properties_in_gnaf": len(world.props), "new_builds_not_in_gnaf": len(world.new_builds),
                "agencies": manifest_agencies, "classes": CLASSES, "contact_classes": CONTACT_CLASSES, "answer_key_rows": len(key)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="out/seed", help="folder to write (created if missing)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--addresses", type=int, default=4000, help="about how many addresses in the G-NAF layout set")
    ap.add_argument("--per-agency", type=int, default=None, help="source rows per agency across the three feeds (default 58 percent of the addresses)")
    a = ap.parse_args(argv)
    manifest = generate(Path(a.out), a.seed, a.addresses, a.per_agency)
    print(json.dumps({k: manifest[k] for k in ("seed", "properties_in_gnaf", "new_builds_not_in_gnaf", "agencies", "answer_key_rows")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
