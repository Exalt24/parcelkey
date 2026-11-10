"""Normalisation: every spelling of a unit, a street type, a state, a lot and a phone, with the rules that must fire for each."""
from __future__ import annotations

import itertools

import pytest
from hypothesis import given, strategies as st

from parcelkey.gnaf_codes import FLAT_TYPES, LEVEL_TYPES, STREET_SUFFIXES, STREET_TYPES
from parcelkey.match import units_conflict
from parcelkey.normalise import (DISTINCT_FLAT_TYPES, LEVEL_MARKERS, OTHER_FLAT_WORDS, OTHER_LEVEL_WORDS, STATE_NAMES, STATES, STREET_SUFFIX_WORDS, STREET_TYPE_SPELLINGS, UNIT_MARKERS, Addr,
                                 addr_from_gnaf, canonical_street_type, common_street_type, gnaf_display_line, gnaf_part, normalise_address, normalise_email, normalise_name, normalise_phone)

FAKE = ("Fakeville", "NSW", "2000")


def parts(a: Addr):
    return (a.unit, a.level, a.lot, a.number_first, a.number_last, a.street_name, a.street_type, a.locality, a.state, a.postcode)


UNIT_FORMS = [
    # text, unit, level, number, rules that must be present
    ("Unit 5, 12 Smith St", "5", "", "12", {"UNIT_MARKER_WORD"}),
    ("5/12 Smith St", "5", "", "12", {"UNIT_SLASH_FORM"}),
    ("Apt 5 12 Smith Street", "5", "", "12", {"UNIT_MARKER_WORD"}),
    ("U5/12 Smith St", "5", "", "12", {"UNIT_MARKER_GLUED", "UNIT_SLASH_FORM"}),
    ("Flat 5, 12 Smith St", "5", "", "12", {"UNIT_MARKER_WORD"}),
    ("Apartment 5, 12 Smith St", "5", "", "12", {"UNIT_MARKER_WORD"}),
    ("#5, 12 Smith St", "5", "", "12", {"UNIT_MARKER_GLUED"}),
    ("# 5 12 Smith St", "5", "", "12", {"UNIT_MARKER_WORD"}),
    ("Level 3 Suite 7, 12 Smith St", "7", "3", "12", {"LEVEL_MARKER", "UNIT_MARKER_WORD"}),
    ("Suite 7, Level 3, 12 Smith St", "7", "3", "12", {"LEVEL_MARKER", "UNIT_MARKER_WORD"}),
    ("Shop 3, 12 Smith St", "3", "", "12", {"UNIT_MARKER_WORD"}),
    ("Unit 5A, 12 Smith St", "5A", "", "12", {"UNIT_MARKER_WORD"}),
    ("5A/12 Smith St", "5A", "", "12", {"UNIT_SLASH_FORM"}),
    ("Unit 5, 12-14 Smith St", "5", "", "12", {"UNIT_MARKER_WORD"}),
    ("Level G Unit 2, 12 Smith St", "2", "G", "12", {"LEVEL_MARKER"}),
]


@pytest.mark.parametrize("text,unit,level,number,rules", UNIT_FORMS)
def test_unit_and_level_forms(text, unit, level, number, rules):
    a = normalise_address(text, *FAKE)
    assert (a.unit, a.level, a.number_first) == (unit, level, number)
    assert a.street_name == "SMITH" and a.street_type == "STREET"
    assert rules <= set(a.rules), a.rules


@pytest.mark.parametrize("marker", sorted(UNIT_MARKERS))
def test_every_unit_marker_word(marker):
    a = normalise_address(f"{marker.title()} 5, 12 Smith St", *FAKE)
    assert (a.unit, a.number_first, a.street_name) == ("5", "12", "SMITH") and "UNIT_MARKER_WORD" in a.rules
    glued = normalise_address(f"{marker.title()}5/12 Smith St", *FAKE)
    assert glued.number_first == "12" and glued.street_name == "SMITH"
    assert (glued.unit, "UNIT_MARKER_GLUED" in glued.rules) in {("5", True), ("5", False)}


@pytest.mark.parametrize("marker", sorted(LEVEL_MARKERS))
def test_every_level_marker_word(marker):
    a = normalise_address(f"{marker.title()} 3 Unit 5, 12 Smith St", *FAKE)
    assert (a.level, a.unit, a.number_first) == ("3", "5", "12") and "LEVEL_MARKER" in a.rules
    g = normalise_address(f"{marker.title()} Ground Unit 5, 12 Smith St", *FAKE)
    assert g.level == "G"


def test_range_number_is_kept_whole():
    a = normalise_address("12-14 Smith St", *FAKE)
    assert (a.number_first, a.number_last, a.number_key) == ("12", "14", "12-14")
    b = normalise_address("5/12-14 Smith St", *FAKE)
    assert (b.unit, b.number_key) == ("5", "12-14")


@pytest.mark.parametrize("spelling,code", sorted(STREET_TYPE_SPELLINGS.items()))
def test_every_street_type_spelling(spelling, code):
    a = normalise_address(f"12 Smith {spelling.title()}", *FAKE)
    assert a.street_type == code and a.street_name == "SMITH"
    if spelling != code:
        assert "STREET_TYPE_EXPANDED" in a.rules
    else:
        assert "STREET_TYPE_EXPANDED" not in a.rules


def test_street_type_lookup():
    assert canonical_street_type("rd") == "ROAD" and canonical_street_type("nonsense") == ""


def test_mount_and_saint_everywhere_they_can_appear():
    a = normalise_address("3 Mt Pleasant Rd", "St Albans", "VIC", "3021")
    assert (a.street_name, a.locality) == ("MOUNT PLEASANT", "SAINT ALBANS")
    assert {"MOUNT_EXPANDED", "SAINT_EXPANDED"} <= set(a.rules)
    b = normalise_address("3 St Kilda Rd", "Mount Eliza", "VIC", "3930")
    assert (b.street_name, b.street_type, b.locality) == ("SAINT KILDA", "ROAD", "MOUNT ELIZA")
    c = normalise_address("9 Smith St", "St Ives", "NSW", "2075")
    assert c.street_type == "STREET" and c.street_name == "SMITH" and c.locality == "SAINT IVES"


