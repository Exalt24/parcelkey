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
        "street suburb": (f"{street} {suburb}", {"loc"}),          # no state or postcode behind it: the first street type word ends the street
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


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# the mutation run of the third review left guards in the tail and number readers that no table pinned; each table below holds the input the guard decides

def parts(a: Addr):
    return (a.number_first, a.street_name, a.street_type, a.locality, a.state, a.postcode)


def test_class_postcode_ranges_are_sorted_four_digit_and_never_overlap():
    """CLASS-TABLE: F12. The postcode ranges are looked up first match wins, so a range that grows into its neighbour changes nothing a lookup shows; the table itself must not overlap."""
    from parcelkey.normalise import _STATE_BY_POSTCODE as ranges

    rows = [(("range is ordered", lo, hi, state), lo <= hi <= 9999 and lo >= 0, True) for lo, hi, state in ranges]
    rows += [(("starts after the one before ends", a, b), b[0] > a[1], True) for a, b in zip(ranges, ranges[1:])]
    rows.append((("every state is a known code",), {s for _, _, s in ranges} <= {"NSW", "ACT", "VIC", "QLD", "SA", "WA", "TAS", "NT"}, True))
    assert_table(rows)


def test_class_a_letter_after_the_number_needs_a_street_behind_it_even_after_a_unit():
    """CLASS-TABLE: F5. `18 A Smith St` is 18A, and so it is behind a unit written in front; with no street type behind it the A belongs to the street (`A Smith`), also behind a unit."""
    rows = []
    for front, street_text in itertools.product(["", "Unit 4 ", "Unit 4, ", "Apt 4 ", "Flat 4, ", "U4 "], ["Smith St", "Smith Rd", "Smith"]):
        a = normalise_address(f"{front}18 A {street_text}")
        two_words = " " in street_text
        rows.append(((front, street_text), (a.number_first, a.street_name), ("18A", "SMITH") if two_words else ("18", f"A {street_text.upper()}")))
    assert_table(rows)


def test_class_a_suburb_in_the_columns_and_a_state_after_the_street_type_is_read_off_the_line():
    """CLASS-TABLE: F6 to F8. The columns give the suburb only; the line ends with the street, a state, a postcode or both: they are read off the line, the street keeps its type and suffix."""
    rows = []
    for street, tail in itertools.product([("Smith St", "SMITH", "STREET"), ("Smith Rd", "SMITH", "ROAD"), ("Smith St North", "SMITH", "STREET NORTH"), ("Smith St South", "SMITH", "STREET SOUTH")],
                                          [("NSW 2000", "NSW", "2000"), ("NSW", "NSW", ""), ("VIC", "VIC", ""), ("2000", "", "2000"), ("New South Wales 2000", "NSW", "2000")]):
        a = normalise_address(f"12 {street[0]} {tail[0]}", "Correby")
        rows.append(((street[0], tail[0]), parts(a), ("12", street[1], street[2], "CORREBY", tail[1], tail[2])))
    assert_table(rows)


def test_class_a_suburb_repeated_after_a_street_with_no_type_is_not_trusted():
    """CLASS-TABLE: F6 to F8. The columns' suburb said again at the end of the line is taken off only when a street name and a type stand before it; behind a bare street name it stays in the line, and the state
    and postcode stay unread, so the record is set aside by the matcher and is never linked on a guess."""
    rows = []
    for tail in ["", " NSW", " NSW 2000"]:
        read = normalise_address(f"12 Smith St Correby{tail}", "Correby")
        rows.append((("street and type before the suburb", tail), (read.street_name, read.street_type, read.locality, bool(read.state)), ("SMITH", "STREET", "CORREBY", bool(tail))))
        for line in (f"12 Smith Correby{tail}", f"12 Correby{tail}"):
            a = normalise_address(line, "Correby")
            rows.append(((line,), (a.street_name, a.locality, a.state, a.postcode), (line[3:].upper(), "CORREBY", "", "")))
    assert_table(rows)


def test_class_a_comma_before_the_suburb_and_postcode_tells_where_the_street_ends_without_a_guess():
    """CLASS-TABLE: F9, F10. A suburb after a comma, with a postcode or a state behind it, is read as written: the street/suburb split heuristic is only for a line with no comma."""
    rows = []
    for front, street, tail in itertools.product(["", "Unit 4, ", "4/"], ["12 Smith St", "12 Smith St North"], [("Fakeville 2000", "FAKEVILLE", "", "2000"), ("Park Orchards 3114", "PARK ORCHARDS", "", "3114"),
                                                                                                                  ("Fakeville NSW", "FAKEVILLE", "NSW", ""), ("Elizabeth Grove SA 5112", "ELIZABETH GROVE", "SA", "5112")]):
        a = normalise_address(f"{front}{street}, {tail[0]}")
        rows.append(((front, street, tail[0]), ("SUBURB_SPLIT_HEURISTIC" in a.rules, a.locality, a.state, a.postcode), (False, tail[1], tail[2], tail[3])))
        b = normalise_address(f"{front}{street} {tail[0]}")
        rows.append((("without the comma", front, street, tail[0]), "SUBURB_SPLIT_HEURISTIC" in b.rules, True))
    assert_table(rows)


def test_class_the_postcode_written_before_the_state_between_commas_reads_the_same_as_state_first():
    """CLASS-TABLE: F9. `Suburb, 2000, NSW` is `Suburb, NSW, 2000`: a state and a postcode that are comma chunks of their own are read in either order, with or without a suburb or a number in front."""
    rows = []
    for front, tail in itertools.product(["12 Smith St", "Smith St", "Unit 4, 12 Smith St", "12 Smith St, Fakeville", "Smith St, Fakeville"], [", NSW, 2000", ", 2000, NSW", ", NSW 2000",
                                                                                                                                                ", 2000, New South Wales"]):
        a = normalise_address(front + tail)
        suburb = "FAKEVILLE" if "Fakeville" in front else ""
        rows.append(((front, tail), (a.street_name, a.locality, a.state, a.postcode), ("SMITH", suburb, "NSW", "2000")))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# the fourth review (final blind recall of corpus-round-3, findings F1 to F5): a unit written as a letter, behind a word with a number, after the street or with a dot

LETTER_UNIT_WORDS = ["Unit", "Flat", "Apt", "Apartment", "Shop", "Suite", "Villa", "Office", "Room", "Townhouse", "Ste", "U", "TH"]
NUMBERED_WORDS = ["Building", "Bldg", "Tower", "Twr", "Block", "Blck", "Penthouse", "Duplex", "Maisonette", "Cottage", "Studio", "Loft", "Bungalow", "Tenancy"]
FLAT_STREETS = [("Smith St", "SMITH STREET"), ("Smith Rd", "SMITH ROAD"), ("St Kilda Road", "SAINT KILDA ROAD")]
FAKE_PARTS = {"locality": "FAKEVILLE", "state": "NSW", "postcode": "2000"}


def fake_addr(number: str, name: str = "SMITH", unit: str = "") -> Addr:
    return Addr(unit=unit, number_first=number, street_name=name, street_type="STREET", **FAKE_PARTS)


def building_flats(number: str, units: list[str]) -> list[Candidate]:
    """G-NAF holds a building at `number` and one row per unit inside it, as the real data does for a block of flats."""
    return [Candidate(f"{number}:bldg", fake_addr(number), 1.0, True, None), *(Candidate(f"{number}:{u}", fake_addr(number, unit=u), 1.0, True, None) for u in units)]


