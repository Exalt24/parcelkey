"""Free text address forms the normaliser must read, from the third review (blind corpus 2, findings F1 to F10 and F12).

CLASS-TABLE: every test in this file builds its rows by code, with nested loops over a small vocabulary, and each table holds the harmful input now handled AND the ordinary input it must not break (a street
literally called St Kilda Road, a plain 3/12 Smith St, Box Hill Road beside the PO Box refusal). No test needs a database: the link direction uses `decide` on hand written candidates.
"""
from __future__ import annotations

import itertools

from parcelkey.block import Candidate
from parcelkey.master import property_id_for_derived
from parcelkey.match import decide
from parcelkey.normalise import Addr, normalise_address, state_for_postcode

FAKE = ("Fakeville", "NSW", "2000")
STREETS = [("St Ingowick Street", "SAINT INGOWICK STREET"), ("Smith St", "SMITH STREET"), ("St Kilda Road", "SAINT KILDA ROAD")]


def assert_table(rows):
    """rows are (label, got, want); every mismatch is listed at once."""
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, f"{len(bad)} of {len(rows)} rows differ, first: {bad[:5]}"


def gnaf(pid: str, number: str, unit: str = "", street: str = "SAINT INGOWICK", typ: str = "STREET") -> Candidate:
    return Candidate(pid, Addr(unit=unit, number_first=number, street_name=street, street_type=typ, locality="CORREBY", state="NSW", postcode="2557"), 1.0, True, None)


# the street as G-NAF holds it: a house at 4 and at 4A and at 18, and flat 4 and flat 5 inside number 18
REFERENCE = [gnaf("HOUSE4", "4"), gnaf("HOUSE4A", "4A"), gnaf("HOUSE4B", "4B"), gnaf("HOUSE18", "18"), gnaf("FLAT4_IN_18", "18", "4"), gnaf("FLAT5_IN_18", "18", "5")]


def decision(line: str, suburb="Correby", state="NSW", postcode="2557"):
    return decide(normalise_address(line, suburb, state, postcode), None, None, REFERENCE)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F1 to F4: a unit written with spaces around the slash is unit 4 of number 18, not number 4 beside a street called "/ 18 ..."

SEPARATORS = ["/", " / ", "/ ", " /"]
UNIT_PREFIXES = ["", "Unit ", "Apt ", "Flat ", "#", "U"]
UNIT_IDS = [("4", "4"), ("04", "4"), ("4A", "4A"), ("G1", "G1")]
HOUSE_NUMBERS = ["18", "18-20", "18A"]


def test_class_unit_slash_separators():
    """CLASS-TABLE: F1 to F4. Every unit prefix, unit id, separator spacing and house number reads as one unit of one number."""
    rows = []
    for prefix, (raw, clean), sep, number, (street_text, street_key) in itertools.product(UNIT_PREFIXES, UNIT_IDS, SEPARATORS, HOUSE_NUMBERS, STREETS):
        if prefix in ("#", "U") and not raw.isdigit():
            continue          # a glued marker takes a plain number only (`U4/18`), the same as before
        a = normalise_address(f"{prefix}{raw}{sep}{number} {street_text}", *FAKE)
        label = (prefix, raw, sep, number, street_text)
        rows.append((label, (a.unit, a.number_key, a.street_key, a.level), (clean, number, street_key, "")))
        rows.append((("rule", *label), "UNIT_SLASH_SPACED" in a.rules, sep != "/"))
        rows.append((("same as written tight", *label), a == normalise_address(f"{prefix}{raw}/{number} {street_text}", *FAKE), True))
    assert len(rows) > 1000
    assert_table(rows)


