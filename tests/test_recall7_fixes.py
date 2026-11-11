"""The seventh review (the final blind recall after the sixth) found seven classes: a level, lot or rear word after the street (F1), a corner marker after a numbered street (F2), any number left in
a street name minting a property (F3), a range number matched one way (F4), Nth, Sth, Upr, Lwr and Ext after a street type (F5), a suburb said twice with an abbreviation (F6) and a spaced dash between a
unit and the number (F7). Each is a generated table over the whole vocabulary of its class, with the ordinary input it must not break beside it. No test here needs a database: the link direction uses
`decide` on hand written candidates."""
from __future__ import annotations

import itertools

from parcelkey.block import Candidate
from parcelkey.gnaf_codes import STREET_SUFFIXES, STREET_TYPES
from parcelkey.match import decide
from parcelkey.normalise import (LEVEL_MARKERS, OTHER_LEVEL_WORDS, STREET_SUFFIX_WORDS, UNIT_MARKERS, Addr, is_unit_list, normalise_address, street_name_problem)

FAKE = ("Fakeville", "NSW", "2000")
STREETS = ("Smith St", "Smith Street", "Smith St North", "Pacific Hwy Nth", "St Kilda Rd", "Smith St Nth East")


def assert_table(rows):
    """rows are (label, got, want); every mismatch is listed at once."""
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, f"{len(bad)} of {len(rows)} rows differ, first: {bad[:5]}"


def cand(pid: str, number: str, last: str = "", unit: str = "", street: str = "SMITH", typ: str = "STREET", locality: str = "FAKEVILLE") -> Candidate:
    return Candidate(pid, Addr(unit=unit, number_first=number, number_last=last, street_name=street, street_type=typ, locality=locality, state="NSW", postcode="2000"), 1.0, True, None)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F1: a level, floor, lot or rear word written after the street is read the way the same word in front of the street is

AFTER_WORDS = ["Level 2", "Lvl 2", "L 2", "Floor 2", "Flr 2", "Fl 2", "Level G", "Level 02", "Ground Floor", "Ground Level", "Ground Flr", "Ground Fl", "2nd Floor", "1st Fl", "3rd Flr", "22nd Level",
               "Level Ground", "Basement 1", "Mezzanine 1", "Podium 2", "Lot 5", "Rear"]
TAILS = ["", ", Fakeville NSW 2000", " Fakeville NSW 2000", ", Fakeville"]


def test_class_f1_a_level_lot_or_rear_after_the_street_reads_like_the_same_word_before_it():
    """CLASS-TABLE: F1, a level, lot or rear word after the street. Every word, street, separator, tail and column shape gives the address the same word in front of the street gives."""
    rows = []
    for word, street, sep, tail, cols in itertools.product(AFTER_WORDS, STREETS, [", ", " "], TAILS, [FAKE, (None, None, None)]):
        after = normalise_address(f"12 {street}{sep}{word}{tail}", *cols)
        before = normalise_address(f"{word}, 12 {street}{tail}", *cols)
        label = (word, street, sep, tail, cols[0])
        rows.append((label, after, before))
        rows.append((("street name", *label), any(w in after.street_name.split() for w in ("LEVEL", "LOT", "REAR", "FLOOR", "GROUND")), False))
    for word, unit in itertools.product(AFTER_WORDS, ["", ", Unit 5", " Unit 5"]):
        a = normalise_address(f"12 Smith St, {word}{unit}", *FAKE)
        rows.append((("with a unit", word, unit), (a.unit, a.street_key), ("5" if unit else "", "SMITH STREET")))
    for word in AFTER_WORDS:
        rule = "LOT_AFTER_STREET" if word.startswith("Lot") else "REAR_AFTER_STREET" if word == "Rear" else "LEVEL_AFTER_STREET"
        a = normalise_address(f"12 Smith St, {word}", *FAKE)
        rows.append((("rules", word), (rule in a.rules, "LEADING_ZERO_DROPPED" in a.rules), (True, word.endswith("02"))))
        rows.append((("twice", word), normalise_address(f"{word}, 12 Smith St, {word}", *FAKE), normalise_address(f"{word}, 12 Smith St", *FAKE)))          # the same one twice is read once
        if word != "Rear":          # a second, different one is not guessed between: it stays in the street name and the street is refused
            front = "Lot 9" if word.startswith("Lot") else "Level 9"
            b = normalise_address(f"{front}, 12 Smith St, {word}", *FAKE)
            rows.append((("different", word), (b.level or b.lot, bool(street_name_problem(b.street_name))), ("9", True)))
    for control in ("12 Smith St, Suite 5", "12 Smith St Level Crossing Rd", "12 Smith St Lotus Court"):
        rows.append((("control", control), normalise_address(control, *FAKE).number_first, "12"))
    assert len(rows) > 1000
    assert_table(rows)