def test_class_a_unit_that_is_only_a_letter_is_the_unit_after_a_unit_word_and_before_a_slash():
    """CLASS-TABLE: F1 (round 4). Unit B, Flat A, Apt C and Shop A before the street, and B/12, are unit B, A or C of number 12: the unit is never blank and never a building name."""
    rows = []
    for word, letter, sep, number, (street_text, street_key) in itertools.product(LETTER_UNIT_WORDS, "ABCGZ", [", ", " "], ["12", "12A", "12-14"], FLAT_STREETS):
        a = normalise_address(f"{word} {letter}{sep}{number} {street_text}", *FAKE)
        label = (word, letter, sep, number, street_text)
        rows.append((label, (a.unit, a.number_key, a.street_key, a.building, a.level), (letter, number, street_key, "", "")))
        rows.append((("rule", *label), "UNIT_MARKER_WORD" in a.rules, True))
        rows.append((("same flat in another word", *label), a.canon_key == normalise_address(f"Unit {letter}{sep}{number} {street_text}", *FAKE).canon_key, True))
        rows.append((("another letter is another key", *label), a.canon_key == normalise_address(f"{word} Y{sep}{number} {street_text}", *FAKE).canon_key, False))
    for letter, number, (street_text, street_key) in itertools.product("ABCGZ", ["12", "12A", "12-14"], FLAT_STREETS):
        for text in (f"{letter}/{number} {street_text}", f"{letter.lower()}/{number} {street_text}", f"Unit {letter}/{number} {street_text}", f"Flat {letter} / {number} {street_text}"):
            a = normalise_address(text, *FAKE)
            rows.append((("slash", text), (a.unit, a.number_key, a.street_key, a.building), (letter, number, street_key, "")))
    # a unit inside a level, behind a building name: still the unit
    for text, level, building in [("Suite A, Level 2, 40 Smith St", "2", ""), ("Level 2, Suite A, 40 Smith St", "2", ""), ("Skyline Tower, Unit A, 40 Smith St", "", "SKYLINE TOWER"), ("Unit A Level 2 40 Smith St", "2", "")]:
        a = normalise_address(text, *FAKE)
        rows.append((("with level or building", text), (a.unit, a.level, a.building, a.number_key), ("A", level, building, "40")))
    assert_table(rows)


def test_class_a_letter_unit_links_to_its_own_flat_never_to_the_building_or_a_neighbour():
    """CLASS-TABLE: F1 (round 4). With flats A, B and C in number 12, each written form links to its own flat; a flat that is not there, or a building with no flat rows, is set aside."""
    from parcelkey.normalise import addr_from_gnaf

    rows = []
    reference = building_flats("12", ["A", "B", "C"])
    for word, letter, form in itertools.product(["Unit", "Flat", "Apt", "Shop"], "ABC", ["{w} {l}, 12 Smith St", "{w} {l} 12 Smith St", "{l}/12 Smith St", "{w} {l}/12 Smith St"]):
        text = form.format(w=word, l=letter)
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        rows.append((("flat", text), (d.outcome, d.rule, d.pid), ("link", "EXACT_KEY", f"12:{letter}")))
        missing = decide(normalise_address(form.format(w=word, l="Q"), *FAKE), None, None, reference)
        rows.append((("no such flat", text), (missing.outcome, missing.rule, missing.pid), ("quarantine", "UNIT_CONFLICT", "")))
        building_only = decide(normalise_address(text, *FAKE), None, None, reference[:1])
        rows.append((("building only", text), (building_only.outcome, building_only.rule), ("quarantine", "UNIT_CONFLICT")))
    for letter, column in itertools.product("ABC", ("flat_number_prefix", "flat_number_suffix")):          # G-NAF keeps a flat letter in the prefix or suffix column, with no flat number
        row = {"street_name": "SMITH", "street_type_code": "STREET", "locality_name": "FAKEVILLE", "state_abbreviation": "NSW", "postcode": "2000", "number_first": 12, column: letter}
        rows.append((("G-NAF letter flat", column, letter), addr_from_gnaf(row).unit, letter))
    # the ordinary inputs: a plain house, a flat with a number, and a letter that belongs to the street or to the house number
    for text, unit, number, street_name in [("12 Smith St", "", "12", "SMITH"), ("Unit 5, 12 Smith St", "5", "12", "SMITH"), ("12 A Smith St", "", "12A", "SMITH"), ("12A Smith St", "", "12A", "SMITH"),
                                            ("Unit A Smith St", "", "", "UNIT A SMITH"), ("Shop A Smith Rd", "", "", "SHOP A SMITH")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.number_key, a.street_name), (unit, number, street_name)))
    assert_table(rows)


CAR_SPELLINGS = [("Car Space", "CARS"), ("CAR SPACE", "CARS"), ("car space", "CARS"), ("Car-Space", "CARS"), ("Carspace", "CARS"), ("Car Park", "CARP"), ("car park", "CARP"), ("Car-Park", "CARP"), ("Carpark", "CARP")]


def test_class_car_space_and_car_park_are_read_like_carspace_and_carpark():
    """CLASS-TABLE: F1 (round 4). Car Space 12 is CARS 12 and Car Park 3 is CARP 3, never the building `CAR SPACE`; a street called Car Park Road keeps its name."""
    from parcelkey.normalise import addr_from_gnaf

    rows = []
    for (spelling, code), (ident, clean), sep in itertools.product(CAR_SPELLINGS, [("12", "12"), ("03", "3"), ("A", "A")], [", ", " "]):
        a = normalise_address(f"{spelling} {ident}{sep}40 Smith St", *FAKE)
        rows.append((("car word", spelling, ident, sep), (a.unit, a.number_key, a.building, a.street_key), (f"{code} {clean}", "40", "", "SMITH STREET")))
        rows.append((("rule", spelling, ident, sep), "CAR_WORD_JOINED" in a.rules, " " in spelling or "-" in spelling))
    for code, word in [("CARS", "Car Space"), ("CARP", "Car Park")]:
        g = addr_from_gnaf({"street_name": "SMITH", "street_type_code": "STREET", "number_first": 40, "flat_type_code": code, "flat_number": 12})
        rows.append((("meets G-NAF", code), normalise_address(f"{word} 12, 40 Smith St").unit, g.unit))
    for text, street_key in [("12 Car Park Rd", "CAR PARK ROAD"), ("12 Car Space St", "CAR SPACE STREET")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.number_key, a.street_key, "CAR_WORD_JOINED" in a.rules), ("", "12", street_key, False)))
    hill = normalise_address("12 Smith St, Car Park Hill NSW 2000")
    rows.append((("a suburb called Car Park Hill",), (hill.unit, hill.street_key, hill.locality, "CAR_WORD_JOINED" in hill.rules), ("", "SMITH STREET", "CAR PARK HILL", False)))
    assert_table(rows)