def test_st_means_street_at_the_end_and_saint_at_the_start():
    a = normalise_address("St Albans St, Mt Eliza VIC 3930")
    assert (a.street_name, a.street_type, a.locality) == ("SAINT ALBANS", "STREET", "MOUNT ELIZA")


@pytest.mark.parametrize("text", ["12 smith st", "12 SMITH ST", "12 Smith St.", "  12   Smith   St  ", "12 Smith St,", "12 SMITH STREET"])
def test_case_and_punctuation_do_not_change_the_result(text):
    assert parts(normalise_address(text, *FAKE)) == ("", "", "", "12", "", "SMITH", "STREET", "FAKEVILLE", "NSW", "2000")


def test_rules_record_case_and_punctuation():
    a = normalise_address("12 smith st.", *FAKE)
    assert {"CASE_FOLDED", "PUNCT_STRIPPED"} <= set(a.rules)
    upper = ("FAKEVILLE", "NSW", "2000")
    assert "CASE_FOLDED" not in normalise_address("12 SMITH STREET", *upper).rules
    assert "WHITESPACE_COLLAPSED" in normalise_address("12   SMITH STREET", *upper).rules
    assert "PUNCT_STRIPPED" not in normalise_address("12 SMITH STREET", *upper).rules


def test_apostrophes_are_dropped_and_hyphens_become_spaces():
    a = normalise_address("7 O'Connell St", *FAKE)
    assert a.street_name == "OCONNELL"
    assert normalise_address("7 Blue-Hill Rd", *FAKE).street_name == "BLUE HILL"


def test_lot_only_address():
    a = normalise_address("Lot 7 Smith Rd", *FAKE)
    assert (a.lot, a.number_first, a.street_name, a.street_type) == ("7", "", "SMITH", "ROAD")
    assert "LOT_MARKER" in a.rules


def test_building_name_with_and_without_a_comma():
    a = normalise_address("Skyline Tower, Unit 5, 12 Smith St", *FAKE)
    assert (a.building, a.unit, a.number_first) == ("SKYLINE TOWER", "5", "12")
    b = normalise_address("Skyline Tower. Unit 5. 12 Smith St.", *FAKE)
    assert (b.building, b.unit, b.number_first) == ("SKYLINE TOWER", "5", "12")
    assert "BUILDING_NAME_PREFIX" in a.rules and "BUILDING_NAME_PREFIX" in b.rules
    assert normalise_address("Smith St", *FAKE).building == ""


def test_tail_of_a_single_line_address():
    a = normalise_address("5/12 Smith St, Fakeville NSW 2000")
    assert parts(a) == ("5", "", "", "12", "", "SMITH", "STREET", "FAKEVILLE", "NSW", "2000")
    assert "SUBURB_FROM_LINE" in a.rules
    b = normalise_address("12 Smith St Fakeville NSW 2000")
    assert (b.street_name, b.locality, b.state, b.postcode) == ("SMITH", "FAKEVILLE", "NSW", "2000")
    assert {"SUBURB_FROM_LINE", "SUBURB_SPLIT_HEURISTIC"} <= set(b.rules)
    c = normalise_address("12 Smith St, Fakeville")
    assert (c.locality, c.state, c.postcode) == ("FAKEVILLE", "", "")
    assert {"POSTCODE_MISSING", "STATE_MISSING"} <= set(c.rules)


def test_a_street_part_alone_gets_no_invented_tail():
    a = normalise_address("Unit 5, 12 Smith St")
    assert (a.locality, a.state, a.postcode) == ("", "", "")
    assert {"SUBURB_MISSING", "POSTCODE_MISSING", "STATE_MISSING"} <= set(a.rules)


@pytest.mark.parametrize("name,code", sorted(STATE_NAMES.items()))
def test_full_state_names(name, code):
    a = normalise_address("12 Smith St", "Fakeville", name.title(), "2000")
    assert a.state == code and "STATE_NAME_TO_CODE" in a.rules
    b = normalise_address(f"12 Smith St, Fakeville {name.title()} 2000")
    assert b.state == code


@pytest.mark.parametrize("state", STATES)
def test_state_codes_in_any_case(state):
    assert normalise_address("12 Smith St", "Fakeville", state.lower(), "2000").state == state


@pytest.mark.parametrize("pc,kept", [("2000", "2000"), ("200", ""), ("20000", ""), ("abcd", ""), ("", ""), (None, "")])
def test_postcode_shape(pc, kept):
    a = normalise_address("12 Smith St", "Fakeville", "NSW", pc)
    assert a.postcode == kept
    if pc and not kept:
        assert "POSTCODE_BAD_SHAPE" in a.rules
    if not kept:
        assert "POSTCODE_MISSING" in a.rules


def test_canonical_key_ignores_postcode_and_spelling():
    a = normalise_address("Unit 5, 12 Smith St", "Fakeville", "NSW", "2000")
    b = normalise_address("5/12 SMITH STREET", "FAKEVILLE", "nsw", "")
    c = normalise_address("U5/12 smith st.", "Fakeville", "NSW", "9999")
    assert a.canon_key == b.canon_key == c.canon_key
    assert a.canon_key != normalise_address("Unit 6, 12 Smith St", *FAKE).canon_key
    assert a.canon_key != normalise_address("Unit 5, 12 Smith Rd", *FAKE).canon_key


def test_addr_equality_ignores_rules():
    assert normalise_address("12 smith st", *FAKE) == normalise_address("12 Smith Street", *FAKE)


def test_address_from_gnaf_row_uses_the_same_vocabulary():
    row = dict(street_name="MOUNT PLEASANT", street_type_code="STREET", locality_name="SAINT ALBANS", state_abbreviation="VIC", postcode="3021", flat_number=5, flat_number_suffix="A",
               level_number=3, number_first=12, number_last=14, lot_number=None, building_name="Skyline Tower")
    a = addr_from_gnaf(row)
    assert parts(a) == ("5A", "3", "", "12", "14", "MOUNT PLEASANT", "STREET", "SAINT ALBANS", "VIC", "3021")
    assert a.building == "SKYLINE TOWER"
    b = addr_from_gnaf(dict(street_name="ST KILDA", street_type_code="ROAD", locality_name="MT ELIZA", state_abbreviation="VIC", postcode="3930", number_first=9))
    assert (b.street_name, b.locality) == ("SAINT KILDA", "MOUNT ELIZA")
    assert addr_from_gnaf(dict(street_name="X", street_type_code="LOOP", locality_name="Y", state_abbreviation="WA", lot_number="12")).street_type == "LOOP"      # an unknown type code is kept as it is