def test_f1_the_level_lot_and_rear_after_the_street_are_the_parts_and_a_different_one_stays_put():
    a = normalise_address("12 Smith St, Level 2", *FAKE)
    assert (a.level, a.street_key, "LEVEL_AFTER_STREET" in a.rules) == ("2", "SMITH STREET", True)
    assert normalise_address("Smith Rd, Lot 5", *FAKE).lot == "5"
    assert normalise_address("12 Smith St, Rear", *FAKE).building == "REAR"
    twice = normalise_address("Level 3, 12 Smith St, Level 3", *FAKE)
    assert (twice.level, twice.street_key) == ("3", "SMITH STREET")
    other = normalise_address("Level 3, 12 Smith St, Level 4", *FAKE)          # a second, different level is not guessed between: it stays in the street, and the match stage sets the record aside
    assert other.level == "3" and "4" in other.street_name.split()
    assert decide(other, None, None, []).reason == "UNPARSEABLE"


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F2: a corner marker after a numbered street drops the second street

CORNER_MARKERS = ["cnr", "Cnr", "CNR.", "corner", "Corner of", "cor", "crnr", "&", "and", "AND"]
SECOND_STREETS = ["Jones Rd", "Jones Street", "Jones Cres", "Mt Pleasant Rd", "Park Gdns", "Elizabeth Pde"]


def test_class_f2_a_corner_marker_after_a_numbered_street_drops_the_second_street():
    """CLASS-TABLE: F2, a corner marker after a numbered street. Every marker, first and second street and tail keeps the number and the first street and records CORNER_DROPPED."""
    rows = []
    for marker, first, second, tail, cols in itertools.product(CORNER_MARKERS, ["Smith St", "Smith Street North"], SECOND_STREETS, TAILS, [FAKE, (None, None, None)]):
        a = normalise_address(f"12 {first} {marker} {second}{tail}", *cols)
        rows.append(((marker, first, second, tail, cols[0]), (a, "CORNER_DROPPED" in a.rules), (normalise_address(f"12 {first}{tail}", *cols), True)))
    for suffix_tail in ("", ", Fakeville NSW 2000"):          # a suffix after the second street goes with it when it ends the words or a comma follows
        a = normalise_address(f"12 Smith St cnr Jones Rd North{suffix_tail}", *FAKE)
        rows.append((("suffix", suffix_tail), (a.street_key, a.number_first), ("SMITH STREET", "12")))
    for control in ("12 Black And White Rd", "12 Corner Inlet Rd", "12 Smith St Corner Brook", "12 Dick & Fanny Rd"):
        rows.append((("control", control), "CORNER_DROPPED" in normalise_address(control, *FAKE).rules, False))
    rows.append((("ampersand in a name", "12 Dick & Fanny Rd"), normalise_address("12 Dick & Fanny Rd", *FAKE).street_name, "DICK FANNY"))
    assert len(rows) > 300
    assert_table(rows)


def test_f2_a_corner_address_links_to_the_first_street_and_never_makes_a_property():
    reference = [cand("HOUSE12", "12")]
    for text in ("12 Smith St cnr Jones Rd", "12 Smith St & Jones Rd", "12 Smith St Corner of Jones Rd Fakeville NSW 2000"):
        d = decide(normalise_address(text, *FAKE), None, None, reference)
        assert (d.outcome, d.rule, d.pid) == ("link", "EXACT_KEY", "HOUSE12"), text
    for text in ("12 Smith St cnr Jones", "12 Smith St & Jones"):          # no second street type to end it at: the corner word stays and no property is made
        d = decide(normalise_address(text, *FAKE), None, None, [])
        assert (d.outcome, d.reason) == ("quarantine", "UNPARSEABLE"), text


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F3: a number left in a street name can never be minted as a property