def test_class_a_word_with_a_number_before_the_street_is_the_unit_and_the_house_number_stays_the_house_number():
    """CLASS-TABLE: F2 (round 4). Unit No. 5, Flat No 6, Apartment Number 5, Building 2, Tower 3, Block 3, Penthouse 1, Duplex 2 in front of 100 Smith St is unit 5, 6, 5, 2, 3, 3, 1, 2 of number 100;
    the street name never starts with a digit."""
    rows = []
    labels = ["", "No ", "No. ", "NO ", "Number ", "Nr. "]
    for word, label, ident, sep, number, (street_text, street_key) in itertools.product(LETTER_UNIT_WORDS + NUMBERED_WORDS, labels, [("5", "5"), ("05", "5"), ("5A", "5A")], [", ", " "],
                                                                                         ["12", "100", "12A"], FLAT_STREETS):
        text = f"{word} {label}{ident[0]}{sep}{number} {street_text}"
        a = normalise_address(text, *FAKE)
        rows.append((("word and number", text), (a.unit, a.number_key, a.street_key, a.building), (ident[1], number, street_key, "")))
        rows.append((("no digit in the street name", text), a.street_name[:1].isdigit(), False))
        rows.append((("read as a unit word", text), "UNIT_MARKER_WORD" in a.rules, True))
        rows.append((("label rule only when there is a label", text), "UNIT_NO_WORD_DROPPED" in a.rules, bool(label)))
    # the ordinary inputs: a building or tower in the name, a street or a suburb that starts with one of the words, a unit as well as a tower
    for text, unit, building, number, street_name, locality in [
        ("Skyline Tower 12 Smith St", "", "SKYLINE TOWER", "12", "SMITH", "FAKEVILLE"), ("Tower, 12 Smith St", "", "TOWER", "12", "SMITH", "FAKEVILLE"),
        ("Block Arcade, 12 Smith St", "", "BLOCK ARCADE", "12", "SMITH", "FAKEVILLE"), ("12 Tower Rd", "", "", "12", "TOWER", "FAKEVILLE"),
        ("Tower 3, Unit 5, 12 Smith St", "5", "TOWER 3", "12", "SMITH", "FAKEVILLE"), ("Unit 5, Tower 3, 12 Smith St", "5", "TOWER 3", "12", "SMITH", "FAKEVILLE"),
        ("Tower 3, 5/12 Smith St", "5", "TOWER 3", "12", "SMITH", "FAKEVILLE"), ("Skyline Tower 3 12 Smith St", "3", "SKYLINE", "12", "SMITH", "FAKEVILLE"),
        ("Tower 3/12 Smith St", "3", "TOWER", "12", "SMITH", "FAKEVILLE"), ("Tower 3 12 Villa St", "3", "", "12", "VILLA", "FAKEVILLE"), ("Block 3, 12 Shop Rd", "3", "", "12", "SHOP", "FAKEVILLE"),
        ("Tower 3 Unit 5 12 Smith St", "5", "TOWER 3", "12", "SMITH", "FAKEVILLE"),
    ]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.building, a.number_key, a.street_name, a.locality), (unit, building, number, street_name, locality)))
        rows.append((("building rule", text), "BUILDING_NAME_PREFIX" in a.rules, bool(building)))
    for text, locality in [("12 Smith St, Cottage Point NSW 2084", "COTTAGE POINT"), ("12 Smith St, Tower Hill VIC 3283", "TOWER HILL"), ("12 Smith St, Bungalow QLD 4870", "BUNGALOW"),
                           ("12 Smith St Block NSW 2000", "BLOCK")]:
        a = normalise_address(text)
        rows.append((("a suburb that is also a word", text), (a.unit, a.street_key, a.locality), ("", "SMITH STREET", locality)))
    assert_table(rows)


def test_class_a_word_with_a_number_never_links_the_record_to_a_house_of_that_number():
    """CLASS-TABLE: F2 (round 4). G-NAF holds houses at 2, 3, 5, 6 and 100 Smith Street, number 12 with flats 5 and 6, and number 100 with flat 7: a record that names a flat links to that flat or is
    set aside, and never to the house whose number is the flat's number or to the building."""
    rows = []
    houses = [Candidate(f"HOUSE{n}", fake_addr(n), 1.0, True, None) for n in ("2", "3", "5", "6", "100")]
    reference = houses + building_flats("12", ["5", "6"]) + building_flats("100", ["7"])[1:]
    for word, label, ident, number in itertools.product(["Unit", "Flat", "Apartment", "Building", "Tower", "Block", "Penthouse", "Duplex"], ["", "No. ", "Number "], ["2", "3", "5", "6", "7", "9"],
                                                        ["12", "100"]):
        text = f"{word} {label}{ident}, {number} Smith St"
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        exists = (number == "12" and ident in ("5", "6")) or (number == "100" and ident == "7")
        rows.append((("flat", text), (d.outcome, d.pid) if exists else (d.outcome, d.rule), ("link", f"{number}:{ident}") if exists else ("quarantine", "UNIT_CONFLICT")))
    assert_table(rows)


def test_class_a_street_name_that_starts_with_a_digit_is_never_linked_on_a_near_miss():
    """CLASS-TABLE: F2 (round 4). A record whose street name starts with a digit (a house number read into the street) scores 0.8 against the street without it; it is set aside and never linked by TRIGRAM,
    while a near miss of an ordinary street name still links and an exact match of a street that starts with a digit still links."""
    rows = []
    for digits, name, sim in itertools.product(["12", "5", "100", "1ST", "5TH"], ["SMITH", "ST KILDA"], [0.6, 0.8, 0.95, 1.0]):
        near = Candidate("P1", fake_addr("7", name), sim, True, None)
        rec = fake_addr("7", f"{digits} {name}")
        d = decide(rec, None, None, [near])
        rows.append((("digit street", digits, name, sim), (d.outcome, d.rule, d.reason, d.detail.get("why")), ("quarantine", "BELOW_THRESHOLD", "BELOW_THRESHOLD", "the street name starts with a digit")))
        clean = decide(fake_addr("7", name + "X"), None, None, [near])
        rows.append((("near miss of an ordinary name", name, sim), (clean.outcome, clean.rule, clean.pid, clean.detail.get("why")), ("link", "TRIGRAM", "P1", None)))
        exact = Candidate("P2", fake_addr("7", rec.street_name), 1.0, True, None)
        again = decide(rec, None, None, [near, exact])
        rows.append((("an exact street that starts with a digit", digits, name), (again.outcome, again.rule, again.pid), ("link", "EXACT_KEY", "P2")))
    assert_table(rows)


COUNTRY_TAILS = ["", ", Australia", " Australia", ", AU", " AU", ", Aus", " AUS", ",Australia", ", australia"]
COUNTRY_SHAPES = [("{s}, Fakeville NSW 2000", True), ("{s}, Fakeville, NSW, 2000", True), ("{s}, Fakeville, NSW 2000", True), ("{s} Fakeville NSW 2000", True), ("{s}, Fakeville New South Wales 2000", True),
                  ("{s}, Fakeville 2000", True), ("{s}, NSW 2000", True), ("{s}, Fakeville, NSW", True), ("{s}, Fakeville", False), ("{s}", False)]