def test_line_renders_what_it_parsed():
    a = normalise_address("Skyline Tower, Level 3 Unit 5, 12-14 Smith St", *FAKE)
    assert a.line() == "SKYLINE TOWER, LEVEL 3 UNIT 5 12-14 SMITH STREET, FAKEVILLE NSW 2000"
    assert normalise_address("Lot 7 Smith Rd", *FAKE).line() == "LOT 7 SMITH ROAD, FAKEVILLE NSW 2000"


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# phones and emails

@pytest.mark.parametrize("raw,e164,rules", [
    ("0412 345 678", "+61412345678", {"PHONE_TRUNK_ZERO_STRIPPED", "PHONE_E164"}),
    ("+61 412 345 678", "+61412345678", {"PHONE_COUNTRY_CODE_STRIPPED", "PHONE_E164"}),
    ("(04) 1234 5678", "+61412345678", {"PHONE_TRUNK_ZERO_STRIPPED"}),
    ("04-1234-5678", "+61412345678", set()),
    ("0412345678", "+61412345678", set()),
    ("61412345678", "+61412345678", {"PHONE_COUNTRY_CODE_STRIPPED"}),
    ("0061 412 345 678", "+61412345678", {"PHONE_COUNTRY_CODE_STRIPPED"}),
    ("+61 (0)412 345 678", "+61412345678", {"PHONE_COUNTRY_CODE_STRIPPED", "PHONE_TRUNK_ZERO_STRIPPED"}),
    ("02 5550 1234", "+61255501234", {"PHONE_TRUNK_ZERO_STRIPPED"}),
    ("(02) 5550 1234", "+61255501234", set()),
    ("+61 2 5550 1234", "+61255501234", {"PHONE_COUNTRY_CODE_STRIPPED"}),
    ("0755501234", "+61755501234", set()),
    ("08 5550-1234", "+61855501234", set()),
])
def test_phone_forms(raw, e164, rules):
    n, fired = normalise_phone(raw)
    assert n == e164
    assert rules <= set(fired) and "PHONE_E164" in fired


@pytest.mark.parametrize("raw", ["5550 1234", "1300 123 456", "1800 000 000", "000", "abc", "04123", "+44 20 7946 0958", "0512345678", "tbc", "n/a"])
def test_phone_that_is_not_an_australian_number_is_not_guessed(raw):
    assert normalise_phone(raw) == ("", ("PHONE_INVALID",)) or normalise_phone(raw)[0] == ""
    assert "PHONE_INVALID" in normalise_phone(raw)[1]


def test_blank_phone_and_email_fire_nothing():
    assert normalise_phone("") == ("", ()) and normalise_phone(None) == ("", ()) and normalise_phone("   ") == ("", ())
    assert normalise_email("") == ("", ()) and normalise_email(None) == ("", ())


@pytest.mark.parametrize("raw,out,rules", [
    ("Foo.Bar@Example.COM", "foo.bar@example.com", {"EMAIL_LOWERCASED"}),
    ("  a@b.co ", "a@b.co", {"EMAIL_TRIMMED"}),
    ("mailto:A@B.co", "a@b.co", {"EMAIL_MAILTO_STRIPPED", "EMAIL_LOWERCASED"}),
    ("<x@y.com>", "x@y.com", set()),
    ("kate+tag@example.org", "kate+tag@example.org", set()),
])
def test_email_forms(raw, out, rules):
    n, fired = normalise_email(raw)
    assert n == out and rules <= set(fired)
    if not rules:
        assert "EMAIL_LOWERCASED" not in fired and "EMAIL_TRIMMED" not in fired


@pytest.mark.parametrize("raw", ["bad@", "no at sign", "@example.com", "a@b", "a b@example.com", "a@@b.com"])
def test_bad_email_is_dropped_with_a_rule(raw):
    assert normalise_email(raw) == ("", ("EMAIL_INVALID",))


def test_email_local_part_is_never_edited_beyond_case():
    assert normalise_email("first.last+promo@Example.com")[0] == "first.last+promo@example.com"
    assert normalise_email("f.i.r.s.t@gmail.com")[0] == "f.i.r.s.t@gmail.com"


def test_name_is_only_tidied():
    assert normalise_name("  Kate   Smith ") == "Kate Smith" and normalise_name(None) == ""


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# properties

_word = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=9)
_type = st.sampled_from(sorted(STREET_TYPE_SPELLINGS))
_unit = st.one_of(st.just(""), st.integers(1, 999).map(str), st.integers(1, 99).map(lambda n: f"{n}A"))
_form = st.sampled_from(["word", "slash", "apt", "u", "flat"])


@st.composite
def messy_address(draw):
    unit = draw(_unit)
    number = str(draw(st.integers(1, 400)))
    street = f"{draw(_word)} {draw(_word)}" if draw(st.booleans()) else draw(_word)
    typ = draw(_type).title()
    front = number
    if unit:
        front = {"word": f"Unit {unit}, {number}", "slash": f"{unit}/{number}", "apt": f"Apt {unit} {number}", "u": f"U{unit}/{number}", "flat": f"Flat {unit}, {number}"}[draw(_form)]
    text = f"{front} {street.title()} {typ}"
    return draw(st.sampled_from([text, text.upper(), text.lower(), text + ".", "  " + text + " "])), unit, number, street.upper(), typ.upper()


@given(messy_address())
def test_normalising_the_canonical_line_gives_the_same_address(case):
    text, *_ = case
    a = normalise_address(text, *FAKE)
    again = normalise_address(a.line())
    assert again == a, (text, a.line())