LIST_WORDS = sorted(UNIT_MARKERS - {"#", "U", "TH"})
LIST_FORMS = [("{w} {a} & {b}", False), ("{w} {a}&{b}", False), ("{w} {a} and {b}", False), ("{w} {a} to {b}", False), ("{w} {a}-{b}", False), ("{w}s {a} - {b}", True), ("{w}s {a}-{b}", True),
              ("{w}s {a} & {b}", True)]


def test_class_f3_a_unit_written_as_a_list_or_a_range_is_one_key_and_is_set_aside():
    """CLASS-TABLE: F3, a unit written as a list or a range. Every unit word, id pair and separator is the key `3-4` with the house number read, and the record is set aside, never made a property."""
    reference = [cand("FLAT3", "12", unit="3"), cand("FLAT4", "12", unit="4"), cand("BUILDING", "12")]
    rows = []
    for word, (form, _), (a, b), line_tail in itertools.product(LIST_WORDS, LIST_FORMS, [("3", "4"), ("1", "3"), ("30", "40"), ("G1", "G2"), ("5A", "5B")], ["{}, 12 Smith St", "{} 12 Smith St"]):
        text = line_tail.format(form.format(w=word.title(), a=a, b=b))
        got = normalise_address(text, *FAKE)
        key = f"{a}-{b}"
        rows.append(((word, form, a, b, line_tail), (got.unit, got.number_first, got.street_key, is_unit_list(got.unit), "UNIT_LIST_READ" in got.rules), (key, "12", "SMITH STREET", True, True)))
        d = decide(got, None, None, reference)
        rows.append((("decision", word, form, a, b), (d.outcome, d.reason), ("quarantine", "UNPARSEABLE")))
    for text in ("Unit 3, 12 Smith St", "Unit 3 12 Smith St", "3/12 Smith St", "Unit 12-05, 100 Smith St"):
        rows.append((("control", text), is_unit_list(normalise_address(text, *FAKE).unit), False))
    assert len(rows) > 1000
    assert_table(rows)


def test_class_f3_every_level_word_is_read_in_front_of_the_street_with_its_number():
    """CLASS-TABLE: F3, Fl 3 and the other level words. Every level word, written in front of the street, is the level and not a number that ends up in the street name."""
    rows = []
    for marker, ident, sep in itertools.product(sorted(LEVEL_MARKERS | set(OTHER_LEVEL_WORDS)), ["3", "03"], [", ", " "]):
        a = normalise_address(f"{marker.title()} {ident}{sep}12 Smith St", *FAKE)
        rows.append(((marker, ident, sep), (a.number_first, a.street_key, bool(a.level)), ("12", "SMITH STREET", True)))
    assert "FL" in LEVEL_MARKERS
    assert_table(rows)


def test_class_f3_a_street_name_with_a_number_or_a_corner_word_is_never_minted_and_still_links_when_g_naf_lists_it():
    """CLASS-TABLE: F3, the guard in the match stage. Every place a number can stand in a street name, for every ordinary type, is set aside as UNPARSEABLE; the same name that G-NAF lists links."""
    rows = []
    for name, typ, number in itertools.product(["14 SMITH", "SMITH 14", "14A SMITH", "SMITH 5 EAST", "SMITH LEVEL 2", "SMITH LOT 5", "SMITH CNR JONES", "SMITH CORNER JONES", "SMITH LEVEL G", "SMITH FLOOR GROUND"], ["STREET", "ROAD", ""], ["6", "12"]):
        rec = Addr(number_first=number, street_name=name, street_type=typ, locality="FAKEVILLE", state="NSW")
        d = decide(rec, None, None, [])
        rows.append((("new", name, typ, number), (d.outcome, d.reason, bool(street_name_problem(name))), ("quarantine", "UNPARSEABLE", True)))
    for name in ("SMITH", "SAINT KILDA", "CORNER INLET", "MOUNT PLEASANT", "ROUTE", "VITAMIN G", "FLOOR MILL"):
        rec = Addr(number_first="12", street_name=name, street_type="ROAD", locality="FAKEVILLE", state="NSW")
        rows.append((("ordinary", name), (decide(rec, None, None, []).outcome, street_name_problem(name)), ("new", "")))
    listed = Addr(number_first="12", street_name="ROUTE 66", street_type="ROAD", locality="FAKEVILLE", state="NSW", postcode="2000")
    rows.append((("listed", "ROUTE 66"), decide(listed, None, None, [Candidate("G", listed, 1.0, True, None)]).outcome, "link"))
    assert_table(rows)