def test_class_a_trailing_country_and_a_bracket_note_are_dropped():
    """CLASS-TABLE: F3 (round 4). `, Australia`, ` Australia`, `, AU`, ` AUS` after the postcode or the state, and a note such as (rear) anywhere, change nothing about the address that is read."""
    rows = []
    for front, (street_text, street_key), (shape, shows_the_end), tail, note in itertools.product(["", "Unit 4, ", "4/"], [("12 Smith St", "SMITH STREET"), ("12 St Kilda Road", "SAINT KILDA ROAD")],
                                                                                                 COUNTRY_SHAPES, COUNTRY_TAILS, ["", " (rear)", " (investment property)"]):
        if tail.startswith(" ") and not shows_the_end:
            continue          # a bare ` Australia` straight after a street or a suburb has no state or postcode to show it is a country
        plain = normalise_address(front + shape.format(s=street_text))
        written = front + shape.format(s=street_text + note) + tail
        got = normalise_address(written)
        rows.append((("same address", written), got, plain))
        rows.append((("country rule", written), "COUNTRY_DROPPED" in got.rules, bool(tail)))
        rows.append((("note rule", written), "BRACKET_NOTE_DROPPED" in got.rules, bool(note)))
        with_columns = normalise_address(written, "Fakeville", "NSW", "2000")
        rows.append((("with the columns", written), (with_columns.street_key, with_columns.locality, with_columns.state, with_columns.postcode, with_columns.number_key),
                     (street_key, "FAKEVILLE", "NSW", "2000", "12")))
    # a note that was never closed ends at the next comma, or at the end of the line, and takes nothing else with it
    for text in ["12 Smith St (rear, Fakeville NSW 2000", "12 Smith St, Fakeville NSW 2000 (rear", "12 Smith St (rear), Fakeville NSW 2000", "12 Smith St (rear, side), Fakeville NSW 2000"]:
        a = normalise_address(text)
        rows.append((("note", text), (a.number_key, a.street_key, a.locality, a.state, a.postcode, "BRACKET_NOTE_DROPPED" in a.rules), ("12", "SMITH STREET", "FAKEVILLE", "NSW", "2000", True)))
    # the ordinary inputs: a street or a suburb that has the country in its name is not a country
    for text, street_key, locality in [("12 Australia St, Fakeville NSW 2000", "AUSTRALIA STREET", "FAKEVILLE"), ("12 Smith St, Australia Fair NSW 2000", "SMITH STREET", "AUSTRALIA FAIR"),
                                       ("12 Smith St, Fakeville NSW 2000", "SMITH STREET", "FAKEVILLE"), ("12 Au St, Fakeville NSW 2000", "AU STREET", "FAKEVILLE")]:
        a = normalise_address(text)
        rows.append((("ordinary", text), (a.street_key, a.locality, "COUNTRY_DROPPED" in a.rules), (street_key, locality, False)))
    assert_table(rows)


def test_class_a_trailing_country_does_not_make_a_second_property():
    """CLASS-TABLE: F3 (round 4). The record with `, Australia` links like the same record without it, to the same G-NAF row, and has the same derived key."""
    rows = []
    reference = building_flats("12", ["5"])
    for text in ["12 Smith St, Fakeville NSW 2000", "5/12 Smith St, Fakeville NSW 2000", "9/12 Smith St, Fakeville NSW 2000"]:
        base = decide(normalise_address(text), None, None, reference)
        for tail in ("", ", Australia", " Australia", ", AU", " AU", " (rear)", ", Australia (rear)"):
            d = decide(normalise_address(text + tail), None, None, reference)
            rows.append((("links the same", text, tail), (d.outcome, d.rule, d.pid), (base.outcome, base.rule, base.pid)))
            rows.append((("derived key is the same", text, tail), normalise_address(text + tail).canon_key, normalise_address(text).canon_key))
    assert_table(rows)


# (written after the street, written in front of it, the unit key)
AFTER_UNITS = [(", Unit 5", "Unit 5", "5"), (" Unit 5", "Unit 5", "5"), (", Apt 5", "Apt 5", "5"), (" Apartment 5", "Apartment 5", "5"), (", Flat 5", "Flat 5", "5"), (", Shop 5", "Shop 5", "5"),
               (", Suite 05", "Suite 5", "5"), (" #5", "#5", "5"), (", Unit 5A", "Unit 5A", "5A"), (", Unit B", "Unit B", "B"), (", Shop A", "Shop A", "A"), (", Unit No. 5", "Unit 5", "5"),
               (", Building 5", "Unit 5", "5"), (", Tower 5", "Unit 5", "5"), (", Garage 5", "Garage 5", "GRGE 5"), (", Car Space 5", "Carspace 5", "CARS 5"), (", Unit 3.02", "Unit 302", "302")]
AFTER_STREETS = [("12 Smith St", "SMITH STREET"), ("12 Smith Street", "SMITH STREET"), ("12 Smith Rd", "SMITH ROAD"), ("12 St Kilda Road", "SAINT KILDA ROAD"), ("12 Smith St North", "SMITH STREET NORTH"),
                 ("12 Smith Gdns", "SMITH GARDENS"), ("12 Smith St Upper", "SMITH STREET UPPER"), ("12 Smith St N", "SMITH STREET NORTH"), ("12 Smith St Lower", "SMITH STREET LOWER")]


def test_class_a_unit_written_after_the_street_is_the_unit():
    """CLASS-TABLE: F4 (round 4). `12 Smith St, Unit 5`, `12 Smith Street Apt 6` and `12 Smith St #5` are unit 5 or 6 of number 12 with the street type read, whether the suburb comes from columns, from the line or
    not at all; the unit is never part of the street name and St never becomes SAINT."""
    rows = []
    for (street_text, street_key), (after, front, unit), where in itertools.product(AFTER_STREETS, AFTER_UNITS, ["columns", "line", "line, comma", "none"]):
        if where == "line" and street_key.split()[-1] in ("NORTH", "UPPER", "LOWER"):
            continue          # no comma before the suburb: the suffix after the street type is read as part of the suburb, as the SUBURB_SPLIT_HEURISTIC rule says
        line = {"columns": street_text + after, "line": f"{street_text}{after} Fakeville NSW 2000", "line, comma": f"{street_text}{after}, Fakeville, NSW 2000", "none": street_text + after}[where]
        parts = FAKE if where == "columns" else ("", "", "")
        a = normalise_address(line, *parts)
        rows.append((("unit after the street", where, line), (a.unit, a.number_key, a.street_key, a.building), (unit, "12", street_key, "")))
        rows.append((("suburb", where, line), (a.locality, a.state, a.postcode), ("FAKEVILLE", "NSW", "2000") if where in ("columns", "line", "line, comma") else ("", "", "")))
        rows.append((("rule", where, line), "UNIT_AFTER_STREET" in a.rules, True))
        first = normalise_address(f"{front}, {street_text}", *parts)
        rows.append((("same address as the unit written first", where, line), (a.unit, a.number_key, a.street_key), (first.unit, first.number_key, first.street_key)))
    # the same unit written twice is read once; two different units are not guessed between; a word that belongs to a street or a suburb is left alone
    for text, unit, rule in [("Unit 5, 12 Smith St, Unit 5", "5", True), ("5/12 Smith St, Unit 5", "5", True), ("Unit 5, 12 Smith St Apt 5", "5", True), ("Unit 5, 12 Smith St, Unit 6", "5", False),
                             ("5/12 Smith St, Flat 6", "5", False), ("12 Villa St", "", False), ("12 Shop St, Fakeville", "", False), ("12 Smith St Villa Park NSW 2000", "", False),
                             ("12 Smith St Unit Park NSW 2000", "", False), ("12 Smith St, Unit", "", False), ("12 Smith Unit 5", "", False), ("12 Big Smith Upper Unit 5", "", False),
                             ("12 St Upper Unit 5", "", False), ("12 Smith Villa 5 Rd", "", False)]:
        a = normalise_address(text)
        rows.append((("two units or none", text), (a.unit, "UNIT_AFTER_STREET" in a.rules), (unit, rule)))
    for text, unit in [("12 Smith St Unit 5/7", ""), ("12 Smith St, U5/7", "")]:          # a slash after the street is not a unit written after the street
        a = normalise_address(text)
        rows.append((("slash after the street", text), (a.unit, "UNIT_AFTER_STREET" in a.rules), (unit, False)))
    assert_table(rows)