@given(messy_address())
def test_unit_and_number_are_read_back_from_any_spelling(case):
    text, unit, number, street, typ = case
    a = normalise_address(text, *FAKE)
    assert (a.unit, a.number_first) == (unit, number)
    assert a.street_type == STREET_TYPE_SPELLINGS[typ]


@given(st.text(max_size=80))
def test_normalise_address_never_raises_and_is_idempotent_on_anything(text):
    a = normalise_address(text, "Fakeville", "NSW", "2000")
    assert normalise_address(a.line(), "Fakeville", "NSW", "2000") == normalise_address(a.line(), "Fakeville", "NSW", "2000")
    assert isinstance(a.line(), str)


@given(st.from_regex(r"[0-9 ()+\-]{0,20}", fullmatch=True))
def test_phone_normalisation_is_idempotent(raw):
    once, _ = normalise_phone(raw)
    if once:
        assert normalise_phone(once)[0] == once
        assert once.startswith("+61") and once[3:].isdigit() and len(once) == 12


@given(st.from_regex(r"[A-Za-z0-9._%+\-]{1,12}@[A-Za-z0-9\-]{1,8}\.[A-Za-z]{2,4}", fullmatch=True), st.sampled_from(["", " ", "mailto:"]))
def test_email_lowercase_is_idempotent_and_case_blind(raw, prefix):
    once, _ = normalise_email(prefix + raw)
    assert once == raw.lower() or once == ""
    if once:
        assert normalise_email(once) == (once, ())
        assert normalise_email(raw.upper())[0] == once


# ---- cases added after the mutation check found lines no test pinned ---------------------------------------------------------------------------------

def test_addr_is_immutable():
    a = normalise_address("12 Smith St", *FAKE)
    with pytest.raises(Exception):
        a.unit = "5"                                              # a frozen dataclass: a decided address cannot be edited by accident


def test_a_lone_st_is_not_a_saint():
    assert normalise_address("12 St", *FAKE).street_name == "ST"


def test_whitespace_rule_is_silent_on_clean_text():
    assert "WHITESPACE_COLLAPSED" not in normalise_address("12 SMITH STREET", "FAKEVILLE", "NSW", "2000").rules


@pytest.mark.parametrize("text,building,unit,number", [
    ("Skyline Tower Level G Unit 2, 12 Smith St", "SKYLINE TOWER", "2", "12"),
    ("Skyline Tower U5/12 Smith St", "SKYLINE TOWER", "5", "12"),
    ("Skyline Tower 5/12 Smith St", "SKYLINE TOWER", "5", "12"),
    ("Skyline Tower 12 Smith St", "SKYLINE TOWER", "", "12"),
    ("Skyline Tower Lot 7 Smith Rd", "SKYLINE TOWER", "", ""),
    ("Skyline Tower Floor 3 Unit 5 12 Smith St", "SKYLINE TOWER", "5", "12"),
    ("Skyline Tower Apt 5 12 Smith St", "SKYLINE TOWER", "5", "12"),
])
def test_a_building_name_with_no_comma_ends_at_the_first_unit_level_lot_or_number(text, building, unit, number):
    a = normalise_address(text, *FAKE)
    assert (a.building, a.unit, a.number_first) == (building, unit, number) and a.street_name in {"SMITH"}


def test_a_marker_followed_by_a_slash_form():
    a = normalise_address("Unit 5/12 Smith St", *FAKE)
    assert (a.unit, a.number_first, a.street_name) == ("5", "12", "SMITH") and {"UNIT_MARKER_WORD", "UNIT_SLASH_FORM"} <= set(a.rules)
    b = normalise_address("Unit 5/12-14 Smith St", *FAKE)
    assert (b.unit, b.number_first, b.number_last, b.street_name) == ("5", "12", "14", "SMITH")


def test_one_line_tail_rules():
    a = normalise_address("12 Smith St, Fakeville New South Wales 2000")
    assert a.state == "NSW" and "STATE_NAME_TO_CODE" in a.rules
    b = normalise_address("12 Smith St, Fakeville NSW 20000")
    assert b.postcode == "" and "POSTCODE_BAD_SHAPE" in b.rules and b.state == "NSW"
    c = normalise_address("12 Smith NSW 2000")
    assert (c.street_name, c.street_type, c.locality, c.state, c.postcode) == ("SMITH", "", "", "NSW", "2000")
    d = normalise_address("12 Parade St Fakeville NSW 2000")
    assert (d.street_name, d.street_type, d.locality) == ("PARADE", "STREET", "FAKEVILLE")


@pytest.mark.parametrize("kwargs", [dict(suburb="Fakeville"), dict(state="NSW"), dict(postcode="2000")])
def test_any_one_separate_field_switches_off_reading_the_tail(kwargs):
    a = normalise_address("12 Smith St, Elsewhere VIC 3000", **kwargs)
    assert "SUBURB_FROM_LINE" not in a.rules


def test_a_bad_separate_state_is_left_empty():
    a = normalise_address("12 Smith St", "Fakeville", "Narnia", "2000")
    assert a.state == "" and "STATE_MISSING" in a.rules


@pytest.mark.parametrize("text,building", [
    ("Tower, 12 Smith St", "TOWER"),
    ("Skyline Tower, 12 Smith St", "SKYLINE TOWER"),
    ("Tower 5/12 Smith St", "TOWER"),
    ("Tower Unit 5/12 Smith St", "TOWER"),
    ("Tower Level 3 Unit 5, 12 Smith St", "TOWER"),
])
def test_a_one_word_building_and_a_building_followed_by_a_marker_and_a_slash_form(text, building):
    a = normalise_address(text, *FAKE)
    assert a.building == building and a.number_first == "12" and a.street_name == "SMITH"


def test_a_state_alone_or_a_postcode_alone_ends_a_one_line_address():
    a = normalise_address("12 Smith St Fakeville NSW")
    assert (a.street_name, a.locality, a.state, a.postcode) == ("SMITH", "FAKEVILLE", "NSW", "")
    b = normalise_address("12 Smith St Fakeville 2000")
    assert (b.street_name, b.locality, b.state, b.postcode) == ("SMITH", "FAKEVILLE", "", "2000")


