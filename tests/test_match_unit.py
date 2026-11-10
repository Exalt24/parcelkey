"""The decision function, with no database: the rules in order, and the two hard rules that nothing can configure away."""
from __future__ import annotations

import random

import pytest
from hypothesis import given, strategies as st

from parcelkey.block import Candidate
from parcelkey.match import Decision, MatchConfig, decide, is_transposition, locality_ok, numbers_equal, units_conflict
from parcelkey.normalise import Addr

CFG = MatchConfig()


def A(number="12", unit="", level="", lot="", street="SMITH", typ="STREET", locality="FAKEVILLE", state="NSW", postcode="2000", last=""):
    return Addr(unit=unit, level=level, lot=lot, number_first=number, number_last=last, street_name=street, street_type=typ, locality=locality, state=state, postcode=postcode)


def C(pid, addr, sim=1.0, dist=None, same_postcode=True):
    return Candidate(pid, addr, sim, same_postcode, dist)


# ---- the hard unit rule ---------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,conflict", [
    ("5", "5", False), ("5", "6", True), ("5", "", True), ("", "5", True), ("", "", False), ("5A", "5B", True), ("5A", "5A", False), ("15", "51", True), ("105", "150", True),
])
def test_units_conflict_table(a, b, conflict):
    assert units_conflict(A(unit=a), A(unit=b)) is conflict


@pytest.mark.parametrize("la,lb,conflict", [("3", "3", False), ("3", "4", True), ("3", "", False), ("", "4", False), ("", "", False)])
def test_level_conflicts_only_when_written_on_both_sides(la, lb, conflict):
    assert units_conflict(A(unit="7", level=la), A(unit="7", level=lb)) is conflict


@pytest.mark.parametrize("rec_unit,cand_units", [("5", ["4", "6", "7"]), ("5", ["15", "51", "50"]), ("", ["1", "2", "3"]), ("9", [""]), ("12", ["1", "2", "21"])])
def test_a_different_unit_never_links_even_when_everything_else_is_equal(rec_unit, cand_units):
    cands = [C(f"P{i}", A(unit=u)) for i, u in enumerate(cand_units)]
    d = decide(A(unit=rec_unit), None, None, cands, CFG)
    assert d.outcome == "quarantine" and d.reason == "UNIT_CONFLICT", d
    assert d.detail["candidate_units"] == sorted(set(cand_units))


def test_the_right_unit_wins_among_siblings():
    cands = [C("P4", A(unit="4")), C("P5", A(unit="5")), C("P6", A(unit="6"))]
    d = decide(A(unit="5"), None, None, cands, CFG)
    assert (d.outcome, d.rule, d.pid, d.score) == ("link", "EXACT_KEY", "P5", 1.0)


def test_a_missing_unit_links_to_the_building_when_the_building_has_its_own_address():
    cands = [C("PB", A(unit="")), C("P5", A(unit="5"))]
    assert decide(A(unit=""), None, None, cands, CFG).pid == "PB"


def test_unit_rule_holds_for_near_miss_streets_too():
    cands = [C("P6", A(unit="6", street="SMYTH"), sim=0.8)]
    d = decide(A(unit="5"), None, None, cands, CFG)
    assert d.outcome == "quarantine" and d.reason == "UNIT_CONFLICT"


# ---- exact, near miss, tie, threshold ----------------------------------------------------------------------------------------------------------

def test_exact_key_links_and_notes_a_postcode_that_disagrees():
    d = decide(A(postcode="2010"), None, None, [C("P1", A(postcode="2000"))], CFG)
    assert (d.outcome, d.rule, d.pid) == ("link", "EXACT_KEY", "P1") and d.rules == ("POSTCODE_MISMATCH",)
    assert decide(A(postcode=""), None, None, [C("P1", A())], CFG).rules == ()
    assert decide(A(), None, None, [C("P1", A())], CFG).rules == ()


def test_two_exact_candidates_are_a_tie_not_a_pick():
    d = decide(A(), None, None, [C("P1", A()), C("P2", A())], CFG)
    assert d.outcome == "quarantine" and d.reason == "AMBIGUOUS_TIE" and {c["pid"] for c in d.detail["candidates"]} == {"P1", "P2"}


def test_near_miss_links_above_the_threshold_and_not_below():
    cand = [C("P1", A(street="HARWICK"), sim=0.72)]
    d = decide(A(street="HARWIK"), None, None, cand, MatchConfig(threshold=0.7))
    assert (d.outcome, d.rule, d.pid, d.score) == ("link", "TRIGRAM", "P1", 0.72)
    d = decide(A(street="HARWIK"), None, None, cand, MatchConfig(threshold=0.73))
    assert d.outcome == "quarantine" and d.reason == "BELOW_THRESHOLD" and d.detail["threshold"] == 0.73
    d = decide(A(street="HARWIK"), None, None, [C("P1", A(street="HARWICK"), sim=0.73)], MatchConfig(threshold=0.73))
    assert d.outcome == "link"                                                      # the threshold itself is enough