def test_class_a_unit_written_after_the_street_links_to_its_flat_and_makes_no_new_property():
    """CLASS-TABLE: F4 (round 4). The unit after the street links to the flat in G-NAF; a flat that is not there is set aside; none of them is a new derived property."""
    rows = []
    reference = building_flats("12", ["5", "6"])
    for (after, _front, unit), (street_text, _key) in itertools.product([a for a in AFTER_UNITS if a[2] in ("5", "5A", "B")], [("12 Smith St", ""), ("12 Smith Street", "")]):
        d = decide(normalise_address(street_text + after, *FAKE), None, None, reference)
        rows.append((("flat", street_text + after), (d.outcome, d.pid) if unit == "5" else d.outcome, ("link", "12:5") if unit == "5" else "quarantine"))
        rows.append((("never a new property", street_text + after), d.outcome == "new", False))
    assert_table(rows)


UNIT_WORDS_FOR_DOTS = ["Unit", "Apt", "Apartment", "Flat", "Suite", "Shop", "Villa", "Office", "Room", "Townhouse", "Ste", "Building", "Tower"]
DOTTED = [("3.02", "302"), ("1.05", "105"), ("12.10", "1210"), ("0.05", "5"), ("3.2", "32"), ("12-05", "1205"), ("3-02", "302"), ("1-09", "109")]


def test_class_a_dotted_or_dashed_unit_number_is_one_unit_number():
    """CLASS-TABLE: F5 (round 4). Unit 3.02, Apt 1.05, Suite 12.10 and Unit 12-05 are the flats 302, 105, 1210 and 1205 (G-NAF holds a flat as one integer, FLAT_NUMBER, up to five digits, so the dot cannot be
    kept); the house number stays the house number and the street keeps its name."""
    rows = []
    for word, (written, joined), sep, number, (street_text, street_key) in itertools.product(UNIT_WORDS_FOR_DOTS, DOTTED, [", ", " "], ["12", "100", "12A"], FLAT_STREETS):
        a = normalise_address(f"{word} {written}{sep}{number} {street_text}", *FAKE)
        label = (word, written, sep, number, street_text)
        rows.append((label, (a.unit, a.number_key, a.street_key, a.building), (joined, number, street_key, "")))
        rows.append((("rule", *label), "UNIT_NUMBER_JOINED" in a.rules, True))
        rows.append((("same as the joined number", *label), a.canon_key == normalise_address(f"{word} {joined}{sep}{number} {street_text}", *FAKE).canon_key, True))
    for (written, joined), number, (street_text, street_key) in itertools.product([d for d in DOTTED if "." in d[0]], ["12", "100"], FLAT_STREETS):
        for text in (f"{written}/{number} {street_text}", f"U{written}/{number} {street_text}", f"#{written}, {number} {street_text}", f"U {written}, {number} {street_text}"):
            a = normalise_address(text, *FAKE)
            rows.append((("dotted before the slash or glued", text), (a.unit, a.number_key, a.street_key), (joined, number, street_key)))
    # the ordinary inputs: a range of house numbers, a dash after a comma, a lot, a dot that is not in a unit
    for text, unit, number in [("12-14 Smith St", "", "12-14"), ("Unit 5, 12-14 Smith St", "5", "12-14"), ("Unit 1, 2-4 Smith St", "1", "2-4"), ("5/12-14 Smith St", "5", "12-14"),
                               ("Lot 7, 12 Smith St", "", "12"), ("12 Smith St. Fakeville", "", "12"), ("Unit 5 12-14 Smith St", "5", "12-14")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.number_key, "UNIT_NUMBER_JOINED" in a.rules), (unit, number, False)))
    assert_table(rows)


def test_class_a_dotted_unit_links_to_the_flat_g_naf_holds_without_the_dot():
    """CLASS-TABLE: F5 (round 4). A record that writes flat 302 as 3.02 links to the G-NAF flat 302 of the same number, never to the house at number 2 or 3."""
    rows = []
    reference = building_flats("100", ["302", "1205", "105"]) + [Candidate("HOUSE2", fake_addr("2"), 1.0, True, None), Candidate("HOUSE3", fake_addr("3"), 1.0, True, None)]
    for text, unit in [("Unit 3.02, 100 Smith St", "302"), ("Apt 12-05, 100 Smith St", "1205"), ("Suite 1.05, 100 Smith St", "105"), ("3.02/100 Smith St", "302"), ("Unit 3.03, 100 Smith St", "")]:
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        rows.append((("dotted flat", text), (d.outcome, d.pid) if unit else (d.outcome, d.rule), ("link", f"100:{unit}") if unit else ("quarantine", "UNIT_CONFLICT")))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# the fifth review (final blind recall of corpus-round-4, findings F1, F2, F5, F6, F8): a level word before a slash, a number range with spaces, a dotted state, a suburb with no state behind it, a lot with a plan

LEVEL_SLASH_WORDS = ["Level", "LEVEL", "level", "Lvl", "Floor", "Flr", "L"]
LEVEL_SLASH_IDS = [("2", "2"), ("02", "2"), ("12", "12"), ("2A", "2A"), ("G", "G")]
LEVEL_SLASH_STREETS = [("Pitt St", "PITT STREET"), ("St Kilda Road", "SAINT KILDA ROAD")]


def test_class_a_level_word_before_a_slash_is_the_level_not_a_unit():
    """CLASS-TABLE: F1 (round 5). `Level 2/45 Pitt St`, `Lvl 2/45`, `Floor 2/45`, `Ground Floor/45` and `Level G/12` are level 2, 2, 2, G and G of the number after the slash, the same address as the
    comma form `Level 2, 45 Pitt St` and as `L2/45`; the unit stays empty and `LEVEL` is never a building name."""
    rows = []
    for word, (ident, clean), sep, number, (street_text, street_key) in itertools.product(LEVEL_SLASH_WORDS, LEVEL_SLASH_IDS, ["/", " / "], ["45", "45-47", "45A"], LEVEL_SLASH_STREETS):
        a = normalise_address(f"{word} {ident}{sep}{number} {street_text}", *FAKE)
        label = (word, ident, sep, number, street_text)
        rows.append((label, (a.unit, a.level, a.building, a.number_key, a.street_key), ("", clean, "", number, street_key)))
        rows.append((("rule", *label), ("LEVEL_PREFIX_FORM" in a.rules, "LEVEL_MARKER" in a.rules, "UNIT_SLASH_FORM" in a.rules), (True, True, True)))
        rows.append((("same as the comma form", *label), a.canon_key, normalise_address(f"{word} {ident}, {number} {street_text}", *FAKE).canon_key))
        if ident != "G":
            rows.append((("same as L2/45", *label), a.canon_key, normalise_address(f"L{ident}/{number} {street_text}", *FAKE).canon_key))
    for text, level, number in [("Ground Floor/45 Pitt St", "G", "45"), ("Ground Level/45 Pitt St", "G", "45"), ("3rd Floor/45 Pitt St", "3", "45"), ("1st Floor / 45 Pitt St", "1", "45"), ("Level 2/45 Pitt St, Sydney NSW 2000", "2", "45")]:
        a = normalise_address(text, *FAKE)
        rows.append((("floor words", text), (a.unit, a.level, a.building, a.number_key), ("", level, "", number)))
    # a level word with no digits yet, and a slash form that is not a level: a word between a unit and the slash, a level written twice, an id with a letter before its digits
    for text in ["Level Ground, 45 Pitt St", "Level Ground 45 Pitt St", "Ground Level, 45 Pitt St"]:
        a = normalise_address(text, *FAKE)
        rows.append((("level ground", text), (a.unit, a.level, a.number_key), ("", "G", "45")))
    for text in ["Unit 5 Foo 2/45 Pitt St", "Unit 5, Skyline 2/45 Pitt St", "Level B2/45 Pitt St", "Floor AB2/45 Pitt St", "Level 3 Level 2/45 Pitt St"]:
        a = normalise_address(text, *FAKE)
        rows.append((("not read as a level before a slash", text), (a.level in ("", "3"), a.level == "2", "LEVEL_PREFIX_FORM" in a.rules), (True, False, False)))
    # the ordinary inputs: a unit with a slash, a level with a comma, a level and a unit, a unit word before the slash, a unit that is a letter and a digit
    for text, unit, level, building, number in [("3/45 Pitt St", "3", "", "", "45"), ("Unit 3/45 Pitt St", "3", "", "", "45"), ("Level 2, 45 Pitt St", "", "2", "", "45"), ("L3/45 Pitt St", "", "3", "", "45"),
                                                ("Level 2, 5/45 Pitt St", "5", "2", "", "45"), ("Level 2 Suite 7, 45 Pitt St", "7", "2", "", "45"), ("Suite 2/45 Pitt St", "2", "", "", "45"),
                                                ("Shop 2/45 Pitt St", "2", "", "", "45"), ("G1/45 Pitt St", "G1", "", "", "45"), ("Skyline Tower, Level 2, 45 Pitt St", "", "2", "SKYLINE TOWER", "45"),
                                                ("Level 2 45 Pitt St", "", "2", "", "45")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.level, a.building, a.number_key), (unit, level, building, number)))
    assert_table(rows)