def test_f3_six_fourteen_smith_street_is_set_aside_and_not_read_as_unit_six_of_fourteen():
    # `6 14 Smith St` could be unit 6 of number 14 or a typing slip: with no unit word nothing says which, so the record is set aside and no flat is guessed
    a = normalise_address("6 14 Smith St", *FAKE)
    assert (a.unit, a.number_first, a.street_name) == ("", "6", "14 SMITH")
    assert decide(a, None, None, [cand("FLAT6", "14", unit="6")]).outcome == "quarantine"


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F4: a number inside a G-NAF range links to that range the way the range end does

RANGES = [("12", "14"), ("14", "20"), ("1", "3"), ("20", "26"), ("7", "9"), ("100", "110")]


def _expected_for_single(n: int, first: int, last: int) -> tuple:
    if n in (first, last):
        return "link", "EXACT_KEY", "RANGE", ("NUMBER_RANGE_END",)
    if first < n < last and (n - first) % 2 == 0:
        return "quarantine", "AMBIGUOUS_TIE", "", ()
    return "new", "NEW_DERIVED", "", ()


def _record(number: str, last: str = "") -> Addr:
    return Addr(number_first=number, number_last=last, street_name="SMITH", street_type="STREET", locality="FAKEVILLE", state="NSW", postcode="2000")


def test_class_f4_a_street_number_that_is_an_end_of_a_g_naf_range_links_to_it_and_one_inside_is_set_aside():
    """CLASS-TABLE: F4, a range number matched one way. For every range, every number from three below to three above: an end links, a number inside on the same side is set aside, any other is new."""
    rows = []
    for (first, last), n in ((r, n) for r in RANGES for n in range(int(r[0]) - 3, int(r[1]) + 4)):
        d = decide(_record(str(n)), None, None, [cand("RANGE", first, last), cand("OTHER", str(int(last) + 500))])
        rows.append(((first, last, n), (d.outcome, d.rule, d.pid, d.rules), _expected_for_single(n, int(first), int(last))))
        elsewhere = cand("RANGE", first, last, locality="OTHERVILLE")          # the same range in another suburb of the same postcode: an end is a locality conflict, anything else is new
        d = decide(_record(str(n)), None, None, [elsewhere])
        rows.append((("other suburb", first, last, n), (d.outcome, d.reason), ("quarantine", "LOCALITY_CONFLICT") if n in (int(first), int(last)) else ("new", "")))
    for (first, last), (a, b) in itertools.product(RANGES, [(0, 0), (0, 1), (1, 0), (1, 1)]):          # a record that is itself a range, against singles at its ends and the range itself
        ends = [("S" + first, first), ("S" + last, last)]
        singles = [cand(pid, number) for k, (pid, number) in enumerate(ends) if (a, b)[k]]
        d = decide(_record(first, last), None, None, singles)
        want = ("new", "NEW_DERIVED", ()) if not singles else ("quarantine", "AMBIGUOUS_TIE", ()) if len(singles) == 2 else ("link", "EXACT_KEY", ("NUMBER_RANGE_END",))
        rows.append((("range record", first, last, a, b), (d.outcome, d.rule, d.rules), want))
        d = decide(_record(first, last), None, None, [*singles, cand("RANGE", first, last)])
        rows.append((("range record and the range", first, last, a, b), (d.outcome, d.pid, d.rules), ("link", "RANGE", ())))          # the very same range wins over singles at its ends
    for first, last, number in [("12A", "14", "14"), ("12", "18B", "14"), ("12", "18", "14A"), ("12A", "18", "14"), ("12", "18", "12A")]:          # a letter on either side: ends still count, the inside never does
        d = decide(_record(number), None, None, [cand("RANGE", first, last)])
        rows.append((("letters", first, last, number), (d.outcome, d.rule), ("link", "EXACT_KEY") if number in (first, last) else ("new", "NEW_DERIVED")))
    for (first, last), (other_first, other_last) in itertools.permutations([("12", "14"), ("14", "20"), ("20", "26")], 2):          # two ranges that share only an end are two addresses
        d = decide(_record(first, last), None, None, [cand("OTHER", other_first, other_last)])
        rows.append((("ranges that touch", first, last, other_first, other_last), (d.outcome, d.rule), ("new", "NEW_DERIVED") if (first, last) != (other_first, other_last) else ("link", "EXACT_KEY")))
    kinds = {r[2][0] for r in rows if isinstance(r[2], tuple) and r[2]}
    assert len(rows) > 150 and {"link", "quarantine", "new"} <= kinds
    assert_table(rows)


