"""parcelkey/gnaf_codes.py is data copied from the Geoscape Data Product Description, Appendix C. These tests pin its size and shape so a hand edit shows up."""
from __future__ import annotations

from parcelkey.gnaf_codes import FLAT_TYPES, LEVEL_TYPES, STREET_SUFFIXES, STREET_TYPES


def test_the_lists_hold_every_code_the_appendix_gave_on_the_day_it_was_read():
    assert (len(STREET_TYPES), len(STREET_SUFFIXES), len(FLAT_TYPES), len(LEVEL_TYPES)) == (275, 19, 53, 15)


def test_codes_are_upper_case_text_and_no_short_street_spelling_is_another_types_code():
    for table in (STREET_TYPES, STREET_SUFFIXES, FLAT_TYPES, LEVEL_TYPES):
        assert all(k == k.upper() and v == v.upper() and k and v for k, v in table.items())
    assert not [short for code, short in STREET_TYPES.items() if short in STREET_TYPES and short != code]
    assert len(set(STREET_TYPES.values())) == len(STREET_TYPES)


def test_a_few_entries_by_name():
    assert STREET_TYPES["STREET"] == "ST" and STREET_TYPES["GARDENS"] == "GDNS" and STREET_TYPES["RIGHT OF WAY"] == "ROFW"
    assert STREET_SUFFIXES["N"] == "NORTH" and STREET_SUFFIXES["UP"] == "UPPER" and STREET_SUFFIXES["NE"] == "NORTH EAST"
    assert FLAT_TYPES["CARS"] == "CARSPACE" and FLAT_TYPES["APT"] == "APARTMENT" and FLAT_TYPES["SE"] == "SUITE"
    assert LEVEL_TYPES["B"] == "BASEMENT" and LEVEL_TYPES["G"] == "GROUND" and LEVEL_TYPES["L"] == "LEVEL"