def test_class_a_floor_written_with_a_slash_links_to_the_floor_never_to_the_flat_of_that_number():
    """CLASS-TABLE: F1 (round 5). With floor 2, flat 2 and the building itself at number 45, `Level 2/45` in every spelling links to the floor, and to nothing else."""
    rows = []
    street = dict(street_name="PITT", street_type="STREET", locality="FAKEVILLE", state="NSW", postcode="2000")
    reference = [Candidate("BLDG", Addr(number_first="45", **street), 1.0, True, None), Candidate("FLOOR2", Addr(level="2", number_first="45", **street), 1.0, True, None),
                 Candidate("FLAT2", Addr(unit="2", number_first="45", **street), 1.0, True, None)]
    for word, sep in itertools.product(LEVEL_SLASH_WORDS, ["/", " / ", ", "]):
        text = f"{word} 2{sep}45 Pitt St"
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        rows.append((("floor", text), (d.outcome, d.rule, d.pid), ("link", "EXACT_KEY", "FLOOR2")))
        other = decide(normalise_address(f"{word} 9{sep}45 Pitt St", *FAKE), None, None, reference)
        rows.append((("a floor that is not there is the building", text), (other.outcome, other.pid), ("link", "BLDG")))
    for text, pid in [("2/45 Pitt St", "FLAT2"), ("Unit 2, 45 Pitt St", "FLAT2"), ("45 Pitt St", "BLDG")]:
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        rows.append((("ordinary", text), (d.outcome, d.rule, d.pid), ("link", "EXACT_KEY", pid)))
    assert_table(rows)


RANGE_SEPARATORS = ["-", " - ", " -", "- ", " \u2013 ", "\u2013", " \u2014 ", "\u2014", " \u2212 ", " & ", "&", " to ", " TO ", " To "]
RANGE_NUMBERS = [("12", "14"), ("2", "4"), ("101", "103"), ("12A", "14B")]
RANGE_FRONTS = ["", "Unit 5, ", "Unit 5 ", "5/", "5 / ", "Level 2, "]
RANGE_TAILS = ["", ", Fakeville NSW 2000"]


def test_class_a_street_number_range_with_spaces_another_dash_or_a_word_is_one_range():
    """CLASS-TABLE: F2 (round 5). `12 - 14 Smith St`, an en or em dash, `12 & 14` and `12 to 14` are the range 12-14, the same address as `12-14 Smith St`: number first and last, street SMITH STREET,
    never number 12 on a street called `14 SMITH`."""
    rows = []
    for front, (first, last), sep, (street_text, street_key), tail in itertools.product(RANGE_FRONTS, RANGE_NUMBERS, RANGE_SEPARATORS, [("Smith St", "SMITH STREET"), ("St Kilda Road", "SAINT KILDA ROAD")], RANGE_TAILS):
        text = f"{front}{first}{sep}{last} {street_text}{tail}"
        tight = normalise_address(f"{front}{first}-{last} {street_text}{tail}", *FAKE)
        a = normalise_address(text, *FAKE)
        rows.append((("parts", text), (a.number_first, a.number_last, a.street_key), (first, last, street_key)))
        rows.append((("same as the tight form", text), (a, a.canon_key), (tight, tight.canon_key)))
        rows.append((("rule", text), "NUMBER_RANGE_READ" in a.rules, sep != "-"))
        rows.append((("no digit in the street name", text), a.street_name[:1].isdigit(), False))
    # the ordinary inputs: a pair of numbers after a unit, level or lot word is not a range of house numbers, and a plain range or a single number is read as before
    for text in ["Unit 3 & 4, 12 Smith St", "Unit 3 - 4, 12 Smith St", "Units 1 & 2, 12 Smith St", "Level 1 - 2, 12 Smith St", "Shop 1 to 2, 12 Smith St", "Suite 3 & 4, 12 Smith St", "Lot 5 - 7, 12 Smith St",
                 "Flat No 3 & 4, 12 Smith St", "# 3 - 4, 12 Smith St", "Tower 3 - 4, 12 Smith St"]:
        a = normalise_address(text, *FAKE)
        rows.append((("not a range", text), "NUMBER_RANGE_READ" in a.rules, False))
    for text, number in [("12-14 Smith St", "12-14"), ("12 Smith St", "12"), ("Unit 5, 12 Smith St", "12"), ("5/12-14 Smith St", "12-14"), ("12 Smith St - Fakeville", "12")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), ("NUMBER_RANGE_READ" in a.rules, a.number_key), (False, number)))
    assert len(rows) > 1000
    assert_table(rows)


def test_class_a_number_range_with_spaces_links_to_the_range_address_and_never_to_a_house_beside_it():
    """CLASS-TABLE: F2 (round 5). G-NAF holds `12-14 Smith St` and a house at 16; the record written with spaces, an en dash, an ampersand or `to` links to the range address."""
    rows = []
    street = dict(street_name="SMITH", street_type="STREET", locality="FAKEVILLE", state="NSW", postcode="2000")
    reference = [Candidate("RANGE", Addr(number_first="12", number_last="14", **street), 1.0, True, None), Candidate("H16", Addr(number_first="16", **street), 1.0, True, None),
                 Candidate("H14", Addr(number_first="14", **street), 1.0, True, None)]
    for sep in RANGE_SEPARATORS:
        d = decide(normalise_address(f"12{sep}14 Smith St", *FAKE), None, None, reference)
        rows.append((("range", sep), (d.outcome, d.rule, d.pid), ("link", "EXACT_KEY", "RANGE")))
    for text, pid in [("16 Smith St", "H16"), ("14 Smith St", "H14")]:
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        rows.append((("ordinary", text), (d.outcome, d.rule, d.pid), ("link", "EXACT_KEY", pid)))
    assert_table(rows)