def test_class_unit_slash_never_links_a_flat_to_the_house_beside_it():
    """CLASS-TABLE: F1, F2. The flat 4 in number 18 links to that flat and never to the house at number 4 or number 18; a flat that does not exist is set aside."""
    rows = []
    for prefix, sep in itertools.product(UNIT_PREFIXES, SEPARATORS):
        line = f"{prefix}4{sep}18 St Ingowick Street"
        d = decision(line)
        rows.append((("flat 4", line), (d.outcome, d.pid), ("link", "FLAT4_IN_18")))
        missing = decision(f"{prefix}9{sep}18 St Ingowick Street")
        rows.append((("flat 9", line), (missing.outcome, missing.rule, missing.pid), ("quarantine", "UNIT_CONFLICT", "")))
    # the ordinary inputs: a plain house, a plain flat, the Unit word with a comma
    for line, pid in [("4 St Ingowick Street", "HOUSE4"), ("18 St Ingowick Street", "HOUSE18"), ("4/18 St Ingowick Street", "FLAT4_IN_18"), ("Unit 5, 18 St Ingowick Street", "FLAT5_IN_18"),
                      ("Unit 4 18 St Ingowick Street", "FLAT4_IN_18")]:
        d = decision(line)
        rows.append((("ordinary", line), (d.outcome, d.rule, d.pid), ("link", "EXACT_KEY", pid)))
    assert_table(rows)


def test_a_slash_that_is_not_between_a_unit_and_a_number_is_left_alone():
    rows = []
    for text in ["12 Smith St / Fakeville", "Shop A / Smith St", "12 Smith St/Fakeville"]:
        rows.append((text, "UNIT_SLASH_SPACED" in normalise_address(text, *FAKE).rules, False))
    rows.append((("plain 3/12", "3/12 Smith St"), normalise_address("3/12 Smith St", *FAKE).unit, "3"))
    rows.append((("plain 3/12 has no spaced rule", "3/12 Smith St"), "UNIT_SLASH_SPACED" in normalise_address("3/12 Smith St", *FAKE).rules, False))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F5: a space or a dash between the house number and its suffix letter

SUFFIX_FORMS = ["{n}{s}", "{n} {s}", "{n}-{s}"]


def test_class_number_suffix_forms():
    """CLASS-TABLE: F5. 4A, 4a, 4 A, 4 a and 4-A are all number 4A and link to the house at 4A, never to the plain 4; plain numbers and a street called A Road are unchanged."""
    rows = []
    for form, letter, number, (street_text, street_key) in itertools.product(SUFFIX_FORMS, "ABF", ("4", "12", "104"), STREETS):
        for case in (str.upper, str.lower):
            a = normalise_address(f"{form.format(n=number, s=case(letter))} {street_text}", *FAKE)
            rows.append((("suffix", form, letter, number, street_text), (a.number_first, a.street_key), (number + letter, street_key)))
            rows.append((("suffix rule", form, letter, number, street_text), "NUMBER_SUFFIX_SPACED" in a.rules, form != "{n}{s}"))
    for form, letter, case in itertools.product(SUFFIX_FORMS, "ABF", (str.upper, str.lower)):
        d = decision(f"{form.format(n='4', s=case(letter))} St Ingowick Street")
        if letter in "AB":
            rows.append((("links to the lettered house", form, letter), (d.outcome, d.pid), ("link", f"HOUSE4{letter}")))
        else:
            rows.append((("a letter G-NAF does not hold is never the plain 4", form, letter), d.pid != "HOUSE4", True))
    for text, number, street_key in [("4 St Ingowick Street", "4", "SAINT INGOWICK STREET"), ("12 A Road", "12", "A ROAD"), ("12 A St", "12", "A STREET"), ("12-14 Smith St", "12-14", "SMITH STREET"),
                                     ("12 Smith St", "12", "SMITH STREET"), ("4A St Ingowick Street", "4A", "SAINT INGOWICK STREET")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.number_key, a.street_key, "NUMBER_SUFFIX_SPACED" in a.rules), (number, street_key, False)))
    plain = decision("4 St Ingowick Street")
    rows.append((("the plain 4 still links to the plain house", ), (plain.outcome, plain.pid), ("link", "HOUSE4")))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F6 to F8: how an address is split across the columns of a feed

STREET = "18 St Ingowick Street"
LOCALITY, STATE, POSTCODE = "Correby", "NSW", "2557"
STREET_FORMS = [("18 St Ingowick Street", "18", "SAINT INGOWICK"), ("12 Smith St", "12", "SMITH")]          # a long street and a short one: the line's end is told from the street type word
LOCALITY_FORMS = [("Correby", "CORREBY"), ("Park Orchards", "PARK ORCHARDS")]