def test_saint_is_expanded_at_the_end_of_a_suburb_and_not_at_the_end_of_a_street_name():
    assert normalise_address("12 Smith St", "Port St", "NSW", "2000").locality == "PORT SAINT"
    row = dict(street_name="OXFORD ST", street_type_code="STREET", locality_name="PORT ST", state_abbreviation="NSW", postcode="2000", number_first=1)
    a = addr_from_gnaf(row)
    assert (a.street_name, a.locality) == ("OXFORD ST", "PORT SAINT")


def test_a_foreign_number_is_not_read_as_an_australian_one():
    assert normalise_phone("+55 412 345 678")[0] == "" and normalise_phone("+55 412 345 678")[1] == ("PHONE_INVALID",)
    assert normalise_phone("+1 612 345 6789")[0] == ""
    assert normalise_phone("61 2345 678")[0] == ""                                # ten digits with no plus: not a country code


def test_a_unit_written_with_only_a_letter_is_not_read_as_a_unit():
    a = normalise_address("Tower Unit G 12 Smith St", *FAKE)
    assert (a.building, a.unit, a.number_first) == ("TOWER UNIT G", "", "12")


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# classes from the second review (F4 to F9). Each CLASS-TABLE test builds its rows by code, and every table holds the harmful input now handled AND the ordinary input it must not break.

def gnaf_row(**kw):
    row = dict(street_name="SMITH", street_type_code="STREET", locality_name="FAKEVILLE", state_abbreviation="NSW", postcode="2000", number_first=40)
    row.update(kw)
    return row


def assert_table(rows):
    """rows are (label, got, want); every mismatch is listed at once."""
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, f"{len(bad)} of {len(rows)} rows differ, first: {bad[:5]}"


def ordinal(n):
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


COMPASS = ("N", "S", "E", "W", "NE", "NW", "SE", "SW")


def test_class_flat_and_level_types_keep_what_tells_two_spaces_apart():
    """CLASS-TABLE: F4. A carspace 3 is not unit 3 and basement 1 is not level 1; apartment 3, flat 3 and suite 3 are still unit 3."""
    rows = []
    for code, ident in itertools.product(sorted(FLAT_TYPES), ("3", "12")):
        want = f"{code} {ident}" if code in DISTINCT_FLAT_TYPES else ident
        rows.append((("flat code", code, ident), addr_from_gnaf(gnaf_row(flat_type_code=code, flat_number=ident)).unit, want))
        rows.append((("flat name", code, ident), addr_from_gnaf(gnaf_row(flat_type_code=FLAT_TYPES[code].lower(), flat_number=ident)).unit, want))
    for code, ident in itertools.product(sorted(LEVEL_TYPES), ("1", "")):
        want = ident if code in ("L", "FL") else f"{code} {ident}".strip()
        rows.append((("level code", code, ident), addr_from_gnaf(gnaf_row(level_type_code=code, level_number=ident or None)).level, want))
        rows.append((("level name", code, ident), addr_from_gnaf(gnaf_row(level_type_code=LEVEL_TYPES[code], level_number=ident or None)).level, want))
    for word, ident in itertools.product(sorted(OTHER_FLAT_WORDS), ("3", "03")):
        a = normalise_address(f"{word.title()} {ident}, 40 Smith St", *FAKE)
        rows.append((("source word", word, ident), (a.unit, a.number_first), (f"{OTHER_FLAT_WORDS[word]} 3", "40")))
        rows.append((("source meets G-NAF", word, ident), a.unit, addr_from_gnaf(gnaf_row(flat_type_code=OTHER_FLAT_WORDS[word], flat_number=3)).unit))
    for word, ident in itertools.product(sorted(OTHER_LEVEL_WORDS), ("1", "01")):
        a = normalise_address(f"{word.title()} {ident}, 40 Smith St", *FAKE)
        rows.append((("source level word", word, ident), (a.level, a.unit), (f"{OTHER_LEVEL_WORDS[word]} 1", "")))
        rows.append((("source meets G-NAF", word, ident), a.level, addr_from_gnaf(gnaf_row(level_type_code=OTHER_LEVEL_WORDS[word], level_number=1)).level))
    # the ordinary inputs: every unit word reads as the bare unit and meets every ordinary flat type; every level word reads as the bare level
    for marker, code in itertools.product(sorted(UNIT_MARKERS), sorted(FLAT_TYPES)):
        got = normalise_address(f"{marker.title()} 3, 40 Smith St", *FAKE).unit == addr_from_gnaf(gnaf_row(flat_type_code=code, flat_number=3)).unit
        rows.append((("ordinary unit word meets flat type", marker, code), got, code not in DISTINCT_FLAT_TYPES))
    for marker, ident in itertools.product(sorted(LEVEL_MARKERS), ("1", "01")):
        rows.append((("ordinary level word", marker, ident), normalise_address(f"{marker.title()} {ident}, 40 Smith St", *FAKE).level, "1"))
    for code in ("L", "FL"):
        rows.append((("ordinary level type", code), addr_from_gnaf(gnaf_row(level_type_code=code, level_number=1)).level, "1"))
    assert_table(rows)
    assert units_conflict(normalise_address("Unit 3, 40 Smith St", *FAKE), normalise_address("Carspace 3, 40 Smith St", *FAKE))
    assert not units_conflict(normalise_address("Apt 3, 40 Smith St", *FAKE), normalise_address("Flat 3, 40 Smith St", *FAKE))
    assert normalise_address("Basement 1, 40 Smith St", *FAKE).canon_key != normalise_address("Level 1, 40 Smith St", *FAKE).canon_key


def test_every_distinct_flat_type_and_every_word_is_a_real_g_naf_code():
    assert DISTINCT_FLAT_TYPES <= set(FLAT_TYPES) and set(OTHER_FLAT_WORDS.values()) <= DISTINCT_FLAT_TYPES
    assert set(OTHER_LEVEL_WORDS.values()) <= set(LEVEL_TYPES) - {"L", "FL"}
    for word, code in OTHER_FLAT_WORDS.items():
        assert FLAT_TYPES[code] == word
    for word, code in OTHER_LEVEL_WORDS.items():
        assert LEVEL_TYPES[code] == word