def test_f4_a_single_number_prefers_the_address_with_exactly_that_number():
    reference = [cand("RANGE", "12", "14"), cand("SINGLE14", "14")]
    d = decide(Addr(number_first="14", street_name="SMITH", street_type="STREET", locality="FAKEVILLE", state="NSW"), None, None, reference)
    assert (d.outcome, d.pid) == ("link", "SINGLE14") and d.rules == ()


def test_f4_a_range_record_is_a_tie_between_two_single_addresses_and_links_to_one_range_address():
    record = Addr(number_first="12", number_last="14", street_name="SMITH", street_type="STREET", locality="FAKEVILLE", state="NSW")
    two = decide(record, None, None, [cand("S12", "12"), cand("S14", "14")])
    assert (two.outcome, two.reason) == ("quarantine", "AMBIGUOUS_TIE") and {c["pid"] for c in two.detail["candidates"]} == {"S12", "S14"}
    one = decide(record, None, None, [cand("S12", "12")])
    assert (one.outcome, one.pid, one.rules) == ("link", "S12", ("NUMBER_RANGE_END",))          # a link that rests on one end of the range says so
    exact = decide(record, None, None, [cand("S14", "14"), cand("RANGE", "12", "14")])
    assert (exact.outcome, exact.pid, exact.rules) == ("link", "RANGE", ())          # an address with the very same range wins over a single number at one end
    other = decide(Addr(number_first="14", street_name="SMITH", street_type="STREET", locality="FAKEVILLE", state="NSW"), None, None, [cand("R", "12", "14", unit="5")])
    assert (other.outcome, other.reason) == ("quarantine", "UNIT_CONFLICT")          # the end rule never gets past the unit rule


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F5: Nth, Sth, Upr, Lwr, Ext and Extn after a street type are a street suffix

SUFFIX_SPELLINGS = ["Nth", "Sth", "Upr", "Lwr", "Ext", "Extn", "Upper", "Lower", "Extension", "North", "N", "Nth East", "Sth West", "Nth West", "Sth East", "North East", "SW"]
SUFFIX_TYPES = sorted(code for code in STREET_TYPES if code.isalpha() and code not in STREET_SUFFIXES.values() and code not in ("NORTH", "SOUTH", "EAST", "WEST", "EXTENSION", "CORNER"))


def test_class_f5_a_short_or_long_suffix_after_a_street_type_is_the_suffix_and_the_type_is_still_expanded():
    """CLASS-TABLE: F5, Nth, Sth, Upr, Lwr and Ext after a street type. Every G-NAF street type, in its code and short spelling, with every suffix spelling, is name, full type and full suffix."""
    rows = []
    for code, spelling in itertools.product(SUFFIX_TYPES, SUFFIX_SPELLINGS):
        for written in {code, STREET_TYPES[code]}:
            a = normalise_address(f"12 Smith {written.title()} {spelling}", *FAKE)
            rows.append(((code, written, spelling), (a.street_name, a.street_type), ("SMITH", f"{code} {STREET_SUFFIX_WORDS[spelling.upper()]}")))
    for spelling, tail in itertools.product(SUFFIX_SPELLINGS, [", Fakeville NSW 2000", ", Fakeville"]):
        a = normalise_address(f"12 Pacific Hwy {spelling}{tail}", *FAKE)
        rows.append((("tail", spelling, tail), (a.street_name, a.street_type), ("PACIFIC", f"HIGHWAY {STREET_SUFFIX_WORDS[spelling.upper()]}")))
    for control, want in (("12 Smith Nth", ("SMITH", "NORTH")), ("12 Upper Rd", ("UPPER", "ROAD")), ("12 North Rd", ("NORTH", "ROAD")), ("12 Smith Extn", ("SMITH", "EXTENSION"))):
        a = normalise_address(control, *FAKE)
        rows.append((("control", control), (a.street_name, a.street_type), want))
    assert len(rows) > 3000
    assert_table(rows)


