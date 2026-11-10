"""Normalisation: every spelling of a unit, a street type, a state, a lot and a phone, with the rules that must fire for each."""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from parcelkey.normalise import (STATE_NAMES, STATES, STREET_TYPE_SPELLINGS, Addr, addr_from_gnaf, canonical_street_type, normalise_address, normalise_email, normalise_name,
                                 normalise_phone)

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