def shapes(street: str, suburb: str) -> dict[str, tuple[str, set[str]]]:
    """How one address can be written on one line: the line, and which of locality, state and postcode it carries."""
    return {
        "street only": (f"{street}", set()),
        "street, suburb": (f"{street}, {suburb}", {"loc"}),
        "street, suburb state postcode": (f"{street}, {suburb} {STATE} {POSTCODE}", {"loc", "st", "pc"}),
        "street, suburb, state postcode": (f"{street}, {suburb}, {STATE} {POSTCODE}", {"loc", "st", "pc"}),
        "street, suburb, state, postcode": (f"{street}, {suburb}, {STATE}, {POSTCODE}", {"loc", "st", "pc"}),
        "street, suburb, state": (f"{street}, {suburb}, {STATE}", {"loc", "st"}),
        "street suburb state postcode": (f"{street} {suburb} {STATE} {POSTCODE}", {"loc", "st", "pc"}),
        "street suburb, state postcode": (f"{street} {suburb}, {STATE} {POSTCODE}", {"loc", "st", "pc"}),
        "street suburb, state": (f"{street} {suburb}, {STATE}", {"loc", "st"}),
        "street suburb, postcode": (f"{street} {suburb}, {POSTCODE}", {"loc", "pc"}),
        "street, state postcode": (f"{street}, {STATE} {POSTCODE}", {"st", "pc"}),
        "street, state": (f"{street}, {STATE}", {"st"}),
        "street, postcode": (f"{street}, {POSTCODE}", {"pc"}),
        "street suburb": (f"{street} {suburb}", {"loc"}),          # no state or postcode to show the end: only a column can say the last word is a suburb
    }


NO_COMMA_BEFORE_SUBURB = {"street suburb state postcode", "street suburb, state postcode", "street suburb, state", "street suburb, postcode", "street suburb"}
UNIT_FRONTS = [("", ""), ("Unit 4, ", "4"), ("4/", "4")]


def test_class_address_split_across_the_columns():
    """CLASS-TABLE: F6 to F8. Whatever the columns leave blank is read from the end of the line, and whatever the columns already give is not read twice."""
    rows = []
    for (front, unit), (street, number, street_name), (suburb, locality), blank_loc, blank_st, blank_pc in itertools.product(UNIT_FRONTS, STREET_FORMS, LOCALITY_FORMS, (True, False), (True, False),
                                                                                                                           (True, False)):
        given = {k for k, blank in (("loc", blank_loc), ("st", blank_st), ("pc", blank_pc)) if not blank}
        for shape, (tail, carries) in shapes(street, suburb).items():
            if shape == "street suburb" and blank_loc:
                continue          # nothing marks the last word of a bare line as a suburb, so it stays with the street (the README says no suburb is guessed)
            got = normalise_address(f"{front}{tail}", "" if blank_loc else suburb, "" if blank_st else STATE, "" if blank_pc else POSTCODE)
            have = given | carries
            want = Addr(unit=unit, number_first=number, street_name=street_name, street_type="STREET", locality=locality if "loc" in have else "", state=STATE if "st" in have else "",
                        postcode=POSTCODE if "pc" in have else "")
            label = (front, street, suburb, shape, blank_loc, blank_st, blank_pc)
            filled = bool(carries - given)
            rows.append((label, got, want))
            rows.append((("read from the line", *label), "SUBURB_FROM_LINE" in got.rules, filled))
            rows.append((("repeated, so read once", *label), "LINE_TAIL_REPEATED" in got.rules or filled, bool(carries)))
            rows.append((("split by the street type word", *label), "SUBURB_SPLIT_HEURISTIC" in got.rules, filled and "loc" in carries and "loc" not in given and shape in NO_COMMA_BEFORE_SUBURB))
    assert len(rows) >= 1500
    assert_table(rows)


def test_a_whole_address_in_the_address_box_links_like_the_split_one():
    rows = []
    for shape, (tail, carries) in shapes(STREET, LOCALITY).items():
        if carries != {"loc", "st", "pc"}:
            continue
        whole = decision(tail, "", "", "")
        split = decision(STREET)
        rows.append((("whole line", shape), (whole.outcome, whole.rule, whole.pid), (split.outcome, split.rule, split.pid)))
        repeated = decision(tail, LOCALITY, STATE, POSTCODE)
        rows.append((("line repeats the columns", shape), (repeated.outcome, repeated.rule, repeated.pid), (split.outcome, split.rule, split.pid)))
    assert_table(rows)
    assert decision(STREET).pid == "HOUSE18"