DOTTED_STATES = {"NSW": ["N.S.W.", "N.S.W", "N. S. W.", "n.s.w.", "N.S.W. "], "VIC": ["V.I.C.", "V.I.C", "V. I. C."], "QLD": ["Q.L.D.", "Q.L.D", "Q. L. D."], "SA": ["S.A.", "S.A", "S. A."],
                 "WA": ["W.A.", "W.A", "W. A."], "TAS": ["T.A.S.", "T.A.S", "T. A. S."], "NT": ["N.T.", "N.T", "N. T."], "ACT": ["A.C.T.", "A.C.T", "A. C. T."], "OT": ["O.T.", "O.T"]}
STATE_LAYOUTS = ["{s}, Fakeville {st} 2000", "{s}, Fakeville, {st} 2000", "{s}, Fakeville, {st}, 2000", "{s} Fakeville {st} 2000", "{s}, Fakeville {st}"]


def test_class_a_state_written_with_dots_is_the_state():
    """CLASS-TABLE: F5 (round 5). `N.S.W.`, `A.C.T.`, `Q.L.D.`, `W.A.` and the rest, with or without the last dot or with spaces, are the state: the street keeps its type (`St` is never SAINT), the suburb is
    the suburb, and the result is the plain-state address. The same holds for a state column."""
    rows = []
    for code, forms in DOTTED_STATES.items():
        for form, (street_text, street_key), layout in itertools.product(forms, [("Smith St", "SMITH STREET"), ("St Kilda Rd", "SAINT KILDA ROAD")], STATE_LAYOUTS):
            text = layout.format(s=f"12 {street_text}", st=form)
            plain = normalise_address(layout.format(s=f"12 {street_text}", st=code))
            a = normalise_address(text)
            rows.append((("parts", text), (a.street_key, a.locality, a.state, a.postcode), (street_key, "FAKEVILLE", code, plain.postcode)))
            rows.append((("same as the plain state", text), a, plain))
            rows.append((("rule", text), "STATE_DOTS_REMOVED" in a.rules, True))
            rows.append((("plain state fires nothing", text), "STATE_DOTS_REMOVED" in plain.rules, False))
        for form in forms:
            col = normalise_address("12 Smith St", "Fakeville", form, "2000")
            rows.append((("state column", code, form), (col.state, col.locality, col.postcode, col.street_key), (code, "FAKEVILLE", "2000", "SMITH STREET")))
    # glued to the postcode, and a state in a street or a suburb name is left alone
    for text, state, postcode in [("12 Smith St, Fakeville, N.S.W.2000", "NSW", "2000"), ("12 Smith St, Fakeville A.C.T.2600", "ACT", "2600")]:
        a = normalise_address(text)
        rows.append((("glued to the postcode", text), (a.street_key, a.locality, a.state, a.postcode), ("SMITH STREET", "FAKEVILLE", state, postcode)))
    for text, street_key, locality, state in [("12 Smith St. Fakeville NSW 2000", "SMITH STREET", "FAKEVILLE", "NSW"), ("12 St Kilda Rd, St Kilda VIC 3182", "SAINT KILDA ROAD", "SAINT KILDA", "VIC"),
                                              ("12 Smith St, Fakeville Qld. 4000", "SMITH STREET", "FAKEVILLE", "QLD"), ("12 Smith St, Fakeville Vic. 3000", "SMITH STREET", "FAKEVILLE", "VIC"),
                                              ("12 Smith St, Fakeville Act 2600", "SMITH STREET", "FAKEVILLE", "ACT"), ("12 Smith St, Fakeville WA 6000", "SMITH STREET", "FAKEVILLE", "WA")]:
        a = normalise_address(text)
        rows.append((("ordinary", text), (a.street_key, a.locality, a.state, "STATE_DOTS_REMOVED" in a.rules), (street_key, locality, state, False)))
    assert len(rows) > 500
    assert_table(rows)


def test_a_dotted_state_links_like_the_plain_state():
    """F5 (round 5). The record with `N.S.W.` links to the house the plain state links to, and is never a street called `SAINT INGOWICK SAINT CORREBY`."""
    rows = []
    for code, forms in DOTTED_STATES.items():
        if code != "NSW":
            continue
        for form in forms:
            d = decision(f"18 St Ingowick Street, Correby, {form} 2557", "", "", "")
            plain = decision("18 St Ingowick Street, Correby, NSW 2557", "", "", "")
            rows.append((("dotted", form), (d.outcome, d.rule, d.pid), (plain.outcome, plain.rule, plain.pid)))
    assert decision("18 St Ingowick Street, Correby, NSW 2557", "", "", "").pid == "HOUSE18"
    assert_table(rows)


SPLIT_STREETS = [("12 Smith St", "12", "SMITH", "STREET"), ("7 Station St", "7", "STATION", "STREET"), ("12 St Kilda Rd", "12", "SAINT KILDA", "ROAD"), ("40 Harbour View Ave", "40", "HARBOUR VIEW", "AVENUE")]
SPLIT_SUBURBS = [("Fakeville", "FAKEVILLE"), ("Mt Druitt", "MOUNT DRUITT"), ("Park Orchards", "PARK ORCHARDS"), ("St Albans", "SAINT ALBANS"), ("Dee Why", "DEE WHY")]


def test_class_a_one_line_address_that_ends_with_a_suburb_and_no_state_is_split_at_the_street_type():
    """CLASS-TABLE: F6 (round 5). `12 Smith St Fakeville` and `7 Station St Mt Druitt`, with no comma, no state and no postcode, are the street and the suburb, the same address as the comma form; St is the
    street type and never SAINT. A line that does not end in a suburb is left as it was."""
    rows = []
    for front, (street_text, number, street_name, street_type), (suburb, locality) in itertools.product(["", "Unit 4, ", "4/", "Unit 4 "], SPLIT_STREETS, SPLIT_SUBURBS):
        text = f"{front}{street_text} {suburb}"
        a = normalise_address(text)
        comma = normalise_address(f"{front}{street_text}, {suburb}")
        rows.append((("parts", text), (a.number_first, a.street_name, a.street_type, a.locality, a.state, a.postcode), (number, street_name, street_type, locality, "", "")))
        rows.append((("same as the comma form", text), a, comma))
        rows.append((("rule", text), ("SUBURB_SPLIT_HEURISTIC" in a.rules, "SUBURB_FROM_LINE" in a.rules), (True, True)))
        columns = normalise_address(text, "", "NSW", "2000")
        rows.append((("the state and postcode in columns", text), (columns.street_key, columns.locality, columns.state, columns.postcode), (f"{street_name} {street_type}", locality, "NSW", "2000")))
    # the ordinary inputs: nothing after the street type, a suffix, a unit word or a number after it, a street type last, a suburb given by a column
    for text, street_key in [("12 Smith St", "SMITH STREET"), ("12 Smith St North", "SMITH STREET NORTH"), ("12 Smith St Upper", "SMITH STREET UPPER"), ("12 Smith St N", "SMITH STREET NORTH"),
                             ("12 The Grove Road", "THE GROVE ROAD"), ("12 Smith Fakeville", "SMITH FAKEVILLE"), ("12 Smith Rd", "SMITH ROAD")]:
        a = normalise_address(text)
        rows.append((("ordinary", text), (a.locality, a.street_key, "SUBURB_SPLIT_HEURISTIC" in a.rules), ("", street_key, False)))
    for text in ["12 Smith St Level 3", "12 Smith St 3/4", "12 Smith St Station Rd", "12 Smith St Lot 5", "12 Smith St Shop", "12 Smith St Elizabeth Grove"]:          # no digit, no unit word first, no street type last: a last word that is a street type may be a second street, so a suburb such as Elizabeth Grove is not split without a state or postcode
        a = normalise_address(text)
        rows.append((("not a suburb", text), (a.locality, "SUBURB_SPLIT_HEURISTIC" in a.rules), ("", False)))
    for text in ["12 Smith St Fakeville", "7 Station St Mt Druitt"]:
        a = normalise_address(text, "Fakeville" if "Fakeville" in text else "Mt Druitt")
        rows.append((("suburb in a column", text), ("SUBURB_SPLIT_HEURISTIC" in a.rules, a.street_type), (False, "STREET")))
    assert_table(rows)


