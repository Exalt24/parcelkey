"""The eighth review (a blind recall after the seventh) reproduced two defects. F1: a unit written in round brackets (`30 Smith St (Unit 5)`) was dropped as a note, so the records of different units of one
tower all linked to the building's own address with EXACT_KEY at score 1.0; the same unit in square brackets or after a comma was read as the unit. F2: a word such as Home or Office after the street
type with a comma behind it (`12 Smith St Home, Fakeville NSW 2000`) kept St out of the street type, so St read as Saint and a second property was minted beside the real address. Each is a generated
table over the whole vocabulary of its class with the ordinary input it must not break beside it; the link direction of F1 runs through the real pipeline on a tower and its units."""
from __future__ import annotations

import itertools

import pytest

import helpers as h
from conftest import AGENCY
from parcelkey.normalise import LEVEL_MARKERS, STREET_DESCRIPTORS, UNIT_MARKERS, normalise_address

FAKE = ("Fakeville", "NSW", "2000")


def assert_table(rows):
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, f"{len(bad)} of {len(rows)} rows differ, first: {bad[:5]}"


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F1: a unit in round brackets is the unit

BRACKET_WORDS = ["Unit", "Apt", "Apartment", "Flat", "Suite", "Shop", "Villa", "U"]
BRACKET_FORMS = ["{s} ({w} {n})", "({w} {n}) {s}", "{s}, ({w} {n})", "{s} ( {w} {n} )", "{s} ({w}. {n})"]
OTHER_FORMS = ["{s} [{w} {n}]", "{s}, {w} {n}", "{w} {n}, {s}"]


def test_class_f1_a_unit_in_round_brackets_reads_like_the_same_unit_in_square_brackets_or_after_a_comma():
    """CLASS-TABLE: F1 (round 8). Every unit word, number, bracket form and tail gives the address the unit written with a comma gives; a bracket with no unit word stays a dropped note."""
    rows = []
    for word, number, form, street, tail in itertools.product(BRACKET_WORDS, ["5", "15", "6B", "102"], BRACKET_FORMS, ["30 Smith St", "30 Smith Street"], ["", ", Fakeville NSW 2000", " Fakeville NSW 2000"]):
        written = form.format(s=street, w=word, n=number) + tail
        got = normalise_address(written)
        want = normalise_address(f"{street}, {word} {number}{tail}" if not form.startswith("(") else f"{word} {number}, {street}{tail}")
        rows.append((("unit", written), (got.unit, got.number_key, got.street_key, got.locality), (want.unit, want.number_key, want.street_key, want.locality)))
        rows.append((("unit is not empty", written), bool(got.unit), True))
        rows.append((("not dropped as a note", written), "BRACKET_NOTE_DROPPED" in got.rules, False))
    for word, number in itertools.product(sorted((UNIT_MARKERS | LEVEL_MARKERS | {"LOT"}) - {"#"}), ["5", "12"]):
        got = normalise_address(f"30 Smith St ({word.title()} {number})", *FAKE)
        rows.append((("every marker", word, number), "BRACKET_NOTE_DROPPED" in got.rules, False))
        rows.append((("every marker keeps the street", word, number), got.street_key, "SMITH STREET"))
    for form in OTHER_FORMS:          # the other forms already worked and must go on working
        got = normalise_address(form.format(s="30 Smith St", w="Unit", n="5"), *FAKE)
        rows.append((("other form", form), (got.unit, got.number_key), ("5", "30")))
    # a bracket with no unit word is a note, a bracket that holds more than a unit is a note
    for note in ["(rear)", "(5)", "(investment property)", "(Unit 5 rear)", "(Unit)", "(near Unit 5)", "(Unit 5, rear)", "(see Kate)", "(Level)"]:
        got = normalise_address(f"30 Smith St {note}", *FAKE)
        rows.append((("note", note), (got.unit, got.number_key, got.street_key, "BRACKET_NOTE_DROPPED" in got.rules), ("", "30", "SMITH STREET", True)))
    assert len(rows) > 1000
    assert_table(rows)


TOWER = [h.addr("GT", "30", building="SKYLINE TOWER"), h.addr("GT5", "30", unit="5", level="3"), h.addr("GT6", "30", unit="6", level="3"), h.addr("GT15", "30", unit="15", level="4")]


@pytest.mark.db
def test_f1_units_of_a_tower_written_in_round_brackets_link_to_their_own_unit_not_the_building(hand, conn, admin, tmp_path):
    hand(TOWER)
    rows = [("R1", "30 Smith St (Unit 5)"), ("R2", "(Unit 6) 30 Smith St"), ("R3", "30 Smith St (Apt 15)"), ("R4", "30 Smith St [Unit 6]"), ("R5", "30 Smith St, Unit 5"), ("R6", "30 Smith St (rear)"),
            ("R7", "30 Smith St")]
    folder = h.write_feeds(tmp_path / "f1", crm_rows=[h.crm(ref, text) for ref, text in rows])
    h.run(conn, AGENCY, folder)
    got = h.decisions(admin, AGENCY)
    assert {r: got[f"crm:{r}"] for r, _ in rows} == {"R1": ("link", "EXACT_KEY", "GT5"), "R2": ("link", "EXACT_KEY", "GT6"), "R3": ("link", "EXACT_KEY", "GT15"), "R4": ("link", "EXACT_KEY", "GT6"),
                                                    "R5": ("link", "EXACT_KEY", "GT5"), "R6": ("link", "EXACT_KEY", "GT"), "R7": ("link", "EXACT_KEY", "GT")}