def test_class_street_suffix_is_kept_and_a_street_called_east_is_not_a_suffix():
    """CLASS-TABLE: F5. `12 Smith St North` is Smith Street with the suffix North, not a street called Smith Saint North; a street named East or a St Kilda Road is untouched."""
    rows = []
    types = [("St", "STREET"), ("Street", "STREET"), ("Rd", "ROAD"), ("Road", "ROAD"), ("Ave", "AVENUE"), ("Pde", "PARADE"), ("Gdns", "GARDENS")]
    suffixes = [(name.title(), name) for name in sorted(STREET_SUFFIXES.values())] + [(code, STREET_SUFFIXES[code]) for code in COMPASS]
    for (t, t_code), (s_text, s_name) in itertools.product(types, suffixes):
        a = normalise_address(f"12 Smith {t} {s_text}", *FAKE)
        rows.append((("source", t, s_text), (a.street_name, a.street_type, a.street_suffix), ("SMITH", f"{t_code} {s_name}", s_name)))
        rows.append((("line reads back", t, s_text), normalise_address(a.line()).street_key, a.street_key))
    for code, name in STREET_SUFFIXES.items():
        from_g_naf = addr_from_gnaf(gnaf_row(street_suffix_code=code))
        rows.append((("G-NAF", code), (from_g_naf.street_type, from_g_naf.street_suffix), (f"STREET {name}", name)))
        rows.append((("G-NAF meets source", code), from_g_naf.street_key, normalise_address(f"40 Smith St {name}", *FAKE).street_key))
    for text, name, typ, suffix in [("12 St Kilda Rd", "SAINT KILDA", "ROAD", ""), ("12 Smith St", "SMITH", "STREET", ""), ("12 East St", "EAST", "STREET", ""), ("5 North Rd", "NORTH", "ROAD", ""),
                                    ("5 West End Rd", "WEST END", "ROAD", ""), ("5 Smith North", "SMITH", "NORTH", "NORTH")]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.street_name, a.street_type, a.street_suffix), (name, typ, suffix)))
    assert_table(rows)
    north, south, bare = (normalise_address(f"12 Smith St {x}", *FAKE) for x in ("North", "South", ""))
    assert len({north.canon_key, south.canon_key, bare.canon_key}) == 3


def test_street_suffix_property_takes_the_longest_name():
    assert Addr(street_type="STREET NORTH EAST").street_suffix == "NORTH EAST"
    assert Addr(street_type="STREET EAST").street_suffix == "EAST"
    assert Addr(street_type="ROAD").street_suffix == "" and Addr(street_type="").street_suffix == "" and Addr(street_type="NORTH").street_suffix == "NORTH"
    assert Addr(street_type="STREET UPPER").street_suffix == "UPPER" and Addr(street_type="LOWER").street_suffix == "LOWER"


def test_class_postal_addresses_are_set_aside_and_boxes_that_are_streets_are_not():
    """CLASS-TABLE: F6. A PO Box, GPO Box, Locked Bag or Private Bag is a mailbox. A street called Box Hill Road or Box Street is a street."""
    rows = []
    boxes = ["PO Box 5", "P.O. Box 5", "P O Box 5", "PO BOX 5", "po box 5", "Po Box 5", "GPO Box 1234", "G.P.O. Box 1234", "gpo box 9", "Locked Bag 5", "LOCKED BAG 5", "Private Bag 7", "private bag 7"]
    layouts = {
        "own line": lambda b: (b, "Fakeville", "NSW", "2000"),
        "one line": lambda b: (f"{b} Fakeville NSW 2000", None, None, None),
        "comma": lambda b: (f"{b}, Fakeville NSW 2000", None, None, None),
        "after a street": lambda b: (f"12 Smith St, {b}", "Fakeville", "NSW", "2000"),
    }
    for box, (layout, build) in itertools.product(boxes, layouts.items()):
        a = normalise_address(*build(box))
        rows.append((("postal", box, layout), ("POSTAL_ADDRESS" in a.rules, a.street_name, a.number_first), (True, "", "")))
    streets = ["14 Box Hill Rd", "12 Box Street", "3 Boxwood Ave", "9 Gpo Lane", "8 Private Road", "7 Locked Gate Rd", "6 Bagot Rd", "5 Po Valley Dr", "2 Post Office Lane"]
    for street, layout in itertools.product(streets, ("fields", "one line")):
        a = normalise_address(street, "Box Hill", "VIC", "3128") if layout == "fields" else normalise_address(f"{street}, Box Hill VIC 3128")
        rows.append((("street", street, layout), ("POSTAL_ADDRESS" in a.rules, bool(a.street_name), bool(a.number_first)), (False, True, True)))
    assert_table(rows)


def test_a_postal_address_has_no_street_and_no_other_part():
    a = normalise_address("PO Box 5 Fakeville NSW 2000")
    assert "POSTAL_ADDRESS" in a.rules and a == Addr() and a.canon_key == "||||||"