def test_a_one_line_address_with_a_suburb_and_no_state_links_like_the_comma_form():
    """F6 (round 5). The record written `18 St Ingowick Street Correby` links to the house the comma form links to, and a street that is not there is not linked."""
    rows = []
    for text in ["18 St Ingowick Street Correby", "4 St Ingowick Street Correby", "4/18 St Ingowick Street Correby", "Unit 5, 18 St Ingowick Street Correby"]:
        d = decision(text, "", "", "")
        comma = decision(text.replace("Street Correby", "Street, Correby"), "", "", "")
        rows.append((("same as the comma form", text), (d.outcome, d.rule, d.pid), (comma.outcome, comma.rule, comma.pid)))
    assert decision("18 St Ingowick Street Correby", "", "", "").pid == "HOUSE18"
    assert decision("4/18 St Ingowick Street Correby", "", "", "").pid == "FLAT4_IN_18"
    assert_table(rows)


LOT_IDS = [("5", "5"), ("12", "12"), ("7A", "7A"), ("07", "07")]
LOT_PLANS = ["", " DP 12345", " DP12345", ", DP 12345", ", DP12345", " dp 12345", " SP 5521", " LP 4", " RP 1234", " CP 9", " PS 7", "/DP 12345", " on", " ON", " on DP 12345"]


def test_class_a_lot_followed_by_a_plan_or_the_word_on_is_the_lot_and_the_plan_is_not_the_street():
    """CLASS-TABLE: F8 (round 5). `Lot 5 DP 12345, Smith Rd`, `Lot 5, DP12345`, `Lot 5 SP 5521`, `Lot 12 on Smith Rd` and `Lot 5 on DP 12345` are lot 5 or 12 of Smith Road, the same address as `Lot 5 Smith Rd`:
    the plan text never ends up in the street name."""
    rows = []
    for word, (ident, _), plan, (street_text, street_key), tail in itertools.product(["Lot", "LOT", "lot"], LOT_IDS, LOT_PLANS, [("Smith Rd", "SMITH ROAD"), ("Mount Pleasant Rd", "MOUNT PLEASANT ROAD")],
                                                                                    ["", ", Fakeville NSW 2000"]):
        text = f"{word} {ident}{plan} {street_text}{tail}"
        a = normalise_address(text)
        plain = normalise_address(f"{word} {ident} {street_text}{tail}")
        rows.append((("parts", text), (a.lot, a.number_first, a.street_key), (ident.upper(), "", street_key)))
        rows.append((("same as the plain lot", text), a, plain))
        rows.append((("rule", text), "LOT_PLAN_DROPPED" in a.rules, bool(plan)))
    for text in ["Lot 5 DP 12345, Smith Rd, Fakeville NSW 2000", "Lot 5, DP12345, Smith Rd", "Lot 5 on DP 12345, Smith Rd", "Lot 5 DP 12345 Smith Rd Fakeville NSW 2000"]:
        a = normalise_address(text)
        rows.append((("with commas", text), (a.lot, a.street_key, "LOT_PLAN_DROPPED" in a.rules), ("5", "SMITH ROAD", True)))
    # the ordinary inputs: a lot with a street, a lot and a number, a street whose name starts with on or sp, a plan word with no number behind it
    for text, lot, number, street_key in [("Lot 7 Smith Rd", "7", "", "SMITH ROAD"), ("Lot 7, Smith Road", "7", "", "SMITH ROAD"), ("Lot 12 Onslow Rd", "12", "", "ONSLOW ROAD"), ("Lot 5 Spring Rd", "5", "", "SPRING ROAD"),
                                          ("Lot 5 Dp Smith Rd", "5", "", "DP SMITH ROAD"), ("Lot 7, 12 Smith St", "7", "12", "SMITH STREET"), ("Lot 3 Ontario St", "3", "", "ONTARIO STREET"),
                                          ("Lot 5 Pse Rd", "5", "", "PSE ROAD")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.lot, a.number_first, a.street_key, "LOT_PLAN_DROPPED" in a.rules), (lot, number, street_key, False)))
    assert len(rows) > 700
    assert_table(rows)


def test_class_a_lot_with_a_plan_links_to_the_lot_and_a_different_lot_is_a_new_property():
    """CLASS-TABLE: F8 (round 5). G-NAF holds `Lot 5 Smith Rd`; the record with a plan or the word on links by EXACT_LOT, and lot 6 does not link to lot 5."""
    rows = []
    street = dict(street_name="SMITH", street_type="ROAD", locality="FAKEVILLE", state="NSW", postcode="2000")
    reference = [Candidate("LOT5", Addr(lot="5", **street), 1.0, True, None), Candidate("LOT9", Addr(lot="9", **street), 1.0, True, None)]
    for plan in LOT_PLANS:
        d = decide(normalise_address(f"Lot 5{plan} Smith Rd", *FAKE), None, None, reference)
        rows.append((("lot 5", plan), (d.outcome, d.rule, d.pid), ("link", "EXACT_LOT", "LOT5")))
        other = decide(normalise_address(f"Lot 6{plan} Smith Rd", *FAKE), None, None, reference)
        rows.append((("lot 6", plan), (other.outcome, other.rule), ("new", "NEW_DERIVED")))
    assert_table(rows)


def test_class_a_street_type_then_a_suffix_word_with_no_name_is_the_street_name_and_a_comma_state_or_postcode_splits_the_suburb():
    """CLASS-TABLE: the two lines the full normalise run of the last pass left alive. A suffix needs a name before the street type, so `Park Upper`
    is one street name (the suffix rule would otherwise leave a street with no name); and a state alone, a postcode alone or both after a comma
    mark where the suburb starts when no comma comes before them."""
    rows = []
    for suffix_word in ("Upper", "Lower"):
        a = normalise_address(f"5 Park {suffix_word}, Fakeville NSW 2000")
        rows.append((("no name", suffix_word), (a.street_name, a.street_type), (f"PARK {suffix_word.upper()}", "")))
        b = normalise_address(f"5 Smith St {suffix_word}, Fakeville NSW 2000")
        rows.append((("with name", suffix_word), (b.street_name, b.street_type), ("SMITH", f"STREET {suffix_word.upper()}")))
    for tail, state, postcode in ((", NSW", "NSW", ""), (", 2000", "", "2000"), (", NSW 2000", "NSW", "2000")):
        for suburb, want in (("Fakeville", "FAKEVILLE"), ("Mount Druitt", "MOUNT DRUITT"), ("Elizabeth Grove", "ELIZABETH GROVE"), ("Oak Park", "OAK PARK")):
            a = normalise_address(f"12 Smith St {suburb}{tail}")
            rows.append((("comma tail", suburb, tail), (a.street_name, a.street_type, a.locality, a.state, a.postcode), ("SMITH", "STREET", want, state, postcode)))
    assert_table(rows)
