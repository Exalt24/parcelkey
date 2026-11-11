"""The sixth review (the final blind recall) found that a shed, a cage and most other kept-apart flat types were not read from feed text, so `Shed 3` merged into `Unit 3` (the mistake the README calls the worst), that a kiosk, a ward, a site and the like were not read as unit words at all, and that Nth, Sth and Pt in a suburb were not expanded. These are generated class tests over the whole vocabulary of G-NAF flat types and the suburb abbreviations."""
from __future__ import annotations

import itertools

import pytest

from parcelkey.gnaf_codes import FLAT_TYPES
from parcelkey.normalise import (DISTINCT_FLAT_TYPES, NUMBERED_UNIT_WORDS, OTHER_FLAT_WORDS, SUBURB_WORD_ABBREVIATIONS, normalise_address)

FAKE = ("Fakeville", "NSW", "2000")
FORMS = ("{w} 3, 40 Smith St", "{w} 3/40 Smith St", "{w} 3 40 Smith St")


def test_every_kept_apart_flat_type_except_marine_berth_has_a_feed_word_and_reads_as_its_own_type():
    assert set(OTHER_FLAT_WORDS.values()) == DISTINCT_FLAT_TYPES - {"MBTH"}
    for word, code in OTHER_FLAT_WORDS.items():
        for form in FORMS:
            a = normalise_address(form.format(w=word.title()), *FAKE)
            assert a.unit == code + " 3", (word, form, a.unit)
            assert a.number_first == "40", (word, form)


def test_a_shed_is_never_the_unit_with_the_same_number():
    shed = normalise_address("Shed 3, 40 Smith St", *FAKE)
    unit = normalise_address("Unit 3, 40 Smith St", *FAKE)
    assert shed.unit != unit.unit and unit.unit == "3"


@pytest.mark.parametrize("word", sorted(NUMBERED_UNIT_WORDS))
def test_every_numbered_unit_word_reads_as_an_ordinary_unit_with_a_number(word):
    for form in FORMS:
        a = normalise_address(form.format(w=word.title()), *FAKE)
        assert a.unit == "3" and a.number_first == "40", (word, form, a.unit, a.number_first)


def test_the_g_nai_flat_types_a_feed_can_write_as_one_word_are_all_read_in_some_way():
    # the class: a G-NAF flat type whose word is read nowhere, so the type vanishes into the street number or a building name
    read = set(OTHER_FLAT_WORDS) | set(NUMBERED_UNIT_WORDS) | {"UNIT", "FLAT", "SHOP", "SUITE", "APARTMENT", "OFFICE", "ROOM", "VILLA", "TOWNHOUSE"}
    unread = sorted(n for c, n in FLAT_TYPES.items() if " " not in n and n.upper() not in read)
    # these are deliberately not unit words: a lot is its own field, and the others are too often part of an address or a place name
    assert unread == sorted(["LOT", "REAR", "RESERVE", "SECTION", "HOUSE"]), unread


@pytest.mark.parametrize("short,long", sorted(SUBURB_WORD_ABBREVIATIONS.items()))
def test_a_suburb_abbreviation_is_expanded_in_a_suburb_and_not_in_a_street(short, long):
    sub = normalise_address("20 Testwood Street", f"{short.title()} Testville", "QLD", "4000")
    assert sub.locality == f"{long} TESTVILLE" and "SUBURB_WORD_EXPANDED" in sub.rules
    street = normalise_address(f"20 {short.title()} Road", "Testville", "QLD", "4000")
    assert short in street.street_name and "SUBURB_WORD_EXPANDED" not in street.rules


def test_a_suburb_that_is_not_an_abbreviation_is_left_alone():
    a = normalise_address("20 Testwood Street", "Northcote", "VIC", "3070")
    assert a.locality == "NORTHCOTE" and "SUBURB_WORD_EXPANDED" not in a.rules


def test_the_feed_words_never_collide_across_the_two_tables():
    assert not (set(OTHER_FLAT_WORDS) & set(NUMBERED_UNIT_WORDS))
    assert all(itertools.chain([True], (c in DISTINCT_FLAT_TYPES for c in OTHER_FLAT_WORDS.values())))
