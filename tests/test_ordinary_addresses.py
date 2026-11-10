"""Everyday addresses: the shapes people and feeds really write, and what the normaliser made of each BEFORE the second review's fixes (F4 to F9).

The expected parts and the rule ids were taken from the build at aa03327 (the first commit of this lab) and checked equal to the build after the fixes, so a change that makes an ordinary
address read differently, or fire a rule it did not fire before, fails here. Nothing in this table is a real person's address; the streets and suburbs are common names or made up.
"""
from __future__ import annotations

import pytest

from parcelkey.normalise import normalise_address

# (the arguments of normalise_address, (unit, level, building, lot, number, street name, street type, locality, state, postcode), the rule ids that fired)
ORDINARY = [
    (('12 Smith St', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('5/12 Smith Street', 'Fakeville', 'NSW', '2000'), ('5', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'UNIT_SLASH_FORM')),
    (('Unit 4, 88 George St', 'Sydney', 'NSW', '2000'), ('4', '', '', '', '88', 'GEORGE', 'STREET', 'SYDNEY', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_WORD')),
    (('100 Collins St', 'Melbourne', 'VIC', '3000'), ('', '', '', '', '100', 'COLLINS', 'STREET', 'MELBOURNE', 'VIC', '3000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('27 St Kilda Rd', 'St Kilda', 'VIC', '3182'), ('', '', '', '', '27', 'SAINT KILDA', 'ROAD', 'SAINT KILDA', 'VIC', '3182'), ('CASE_FOLDED', 'SAINT_EXPANDED', 'STREET_TYPE_EXPANDED')),
    (('3 Mt Eliza Way', 'Mount Eliza', 'VIC', '3930'), ('', '', '', '', '3', 'MOUNT ELIZA', 'WAY', 'MOUNT ELIZA', 'VIC', '3930'), ('CASE_FOLDED', 'MOUNT_EXPANDED')),
    (('14 Box Hill Rd', 'Box Hill', 'VIC', '3128'), ('', '', '', '', '14', 'BOX HILL', 'ROAD', 'BOX HILL', 'VIC', '3128'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('9 Ocean Beach Rd', 'Bondi', 'NSW', '2026'), ('', '', '', '', '9', 'OCEAN BEACH', 'ROAD', 'BONDI', 'NSW', '2026'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('22 Park Ave', 'Sunnybank Hills', 'QLD', '4109'), ('', '', '', '', '22', 'PARK', 'AVENUE', 'SUNNYBANK HILLS', 'QLD', '4109'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('Lot 7 Smith Rd', 'Fakeville', 'NSW', '2000'), ('', '', '', '7', '', 'SMITH', 'ROAD', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'LOT_MARKER', 'STREET_TYPE_EXPANDED')),
    (('45-47 Queen St', 'Brisbane City', 'QLD', '4000'), ('', '', '', '', '45-47', 'QUEEN', 'STREET', 'BRISBANE CITY', 'QLD', '4000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('Level 3, 200 Adelaide Tce', 'Perth', 'WA', '6000'), ('', '3', '', '', '200', 'ADELAIDE', 'TERRACE', 'PERTH', 'WA', '6000'), ('CASE_FOLDED', 'LEVEL_MARKER', 'STREET_TYPE_EXPANDED')),
    (('Apt 12, 30 Marine Pde', 'Manly', 'NSW', '2095'), ('12', '', '', '', '30', 'MARINE', 'PARADE', 'MANLY', 'NSW', '2095'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_WORD')),
    (('U5/12 Smith St', 'Fakeville', 'NSW', '2000'), ('5', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_GLUED', 'UNIT_SLASH_FORM')),
    (("1 O'Connell St", 'North Adelaide', 'SA', '5006'), ('', '', '', '', '1', 'OCONNELL', 'STREET', 'NORTH ADELAIDE', 'SA', '5006'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('7 Blue-Hill Rd', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '7', 'BLUE HILL', 'ROAD', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('12A Smith St', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '12A', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('2 The Esplanade', 'Cairns', 'QLD', '4870'), ('', '', '', '', '2', 'THE', 'ESPLANADE', 'CAIRNS', 'QLD', '4870'), ('CASE_FOLDED',)),
    (('88 Surfers Paradise Blvd', 'Surfers Paradise', 'QLD', '4217'), ('', '', '', '', '88', 'SURFERS PARADISE', 'BOULEVARD', 'SURFERS PARADISE', 'QLD', '4217'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('15 Palm Beach Rd', 'Palm Beach', 'NSW', '2108'), ('', '', '', '', '15', 'PALM BEACH', 'ROAD', 'PALM BEACH', 'NSW', '2108'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('3 Hill Top Cres', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '3', 'HILL TOP', 'CRESCENT', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('Shop 5, 120 Queen St', 'Melbourne', 'VIC', '3000'), ('5', '', '', '', '120', 'QUEEN', 'STREET', 'MELBOURNE', 'VIC', '3000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_WORD')),
    (('Suite 7, Level 3, 12 Smith St', 'Fakeville', 'NSW', '2000'), ('7', '3', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'LEVEL_MARKER', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_WORD')),
    (('Flat 2, 8 Station Rd', 'Epping', 'NSW', '2121'), ('2', '', '', '', '8', 'STATION', 'ROAD', 'EPPING', 'NSW', '2121'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_WORD')),
    (('1 Smith Lane', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '1', 'SMITH', 'LANE', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED',)),
    (('10 Smith Place', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '10', 'SMITH', 'PLACE', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED',)),
    (('Skyline Tower, Unit 5, 12 Smith St', 'Fakeville', 'NSW', '2000'), ('5', '', 'SKYLINE TOWER', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('BUILDING_NAME_PREFIX', 'CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'UNIT_MARKER_WORD')),
    (('12 Smith St, Bondi Beach', None, None, None), ('', '', '', '', '12', 'SMITH', 'STREET', 'BONDI BEACH', '', ''), ('CASE_FOLDED', 'POSTCODE_MISSING', 'STATE_MISSING', 'STREET_TYPE_EXPANDED', 'SUBURB_FROM_LINE')),
    (('5/12 Smith St, Fakeville NSW 2000', None, None, None), ('5', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'SUBURB_FROM_LINE', 'UNIT_SLASH_FORM')),
    (('12 Smith St Fakeville NSW 2000', None, None, None), ('', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'SUBURB_FROM_LINE', 'SUBURB_SPLIT_HEURISTIC')),
    (('Unit 3 12 Smith Street', 'Fakeville', 'NSW', '2000'), ('3', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'UNIT_MARKER_WORD')),
    (('12 Mount Pleasant Rd', 'Fakeville', 'NSW', '2000'), ('', '', '', '', '12', 'MOUNT PLEASANT', 'ROAD', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('12 Smith St', 'Fakeville', 'NSW', ''), ('', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', ''), ('CASE_FOLDED', 'POSTCODE_MISSING', 'STREET_TYPE_EXPANDED')),
    (('12 SMITH ST.', 'FAKEVILLE', 'nsw', '2000'), ('', '', '', '', '12', 'SMITH', 'STREET', 'FAKEVILLE', 'NSW', '2000'), ('CASE_FOLDED', 'PUNCT_STRIPPED', 'STREET_TYPE_EXPANDED')),
    (('12 Smith St', 'Darwin', 'NT', '0800'), ('', '', '', '', '12', 'SMITH', 'STREET', 'DARWIN', 'NT', '0800'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('12 Smith St', 'Canberra', 'ACT', '2600'), ('', '', '', '', '12', 'SMITH', 'STREET', 'CANBERRA', 'ACT', '2600'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('5 Grove Rd', 'Hobart', 'TAS', '7000'), ('', '', '', '', '5', 'GROVE', 'ROAD', 'HOBART', 'TAS', '7000'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED')),
    (('14 Beach St, Port Melbourne VIC 3207', None, None, None), ('', '', '', '', '14', 'BEACH', 'STREET', 'PORT MELBOURNE', 'VIC', '3207'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'SUBURB_FROM_LINE')),
    (('2 Hill St, Surry Hills NSW 2010', None, None, None), ('', '', '', '', '2', 'HILL', 'STREET', 'SURRY HILLS', 'NSW', '2010'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'SUBURB_FROM_LINE')),
    (('8 Park Rd, Park Orchards VIC 3114', None, None, None), ('', '', '', '', '8', 'PARK', 'ROAD', 'PARK ORCHARDS', 'VIC', '3114'), ('CASE_FOLDED', 'STREET_TYPE_EXPANDED', 'SUBURB_FROM_LINE')),
]


@pytest.mark.parametrize("args,parts,rules", ORDINARY, ids=[a[0] for a, _, _ in ORDINARY])
def test_an_ordinary_address_reads_as_it_did_before_the_second_review(args, parts, rules):
    a = normalise_address(*args)
    assert (a.unit, a.level, a.building, a.lot, a.number_key, a.street_name, a.street_type, a.locality, a.state, a.postcode) == parts
    assert tuple(sorted(a.rules)) == rules
    assert "POSTAL_ADDRESS" not in a.rules


def test_there_are_at_least_25_ordinary_samples_and_no_two_are_the_same():
    assert len(ORDINARY) >= 25 and len({repr(a) for a, _, _ in ORDINARY}) == len(ORDINARY)


# ---- a generated crowd of everyday addresses: none refused, none flagged, each read as written ----------------------------------------------------

NAMES = ["Smith", "George", "Queen", "King", "Park", "Beach", "Hill", "High", "Church", "Station", "Main", "Ocean", "Victoria", "Albert", "Collins", "Elizabeth", "Bridge", "Marine", "Lake", "River",
         "Forest", "Garden", "Sunset", "Harbour", "Bay", "Valley", "Rose", "Palm", "Orchard", "Grove", "Market", "North", "South", "East", "West", "Boundary", "Gold", "Silver"]
TYPES = {"St": "STREET", "Street": "STREET", "Rd": "ROAD", "Road": "ROAD", "Ave": "AVENUE", "Avenue": "AVENUE", "Dr": "DRIVE", "Drive": "DRIVE", "Ct": "COURT", "Court": "COURT", "Pl": "PLACE",
         "Place": "PLACE", "Cres": "CRESCENT", "Crescent": "CRESCENT", "Ln": "LANE", "Lane": "LANE", "Tce": "TERRACE", "Terrace": "TERRACE", "Pde": "PARADE", "Parade": "PARADE", "Cl": "CLOSE",
         "Close": "CLOSE", "Blvd": "BOULEVARD", "Hwy": "HIGHWAY", "Way": "WAY", "Sq": "SQUARE", "Esp": "ESPLANADE", "Cct": "CIRCUIT", "Gr": "GROVE", "Grove": "GROVE"}
SUBURBS = [("Bondi Beach", "NSW", "2026"), ("Park Orchards", "VIC", "3114"), ("Surfers Paradise", "QLD", "4217"), ("Sunnybank Hills", "QLD", "4109"), ("North Sydney", "NSW", "2060"),
           ("St Kilda", "VIC", "3182"), ("Mount Gravatt East", "QLD", "4122"), ("Port Melbourne", "VIC", "3207"), ("Box Hill", "VIC", "3128"), ("Palm Beach", "NSW", "2108"), ("Fakeville", "NSW", "2000"),
           ("Surry Hills", "NSW", "2010"), ("Forest Lodge", "NSW", "2037"), ("Manly Vale", "NSW", "2093"), ("Glen Waverley", "VIC", "3150"), ("Rose Bay", "NSW", "2029"), ("Darwin", "NT", "0800"),
           ("Hobart", "TAS", "7000"), ("Perth", "WA", "6000"), ("Adelaide", "SA", "5000"), ("Canberra", "ACT", "2600")]
NEW_RULES = {"POSTAL_ADDRESS", "FLAT_TYPE_KEPT", "LEVEL_TYPE_KEPT", "STREET_SUFFIX_KEPT", "POSTCODE_ZERO_RESTORED", "LEADING_ZERO_DROPPED", "UNIT_PREFIX_FORM", "LEVEL_PREFIX_FORM", "FLOOR_WORDS_READ"}


def test_three_thousand_generated_everyday_addresses_are_read_as_written_and_set_off_no_new_rule():
    import random
    rng = random.Random(20261110)
    bad = []
    for i in range(3000):
        name, (typ, code) = rng.choice(NAMES), rng.choice(sorted(TYPES.items()))
        number = str(rng.randint(1, 400)) + rng.choice(["", "", "", "A"])
        unit = level = lot = ""
        front, roll = number, rng.random()
        if roll < 0.2:
            unit = str(rng.randint(1, 60))
            front = f"{rng.choice(['Unit', 'Apt', 'Flat', 'Shop', 'Suite'])} {unit}, {number}"
        elif roll < 0.3:
            unit = str(rng.randint(1, 60))
            front = f"{unit}/{number}"
        elif roll < 0.35:
            level = str(rng.randint(1, 9))
            front = f"Level {level}, {number}"
        elif roll < 0.38:
            lot, number = str(rng.randint(1, 80)), ""
            front = f"Lot {lot}"
        suburb, state, pc = rng.choice(SUBURBS)
        line = f"{front} {name} {typ}"
        args = rng.choice([(line, suburb, state, pc), (f"{line}, {suburb} {state} {pc}",), (f"{line.replace(', ', ' ')} {suburb} {state} {pc}",), (f"{line}, {suburb}",)])          # no comma after the street: the front has none either
        a = normalise_address(*args)
        got = (a.unit, a.level, a.lot, a.number_key, a.street_type, a.street_suffix, bool(a.street_name), NEW_RULES & set(a.rules))
        if got != (unit, level, lot, number, code, "", True, set()):
            bad.append((args, got))
    assert not bad, (len(bad), bad[:3])