def test_two_close_streets_are_a_tie():
    cands = [C("P1", A(street="HARWICK"), sim=0.70), C("P2", A(street="HARDWICK"), sim=0.68)]
    d = decide(A(street="HARWIK"), None, None, cands, MatchConfig(threshold=0.6, tie_margin=0.03))
    assert d.outcome == "quarantine" and d.reason == "AMBIGUOUS_TIE"
    d = decide(A(street="HARWIK"), None, None, [C("P1", A(street="HARWICK"), sim=0.80), C("P2", A(street="HARDWICK"), sim=0.68)], MatchConfig(threshold=0.6, tie_margin=0.03))
    assert (d.outcome, d.pid) == ("link", "P1")
    d = decide(A(street="HARWIK"), None, None, [C("P1", A(street="HARWICK"), sim=0.71), C("P2", A(street="HARDWICK"), sim=0.68)], MatchConfig(threshold=0.6, tie_margin=0.03))
    assert d.reason == "AMBIGUOUS_TIE"                                              # a gap of exactly the margin is still a tie


def test_a_street_below_the_floor_is_not_a_candidate():
    d = decide(A(street="HARWIK"), None, None, [C("P1", A(street="ELM"), sim=0.2)], CFG)
    assert d.outcome == "new" and d.rule == "NEW_DERIVED"


def test_number_must_match_for_any_link():
    d = decide(A(number="14"), None, None, [C("P1", A(number="12")), C("P2", A(number="16"))], CFG)
    assert d.outcome == "new"


def test_range_numbers():
    assert numbers_equal(A(number="12", last="14"), A(number="12", last="14"))
    assert numbers_equal(A(number="12", last="14"), A(number="12"))
    assert numbers_equal(A(number="12"), A(number="12", last="14"))
    assert not numbers_equal(A(number="12", last="14"), A(number="12", last="16"))
    assert not numbers_equal(A(number="12"), A(number="13"))


def test_locality_and_state_agreement():
    assert locality_ok(A(), A()) and locality_ok(A(locality=""), A()) and locality_ok(A(), A(locality=""))
    assert not locality_ok(A(locality="OTHERTON"), A())
    assert not locality_ok(A(state="VIC"), A(state="NSW"))
    assert locality_ok(A(state=""), A(state="NSW"))


def test_same_street_and_number_in_another_suburb_of_the_same_postcode_is_set_aside():
    other = C("P1", A(locality="OTHERTON"), same_postcode=True)
    d = decide(A(locality="FAKEVILLE"), None, None, [other], CFG)
    assert d.outcome == "quarantine" and d.reason == "LOCALITY_CONFLICT"
    far = C("P1", A(locality="OTHERTON", postcode="3000"), same_postcode=False)
    assert decide(A(locality="FAKEVILLE"), None, None, [far], CFG).outcome == "new"


# ---- transposed numbers, unparseable, new ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,expect", [("12", "21", True), ("1304", "1034", True), ("1234", "1243", True), ("12", "12", False), ("12", "13", False), ("123", "321", False), ("1", "1", False),
                                        ("12", "123", False), ("ab", "ba", False), ("1A", "A1", False), ("1220", "2210", False), ("1220", "2120", True)])
def test_is_transposition(a, b, expect):
    assert is_transposition(a, b) is expect


def test_a_number_that_is_a_digit_swap_of_a_neighbour_is_set_aside():
    d = decide(A(number="21"), None, None, [C("P12", A(number="12"))], CFG)
    assert d.outcome == "quarantine" and d.reason == "NUMBER_TRANSPOSED"
    assert decide(A(number="21"), None, None, [C("P12", A(number="12")), C("P21", A(number="21"))], CFG).pid == "P21"       # the number exists: no suspicion


def test_a_transposed_number_with_a_different_unit_is_not_suspected():
    assert decide(A(number="21", unit="3"), None, None, [C("P12", A(number="12", unit="4"))], CFG).outcome == "new"


def test_nothing_near_makes_a_new_derived_property():
    d = decide(A(number="14"), None, None, [], CFG)
    assert (d.outcome, d.rule, d.score, d.pid) == ("new", "NEW_DERIVED", 1.0, "")


@pytest.mark.parametrize("rec", [A(street=""), A(number=""), Addr()])
def test_unparseable_records_are_set_aside_with_a_reason(rec):
    d = decide(rec, None, None, [C("P1", A())], CFG)
    assert d.outcome == "quarantine" and d.reason == "UNPARSEABLE"


# ---- lots ---------------------------------------------------------------------------------------------------------------------------------------

def lot_rec(**kw):
    return A(number="", lot="7", street="SMITH", typ="ROAD", **kw)


def test_a_lot_only_record_links_to_a_lot_only_address_without_a_geocode():
    d = decide(lot_rec(), None, None, [C("PL", A(number="", lot="7", typ="ROAD"))], CFG)
    assert (d.outcome, d.rule, d.pid) == ("link", "EXACT_LOT", "PL")