def test_f5_the_same_street_in_every_spelling_is_one_derived_key():
    keys = {normalise_address(f"12 {text}", *FAKE).canon_key for text in ("Pacific Hwy Nth", "Pacific Highway North", "Pacific Hwy N", "PACIFIC HWY NORTH")}
    assert len(keys) == 1 and not any(Addr().street_suffix for _ in keys)
    a = normalise_address("12 Pacific Hwy Nth", *FAKE)
    assert a.street_suffix == "NORTH" and a.street_base == "PACIFIC HIGHWAY"


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F6: a suburb said in the line and in the column is said once, compared after Mt, St, Nth, Sth and Pt are written out

SUBURBS = [("Mt Druitt", "Mount Druitt"), ("Mount Druitt", "Mt Druitt"), ("St Marys", "Saint Marys"), ("Saint Marys", "St Marys"), ("Nth Sydney", "North Sydney"), ("North Sydney", "Nth Sydney"),
           ("Sth Yarra", "South Yarra"), ("Pt Melbourne", "Port Melbourne"), ("Fakeville", "Fakeville"), ("Mt Druitt", "Mt Druitt")]


def test_class_f6_a_suburb_repeated_in_the_line_is_removed_from_the_street_whatever_its_spelling():
    """CLASS-TABLE: F6, the repeated suburb after the expansions. Every suburb pair, street with or without a suffix, and tail shape leaves the street alone and the suburb written out once."""
    rows = []
    for (line_sub, col_sub), street, tail in itertools.product(SUBURBS, ["Smith St", "Smith Street", "Smith St North", "Smith St N", "Smith St Nth", "Smith Street Sth"], [" {}", " {} NSW 2770", ", {}", ", {} NSW 2770"]):
        a = normalise_address(f"12 {street}{tail.format(line_sub)}", col_sub, "NSW", "2770")
        want = normalise_address(f"12 {street}", col_sub, "NSW", "2770")
        rows.append(((line_sub, col_sub, street, tail), (a.street_name, a.street_type, a.locality, a.number_first), (want.street_name, want.street_type, want.locality, "12")))
    for other in ("Newtown", "Kings Cross"):          # a different suburb in the line is not removed: the column wins and nothing is guessed away
        a = normalise_address(f"12 Smith St {other}", "Fakeville", "NSW", "2000")
        rows.append((("different", other), a.locality, "FAKEVILLE"))
    assert len(rows) > 200
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F7: a dash with a space on at least one side between a unit, level or lot and the number is a separator

def test_class_f7_a_spaced_dash_between_a_unit_word_and_the_house_number_is_the_slash():
    """CLASS-TABLE: F7, the spaced dash. Every unit, level and lot word, id, dash spacing and number reads the same as the comma form, and the tight dash stays a range of units."""
    words = sorted(UNIT_MARKERS - {"#"}) + sorted(LEVEL_MARKERS) + ["Lot", "Basement"]
    rows = []
    for word, ident, dash, number in itertools.product(words, ["5", "05", "5A", "G1"], [" - ", " -", "- "], ["12", "12-14", "12A", "100"]):
        if word in LEVEL_MARKERS or word in ("Basement", "Lot"):          # a level or lot id has no letter prefix
            ident = ident.lstrip("G") or ident
        a = normalise_address(f"{word.title()} {ident}{dash}{number} Smith St", *FAKE)
        b = normalise_address(f"{word.title()} {ident}, {number} Smith St", *FAKE)
        rows.append(((word, ident, dash, number), (a, "UNIT_DASH_SPACED" in a.rules, a.number_first != ""), (b, True, True)))
    a = normalise_address("#5 - 12 Smith St", *FAKE)
    rows.append((("hash",), (a.unit, a.number_first), ("5", "12")))
    rows.append((("tight dash", "Unit 5-12"), is_unit_list(normalise_address("Unit 5-12, 40 Smith St", *FAKE).unit), True))
    assert len(rows) > 1000
    assert_table(rows)