def test_a_line_that_disagrees_with_the_columns_is_not_guessed_away():
    """The columns win and the line is left as it was: no new link comes from reading a tail that contradicts them."""
    rows = []
    for text, locality, state, postcode in [("12 Smith St, Otherton NSW 2000", "Fakeville", "NSW", "2000"), ("12 Smith St, Fakeville VIC 2000", "Fakeville", "NSW", "2000"),
                                            ("12 Smith St, Fakeville NSW 3000", "Fakeville", "NSW", "2000")]:
        a = normalise_address(text, locality, state, postcode)
        rows.append((text, (a.locality, a.state, a.postcode, "LINE_TAIL_REPEATED" in a.rules, "SUBURB_FROM_LINE" in a.rules), (locality.upper(), state, postcode, False, False)))
    # an ordinary line with columns: a unit, a building name and no tail are read as before
    for text, unit, number, street_key, building in [("Unit 5, 12 Smith St", "5", "12", "SMITH STREET", ""), ("Skyline Tower, Unit 5, 12 Smith St", "5", "12", "SMITH STREET", "SKYLINE TOWER"),
                                                    ("5/12 Smith St", "5", "12", "SMITH STREET", ""), ("12 St Kilda Road", "", "12", "SAINT KILDA ROAD", "")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.number_key, a.street_key, a.building, a.locality, a.state, a.postcode), (unit, number, street_key, building, "FAKEVILLE", "NSW", "2000")))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F9, F10: a comma is a separator the same as a space

JOINERS = [" ", ", "]


def test_class_one_line_address_separators():
    """CLASS-TABLE: F9, F10. A comma or a space between unit and street, street and suburb, suburb and state, state and postcode all read to the same address."""
    rows = []
    fronts = [("", ""), ("Unit 4, ", "4"), ("Unit 4 ", "4"), ("4/", "4"), ("Unit 4/", "4"), ("4 / ", "4"), ("U4/", "4"), ("#4, ", "4")]
    for (front, unit), a_sep, b_sep, c_sep, (street_text, street_name, street_type) in itertools.product(fronts, JOINERS, JOINERS, JOINERS, [("St Ingowick Street", "SAINT INGOWICK", "STREET"),
                                                                                                                                              ("Smith Rd", "SMITH", "ROAD")]):
        line = f"{front}18 {street_text}{a_sep}Correby{b_sep}NSW{c_sep}2557"
        want = Addr(unit=unit, number_first="18", street_name=street_name, street_type=street_type, locality="CORREBY", state="NSW", postcode="2557")
        got = normalise_address(line)
        rows.append(((front, a_sep, b_sep, c_sep, street_text), got, want))
        rows.append((("split by the street type word only without a comma", front, a_sep, b_sep, c_sep, street_text), "SUBURB_SPLIT_HEURISTIC" in got.rules, a_sep == " "))
        rows.append((("read from the line", front, a_sep, b_sep, c_sep, street_text), "SUBURB_FROM_LINE" in got.rules, True))
    assert len(rows) == 384
    assert_table(rows)


def test_one_line_address_tails_that_are_not_a_suburb_stay_with_the_street():
    rows = []
    for line, street_key, locality, state, postcode in [("12 Smith St North, NSW 2000", "SMITH STREET NORTH", "", "NSW", "2000"), ("12 Smith St, NSW 2000", "SMITH STREET", "", "NSW", "2000"),
                                                        ("12 Smith St, 2000", "SMITH STREET", "", "", "2000"), ("12 Smith St, Fakeville", "SMITH STREET", "FAKEVILLE", "", ""),
                                                        ("Unit 5, 12 Smith St, Fakeville, NSW, 2000", "SMITH STREET", "FAKEVILLE", "NSW", "2000"),
                                                        ("5/12 Smith St, Park Orchards VIC 3114", "SMITH STREET", "PARK ORCHARDS", "VIC", "3114"),
                                                        ("12 Smith St, Elizabeth Grove SA 5112", "SMITH STREET", "ELIZABETH GROVE", "SA", "5112"),
                                                        ("12 Smith St, Elizabeth Grove, SA 5112", "SMITH STREET", "ELIZABETH GROVE", "SA", "5112")]:
        a = normalise_address(line)
        rows.append((line, (a.street_key, a.locality, a.state, a.postcode), (street_key, locality, state, postcode)))
    for line in ["Skyline Tower", "Fakeville", "Park Orchards"]:          # one chunk with no comma has no tail chunk: nothing is read as a suburb from it
        a = normalise_address(line)
        rows.append((("no comma, no tail", line), (a.street_name, a.locality), (line.upper(), "")))
    assert_table(rows)