def test_class_unit_and_level_forms_prefixes_zeros_and_floor_words():
    """CLASS-TABLE: F7. G01/12 is unit G1 of number 12, L3/12 is level 3 of number 12, 01/12 is unit 1, Ground Floor is level G and 3rd Floor is level 3; the plain forms are unchanged."""
    rows = []
    for prefix, (raw, clean) in itertools.product(("", "G", "A", "LG"), (("1", "1"), ("01", "1"), ("001", "1"), ("10", "10"), ("010", "10"), ("05A", "5A"), ("0", "0"), ("00", "0"))):
        a = normalise_address(f"{prefix}{raw}/12 Smith St", *FAKE)
        rows.append((("slash", prefix, raw), (a.unit, a.level, a.number_first, a.street_name), (prefix + clean, "", "12", "SMITH")))
        rows.append((("slash fires", prefix, raw), ("UNIT_SLASH_FORM" in a.rules, "UNIT_PREFIX_FORM" in a.rules, "LEADING_ZERO_DROPPED" in a.rules), (True, bool(prefix), clean != raw)))
        for marker in ("Unit", "Apt", "Flat", "Shop"):
            b = normalise_address(f"{marker} {prefix}{raw}, 12 Smith St", *FAKE)
            rows.append((("marker", marker, prefix, raw), (b.unit, b.number_first), (prefix + clean, "12")))
        c = normalise_address(f"{prefix}{raw}/12-14 Smith St", *FAKE)
        rows.append((("slash range", prefix, raw), (c.unit, c.number_key), (prefix + clean, "12-14")))
    for raw, clean in (("3", "3"), ("03", "3"), ("10", "10"), ("3A", "3A")):
        a = normalise_address(f"L{raw}/12 Smith St", *FAKE)
        rows.append((("level prefix", raw), (a.level, a.unit, a.number_first, "LEVEL_PREFIX_FORM" in a.rules), (clean, "", "12", True)))
    for raw, clean in (("3", "3"), ("03", "3"), ("12", "12"), ("012", "12")):
        for marker in ("Level", "Floor", "Lvl", "L", "Flr"):
            a = normalise_address(f"{marker} {raw}, 12 Smith St", *FAKE)
            rows.append((("level word", marker, raw), a.level, clean))
    for words in ("Ground Floor", "GROUND FLOOR", "Ground Level", "Ground Flr", "ground lvl"):
        for layout in (f"{words}, 12 Smith St", f"{words} 12 Smith St", f"Unit 5, {words}, 12 Smith St"):
            a = normalise_address(layout, *FAKE)
            rows.append((("ground", layout), (a.level, a.number_first, a.building), ("G", "12", "")))
    for n, word in itertools.product((1, 2, 3, 4, 5, 11, 12, 13, 21, 22, 23, 101), ("Floor", "Level", "Flr", "Lvl")):
        a = normalise_address(f"{ordinal(n)} {word}, 12 Smith St", *FAKE)
        rows.append((("ordinal", ordinal(n), word), (a.level, a.number_first, a.building, "FLOOR_WORDS_READ" in a.rules), (str(n), "12", "", True)))
    for text, want in [("3/12 Smith St", ("3", "", "12")), ("Unit 3, 12 Smith St", ("3", "", "12")), ("Level 3, 12 Smith St", ("", "3", "12")), ("12 Smith St", ("", "", "12")),
                       ("5A/12 Smith St", ("5A", "", "12")), ("Lot 7 Smith Rd", ("", "", "")), ("12 1st Ave", ("", "", "12"))]:
        a = normalise_address(text, *FAKE)
        rows.append((("ordinary", text), (a.unit, a.level, a.number_first), want))
    assert_table(rows)
    plain = normalise_address("12 Smith St", *FAKE)
    assert not {"LEADING_ZERO_DROPPED", "UNIT_PREFIX_FORM", "LEVEL_PREFIX_FORM", "FLOOR_WORDS_READ", "FLAT_TYPE_KEPT", "LEVEL_TYPE_KEPT", "STREET_SUFFIX_KEPT"} & set(plain.rules)


def test_a_unit_key_reads_back_from_its_own_line():
    for text in ("G01/12 Smith St", "L3/12 Smith St", "Carspace 12, 40 Smith St", "Basement 1, 40 Smith St", "Garage 4/40 Smith St", "Unit 5, Level 3, 12 Smith St", "Ground Floor, 12 Smith St",
                 "Mezzanine 2, Unit 5, 12 Smith St", "Store 7, 12 Smith St North"):
        a = normalise_address(text, *FAKE)
        assert normalise_address(a.line()) == a, (text, a.line())
    assert Addr(unit="SHED 3", number_first="1", street_name="X").line().startswith("SHED 3 1 X")
    assert Addr(level="LG 2", number_first="1", street_name="X").line().startswith("LOWER GROUND FLOOR 2 1 X")
    assert Addr(unit="CARS 3", number_first="1", street_name="X").line().startswith("CARSPACE 3 1 X")


def test_class_street_types_cover_everything_g_naf_lists_and_the_common_ones_still_split_a_line():
    """CLASS-TABLE: F8. All 275 STREET_TYPE_AUT types are read by code or short name; a suburb that ends in a street type word (Bondi Beach) or starts with one (Park Orchards) still splits off."""
    rows = []
    assert len(STREET_TYPES) == 275
    for (code, short), kind in itertools.product(sorted(STREET_TYPES.items()), ("code", "short", "short title")):
        if kind == "code" and (" " in code or "-" in code):
            continue
        word = {"code": code, "short": short, "short title": short.title()}[kind]
        a = normalise_address(f"5 Park {word}", *FAKE)
        rows.append((("type", code, kind), (a.street_name, a.street_type), ("PARK", code)))
        rows.append((("type word", code, kind), (canonical_street_type(word), bool(common_street_type(word))), (code, word.upper() in STREET_TYPE_SPELLINGS)))
    for spelling, code in sorted(STREET_TYPE_SPELLINGS.items()):
        rows.append((("common", spelling), (canonical_street_type(spelling), common_street_type(spelling)), (code, code)))
    for text, want in [
        ("12 Smith St, Bondi Beach", ("SMITH", "STREET", "BONDI BEACH")),
        ("12 Smith St, Palm Beach NSW 2108", ("SMITH", "STREET", "PALM BEACH")),
        ("12 Ocean Beach Rd Bondi NSW 2000", ("OCEAN BEACH", "ROAD", "BONDI")),
        ("12 Smith St Park Orchards VIC 3114", ("SMITH", "STREET", "PARK ORCHARDS")),
        ("88 Surfers Paradise Blvd, Surfers Paradise QLD 4217", ("SURFERS PARADISE", "BOULEVARD", "SURFERS PARADISE")),
        ("5 Park Gdns Fakeville NSW 2000", ("PARK", "GARDENS", "FAKEVILLE")),
        ("9 Hill Hts, Fakeville NSW 2000", ("HILL", "HEIGHTS", "FAKEVILLE")),
        ("12 Smith St, Sunnybank Hills QLD 4109", ("SMITH", "STREET", "SUNNYBANK HILLS")),
        ("12 Smith Street Bondi Beach NSW 2026", ("SMITH", "STREET", "BONDI BEACH")),
    ]:
        a = normalise_address(text)
        rows.append((("line", text), (a.street_name, a.street_type, a.locality), want))
    assert_table(rows)