def test_a_lot_only_record_never_links_to_a_numbered_address_without_a_geocode():
    numbered = C("PN", A(number="23", lot="7", typ="ROAD"), dist=None)
    d = decide(lot_rec(), None, None, [numbered], CFG)
    assert d.outcome == "quarantine" and d.reason == "LOT_NO_GEOCODE"
    d = decide(lot_rec(), -33.8, 151.0, [numbered], CFG)                  # the record has coordinates but the reference has no distance: not near
    assert d.reason == "LOT_TOO_FAR"


def test_a_lot_only_record_links_to_a_numbered_address_only_within_the_distance():
    inside = C("PN", A(number="23", lot="7", typ="ROAD"), dist=49.9)
    d = decide(lot_rec(), -33.8, 151.0, [inside], MatchConfig(lot_distance_m=50.0))
    assert (d.outcome, d.rule, d.pid) == ("link", "LOT_GEOCODE", "PN") and d.rules == ("LOT_RESOLVED_BY_GEOCODE",) and 0 < d.score < 0.01
    edge = C("PN", A(number="23", lot="7", typ="ROAD"), dist=50.0)
    assert decide(lot_rec(), -33.8, 151.0, [edge], MatchConfig(lot_distance_m=50.0)).outcome == "link"          # at the limit still counts
    over = C("PN", A(number="23", lot="7", typ="ROAD"), dist=50.1)
    d = decide(lot_rec(), -33.8, 151.0, [over], MatchConfig(lot_distance_m=50.0))
    assert d.outcome == "quarantine" and d.reason == "LOT_TOO_FAR" and d.detail == {"nearest_m": 50.1, "limit_m": 50.0}


def test_distance_limit_is_the_configured_one():
    c = C("PN", A(number="23", lot="7", typ="ROAD"), dist=120.0)
    assert decide(lot_rec(), -33.8, 151.0, [c], MatchConfig(lot_distance_m=150.0)).outcome == "link"
    assert decide(lot_rec(), -33.8, 151.0, [c], MatchConfig(lot_distance_m=100.0)).outcome == "quarantine"


def test_two_numbered_addresses_inside_the_distance_are_a_tie():
    cs = [C("P1", A(number="23", lot="7", typ="ROAD"), dist=10.0), C("P2", A(number="25", lot="7", typ="ROAD"), dist=20.0)]
    assert decide(lot_rec(), -33.8, 151.0, cs, CFG).reason == "AMBIGUOUS_TIE"


def test_a_different_lot_does_not_match():
    c = C("PN", A(number="23", lot="8", typ="ROAD"), dist=1.0)
    assert decide(lot_rec(), -33.8, 151.0, [c], CFG).outcome == "new"


def test_a_lot_that_is_not_in_the_reference_is_new():
    assert decide(lot_rec(), None, None, [], CFG).outcome == "new"


# ---- order does not matter ------------------------------------------------------------------------------------------------------------------

def _random_cands(rng: random.Random):
    out = []
    for i in range(rng.randint(0, 6)):
        out.append(C(f"P{i}", A(number=rng.choice(["12", "12", "21", "14"]), unit=rng.choice(["", "5", "5", "6"]), street=rng.choice(["SMITH", "SMYTH", "HARWICK"]),
                                 locality=rng.choice(["FAKEVILLE", "FAKEVILLE", "OTHERTON"])), sim=rng.choice([1.0, 0.8, 0.72, 0.5, 0.31]), dist=rng.choice([None, 5.0, 40.0, 90.0]),
                     same_postcode=rng.choice([True, False])))
    return out


@given(st.integers(0, 10_000), st.integers(0, 10_000))
def test_the_order_of_the_candidates_never_changes_the_decision(seed, shuffle_seed):
    rng = random.Random(seed)
    rec = A(number=rng.choice(["12", "21", "14"]), unit=rng.choice(["", "5", "6"]), street=rng.choice(["SMITH", "SMYTH"]), locality="FAKEVILLE")
    cands = _random_cands(rng)
    base = decide(rec, -33.8, 151.0, cands, MatchConfig(threshold=0.6))
    shuffled = list(cands)
    random.Random(shuffle_seed).shuffle(shuffled)
    assert decide(rec, -33.8, 151.0, shuffled, MatchConfig(threshold=0.6)) == base


@given(st.integers(0, 10_000))
def test_a_link_never_pairs_two_different_units(seed):
    rng = random.Random(seed)
    rec = A(number="12", unit=rng.choice(["", "1", "2", "3", "15", "51"]))
    cands = [C(f"P{i}", A(number="12", unit=rng.choice(["", "1", "2", "3", "15", "51"])), sim=rng.choice([1.0, 0.9, 0.7])) for i in range(rng.randint(1, 6))]
    d = decide(rec, None, None, cands, MatchConfig(threshold=0.5))
    if d.outcome == "link":
        chosen = next(c for c in cands if c.pid == d.pid)
        assert chosen.addr.unit == rec.unit


def test_decision_is_a_plain_value():
    assert Decision("link", "EXACT_KEY", 1.0, "P1") == Decision("link", "EXACT_KEY", 1.0, "P1")