def test_postal_addresses_with_commas_are_still_set_aside_and_box_hill_road_is_a_street():
    rows = []
    for box, layout in itertools.product(["PO Box 5", "GPO Box 1234", "Locked Bag 5", "Private Bag 7"], [", Fakeville, NSW 2000", ", Fakeville NSW, 2000", " Fakeville NSW 2000"]):
        a = normalise_address(f"{box}{layout}")
        rows.append((("mailbox", box, layout), ("POSTAL_ADDRESS" in a.rules, a.street_name), (True, "")))
    for layout in [", Box Hill, VIC 3128", ", Box Hill VIC 3128", " Box Hill, VIC, 3128"]:
        a = normalise_address(f"14 Box Hill Rd{layout}")
        rows.append((("street", layout), ("POSTAL_ADDRESS" in a.rules, a.street_key, a.locality, a.state, a.postcode), (False, "BOX HILL ROAD", "BOX HILL", "VIC", "3128")))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F12: a blank state column must not make a second derived id

POSTCODE_STATES = [
    ("0199", ""), ("0200", "ACT"), ("0299", "ACT"), ("0300", ""), ("0799", ""), ("0800", "NT"), ("0999", "NT"), ("1000", "NSW"), ("1999", "NSW"), ("2000", "NSW"), ("2599", "NSW"),
    ("2600", "ACT"), ("2618", "ACT"), ("2619", "NSW"), ("2899", "NSW"), ("2900", "ACT"), ("2920", "ACT"), ("2921", "NSW"), ("2999", "NSW"), ("3000", "VIC"), ("3999", "VIC"),
    ("4000", "QLD"), ("4999", "QLD"), ("5000", "SA"), ("5999", "SA"), ("6000", "WA"), ("6999", "WA"), ("7000", "TAS"), ("7999", "TAS"), ("8000", "VIC"), ("8999", "VIC"),
    ("9000", "QLD"), ("9999", "QLD"), ("", ""), ("abcd", ""), ("20000", ""), ("200", ""),
]


def test_state_for_postcode_ranges():
    assert_table([(pc, state_for_postcode(pc), state) for pc, state in POSTCODE_STATES])


def test_class_blank_state_column_keeps_one_canonical_key():
    """CLASS-TABLE: F12. The same new address with the state written, spelled out or blank, and the postcode written or blank, is one derived id wherever the state can be known."""
    rows = []
    full = normalise_address("7 Quandong Cl", "Correby", "NSW", "2557")
    for state, postcode, line in itertools.product(["NSW", "New South Wales", "nsw", ""], ["2557", ""], ["7 Quandong Cl", "7 Quandong Cl, Correby", "7 Quandong Cl, Correby NSW 2557"]):
        a = normalise_address(line, "Correby", state, postcode)
        known = bool(state) or "NSW" in line
        same_id = property_id_for_derived(a.canon_key) == property_id_for_derived(full.canon_key)
        rows.append(((state, postcode, line), (a.canon_key.split("|")[-1], same_id), ("NSW" if known or postcode == "2557" else "", known or postcode == "2557")))
    # the ordinary inputs: a state that is written is never replaced by the postcode's range, a postcode in no range gives no state, a different state is a different key
    rows.append((("written state wins",), normalise_address("7 Quandong Cl", "Correby", "VIC", "2557").canon_key.split("|")[-1], "VIC"))
    rows.append((("no range",), normalise_address("7 Quandong Cl", "Correby", "", "0100").canon_key.split("|")[-1], ""))
    rows.append((("two states stay two keys",), normalise_address("7 Quandong Cl", "Correby", "VIC", "3000").canon_key == full.canon_key, False))
    rows.append((("the postcode is still not part of the key",), normalise_address("7 Quandong Cl", "Correby", "NSW", "2999").canon_key, full.canon_key))
    rows.append((("a G-NAF address keeps its own state",), Addr(number_first="7", street_name="QUANDONG", street_type="CLOSE", locality="CORREBY", state="ACT", postcode="2557").canon_key.split("|")[-1], "ACT"))
    assert_table(rows)