def test_a_street_type_only_g_naf_lists_splits_a_line_only_when_no_common_type_does():
    assert normalise_address("5 Park Gdns Fakeville NSW 2000").locality == "FAKEVILLE"
    mixed = normalise_address("5 Park Gdns Smith St Fakeville NSW 2000")          # the common type ends the street first
    assert (mixed.street_name, mixed.street_type, mixed.locality) == ("PARK GDNS SMITH", "STREET", "FAKEVILLE")
    assert normalise_address("5 Park Gdns").street_type == "GARDENS"


def test_class_postcode_and_state_forms_nt_leading_zero_and_other_territories():
    """CLASS-TABLE: F9. NT postcode 800 is 0800 and OT is a state; the same short postcode in any other state is still dropped."""
    rows = []
    for state, pc in itertools.product(("NT", "nt", "Northern Territory"), ("800", "0800", "880", "08000", "80", "abc")):
        a = normalise_address("12 Smith St", "Darwin", state, pc)
        want = {"800": "0800", "880": "0880", "0800": "0800"}.get(pc, "")
        rows.append((("fields", state, pc), (a.postcode, a.state, "POSTCODE_ZERO_RESTORED" in a.rules, "POSTCODE_BAD_SHAPE" in a.rules),
                     (want, "NT", pc in ("800", "880"), pc in ("08000", "80", "abc"))))
    for state, pc in itertools.product(("NSW", "VIC", "QLD", "SA", "WA", "TAS", "ACT", "", None), ("800", "200")):
        a = normalise_address("12 Smith St", "Fakeville", state, pc)
        rows.append((("other state", state, pc), (a.postcode, "POSTCODE_ZERO_RESTORED" in a.rules, "POSTCODE_BAD_SHAPE" in a.rules), ("", False, True)))
    for state, pc in itertools.product(("NT", "nt", "Northern Territory"), ("800", "0800", "20000")):
        a = normalise_address(f"12 Smith St, Darwin {state} {pc}")
        rows.append((("one line", state, pc), (a.postcode, a.state), ({"800": "0800", "0800": "0800"}.get(pc, ""), "NT")))
        b = normalise_address(f"12 Smith St Darwin {state} {pc}")
        rows.append((("one line no comma", state, pc), (b.postcode, b.state, b.locality), ({"800": "0800", "0800": "0800"}.get(pc, ""), "NT", "DARWIN")))
    for state, pc in itertools.product(("OT", "ot", "Other Territories"), ("2899", "6798", "6799")):
        a = normalise_address("12 Smith St", "Norfolk Island", state, pc)
        rows.append((("OT fields", state, pc), (a.state, a.postcode), ("OT", pc)))
        b = normalise_address(f"12 Smith St, Norfolk Island {state} {pc}")
        rows.append((("OT line", state, pc), (b.state, b.postcode), ("OT", pc)))
    for state in ("XX", "Narnia", "OTT", "N"):
        rows.append((("not a state", state), normalise_address("12 Smith St", "Fakeville", state, "2000").state, ""))
    for state, pc in itertools.product(STATES, ("2000", "3000")):
        a = normalise_address("12 Smith St", "Fakeville", state, pc)
        rows.append((("ordinary", state, pc), (a.state, a.postcode, "POSTCODE_ZERO_RESTORED" in a.rules), (state, pc, False)))
    assert_table(rows)


def test_gnaf_part_and_display_line_keep_the_g_naf_spelling():
    assert gnaf_part("G", 1, "A") == "G1A" and gnaf_part(None, None, None) == "" and gnaf_part("", "", "") == "" and gnaf_part(None, 12, None) == "12"
    row = gnaf_row(street_name="ST KILDA", street_type_code="ROAD", locality_name="ST KILDA", state_abbreviation="VIC", postcode="3182", number_first=12, number_last=14,
                   flat_type_code="APT", flat_number=5, flat_number_suffix="A", level_type_code="B", level_number=1, building_name="Skyline Tower")
    assert gnaf_display_line(row) == "SKYLINE TOWER, BASEMENT 1 APARTMENT 5A 12-14 ST KILDA ROAD, ST KILDA VIC 3182"
    assert gnaf_display_line(gnaf_row()) == "40 SMITH STREET, FAKEVILLE NSW 2000"
    assert gnaf_display_line(gnaf_row(level_number=3, flat_number=2, number_first=None, lot_number="7")) == "LEVEL 3 UNIT 2 LOT 7 SMITH STREET, FAKEVILLE NSW 2000"
    assert gnaf_display_line(gnaf_row(level_type_code="G", street_suffix_code="N")) == "GROUND 40 SMITH STREET NORTH, FAKEVILLE NSW 2000"
    assert gnaf_display_line(gnaf_row(level_type_code="FL", level_number=2, flat_type_code="CARS", flat_number=9)) == "FLOOR 2 CARSPACE 9 40 SMITH STREET, FAKEVILLE NSW 2000"
    assert gnaf_display_line(gnaf_row(street_suffix_code="ZZ", locality_name="", postcode=None)) == "40 SMITH STREET ZZ, NSW"
    assert gnaf_display_line({}) == ""


def test_a_type_code_g_naf_does_not_list_is_kept_as_it_is():
    assert addr_from_gnaf(gnaf_row(street_type_code="ZZTOP")).street_type == "ZZTOP"
    assert addr_from_gnaf(gnaf_row(street_type_code="")).street_type == "" and addr_from_gnaf(gnaf_row(street_type_code=None)).street_type == ""


def test_the_rules_for_a_kept_type_or_suffix_fire_only_when_one_was_kept():
    for text, rule in (("Carspace 12, 40 Smith St", "FLAT_TYPE_KEPT"), ("Basement 1, 40 Smith St", "LEVEL_TYPE_KEPT"), ("12 Smith St North", "STREET_SUFFIX_KEPT")):
        assert rule in normalise_address(text, *FAKE).rules
        assert rule not in normalise_address(text.replace("Carspace", "Unit").replace("Basement", "Level").replace(" North", ""), *FAKE).rules