@pytest.mark.db
def test_f1_a_unit_in_round_brackets_that_the_building_does_not_have_is_still_a_unit_conflict(hand, conn, admin, tmp_path):
    hand(TOWER)
    folder = h.write_feeds(tmp_path / "f1c", crm_rows=[h.crm("R1", "30 Smith St (Unit 9)")])
    h.run(conn, AGENCY, folder)
    assert h.decisions(admin, AGENCY)["crm:R1"] == ("quarantine", "UNIT_CONFLICT")


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# F2: a descriptive word after the street type does not turn St into Saint

def test_class_f2_a_descriptive_word_after_the_street_type_does_not_turn_st_into_saint():
    """CLASS-TABLE: F2 (round 8). Home, Office, Shop, Garage, Penthouse and Granny Flat after a street type, with or without a comma behind it, read as the street with no word; St stays a street type."""
    rows = []
    for descriptor, street, tail in itertools.product(STREET_DESCRIPTORS, ["Smith St", "Smith Street", "Smith St North", "Pacific Hwy", "St Kilda Rd"], ["", ", Fakeville NSW 2000", ", Fakeville"]):
        word = " ".join(descriptor).title()
        got = normalise_address(f"12 {street} {word}{tail}")
        plain = normalise_address(f"12 {street}{tail}")
        rows.append((("same as without", word, street, tail), (got.number_key, got.street_name, got.street_type, got.locality, got.state, got.postcode),
                     (plain.number_key, plain.street_name, plain.street_type, plain.locality, plain.state, plain.postcode)))
        rows.append((("no descriptor in the street", word, street, tail), word.upper() in got.street_key, False))
    # the words of the set, and nothing wider
    assert {" ".join(d) for d in STREET_DESCRIPTORS} == {"HOME", "OFFICE", "SHOP", "GARAGE", "PENTHOUSE", "GRANNY FLAT"}
    # ordinary inputs the change must not touch: a suburb that starts with the word, a street named with it, any other word, an unknown word
    for text, want in [("12 Smith St, Home Hill QLD 4806", ("SMITH STREET", "HOME HILL")), ("12 Smith St Home Hill QLD 4806", ("SMITH STREET", "HOME HILL")),
                       ("12 Home St, Fakeville NSW 2000", ("HOME STREET", "FAKEVILLE")), ("12 Shop St, Fakeville NSW 2000", ("SHOP STREET", "FAKEVILLE")),
                       ("12 St Kilda Rd, Fakeville NSW 2000", ("SAINT KILDA ROAD", "FAKEVILLE")), ("12 Smith St, Fakeville NSW 2000", ("SMITH STREET", "FAKEVILLE")),
                       ("12 Smith St Barn, Fakeville NSW 2000", ("SMITH SAINT BARN", "FAKEVILLE"))]:
        got = normalise_address(text)
        rows.append((("ordinary", text), (got.street_key, got.locality), want))
    rows.append((("rule", "home"), "DESCRIPTOR_AFTER_STREET" in normalise_address("12 Smith St Home, Fakeville NSW 2000").rules, True))
    rows.append((("rule absent", "ordinary"), "DESCRIPTOR_AFTER_STREET" in normalise_address("12 Smith St, Fakeville NSW 2000").rules, False))
    assert len(rows) > 100
    assert_table(rows)


@pytest.mark.db
def test_f2_a_trailing_home_or_office_links_to_the_real_address_and_mints_no_property(hand, conn, admin, tmp_path):
    hand([h.addr("G12", "12"), h.addr("G14", "14")])
    rows = [("R1", "12 Smith St Home, Fakeville NSW 2000"), ("R2", "12 Smith St Office, Fakeville NSW 2000"), ("R3", "12 Smith St Granny Flat, Fakeville NSW 2000"), ("R4", "14 Smith St Garage")]
    folder = h.write_feeds(tmp_path / "f2", crm_rows=[h.crm(ref, text) for ref, text in rows])
    h.run(conn, AGENCY, folder)
    got = h.decisions(admin, AGENCY)
    assert [got[f"crm:{r}"] for r, _ in rows] == [("link", "EXACT_KEY", "G12"), ("link", "EXACT_KEY", "G12"), ("link", "EXACT_KEY", "G12"), ("link", "EXACT_KEY", "G14")]
    assert admin.execute("select count(*) from property where origin <> 'gnaf'").fetchone()[0] == 0
