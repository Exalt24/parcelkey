"""Deliberately planted bugs. Each test breaks one guard on purpose and requires that an existing test notices. If a planted bug ever passes, the suite has a hole there."""
from __future__ import annotations

import pytest

import test_match_unit as unit_tests
import test_normalise as normalise_tests
import test_pipeline as pipeline_tests
import test_tenant as tenant_tests
from parcelkey import match


def test_planted_bug_a_unit_conflict_that_never_conflicts_is_caught(monkeypatch):
    monkeypatch.setattr(match, "units_conflict", lambda a, b: False)
    with pytest.raises(AssertionError):
        unit_tests.test_a_different_unit_never_links_even_when_everything_else_is_equal("5", ["4", "6", "7"])
    with pytest.raises(AssertionError):
        unit_tests.test_the_right_unit_wins_among_siblings()


def test_planted_bug_a_lot_that_ignores_the_distance_is_caught(monkeypatch):
    original = match.decide

    def blind(rec, lat, lon, cands, cfg=match.MatchConfig()):
        return original(rec, lat, lon, cands, match.MatchConfig(lot_distance_m=1e9))

    monkeypatch.setattr(unit_tests, "decide", blind)
    with pytest.raises(AssertionError):
        unit_tests.test_a_lot_only_record_links_to_a_numbered_address_only_within_the_distance()


def test_planted_bug_an_email_that_is_not_lower_cased_is_caught(monkeypatch):
    monkeypatch.setattr(normalise_tests, "normalise_email", lambda raw: ((raw or "").strip(), ()))
    with pytest.raises(AssertionError):
        normalise_tests.test_email_forms("Foo.Bar@Example.COM", "foo.bar@example.com", {"EMAIL_LOWERCASED"})


def test_planted_bug_a_phone_that_keeps_its_trunk_zero_is_caught(monkeypatch):
    monkeypatch.setattr(normalise_tests, "normalise_phone", lambda raw: ("+610" + "".join(c for c in (raw or "") if c.isdigit())[1:], ("PHONE_E164",)))
    with pytest.raises(AssertionError):
        normalise_tests.test_phone_forms("0412 345 678", "+61412345678", {"PHONE_TRUNK_ZERO_STRIPPED"})


def test_planted_bug_a_threshold_that_is_ignored_is_caught(monkeypatch):
    original = match.decide
    monkeypatch.setattr(unit_tests, "decide", lambda rec, lat, lon, cands, cfg=match.MatchConfig(): original(rec, lat, lon, cands, match.MatchConfig(threshold=0.0)))
    with pytest.raises(AssertionError):
        unit_tests.test_near_miss_links_above_the_threshold_and_not_below()


@pytest.mark.db
def test_planted_bug_row_level_security_switched_off_is_caught(two_agencies):
    conn, admin = two_agencies
    admin.execute("alter table property_link disable row level security")
    try:
        with pytest.raises(AssertionError):
            tenant_tests.test_agency_b_cannot_read_agency_a_links_or_contacts(conn, admin)
    finally:
        admin.execute("alter table property_link enable row level security")
        admin.execute("alter table property_link force row level security")


@pytest.fixture()
def two_agencies(tmp_path, hand, conn, admin):
    hand(tenant_tests.REFERENCE)
    import helpers as h
    for agency, suffix in (("agency-a", "a"), ("agency-b", "b")):
        folder = h.write_feeds(tmp_path / suffix, crm_rows=[h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com", phone="02 5550 0001"), h.crm("C2", "Unit 9, 30 Smith St", name="Bob")],
                               listing_rows=[h.listing("L1", "14 Smith St", campaign=f"CMP-{suffix}")], sales_rows=[h.sale("S1", "12 Smith St, Fakeville NSW 2000", email="kate@example.com")])
        h.run(conn, agency, folder)
    return conn, admin


@pytest.mark.db
def test_planted_bug_a_quarantine_that_links_anyway_is_caught(monkeypatch, hand, conn, admin, tmp_path):
    original = match.decide

    def greedy(rec, lat, lon, cands, cfg=match.MatchConfig()):
        d = original(rec, lat, lon, cands, cfg)
        if d.outcome == "quarantine" and d.reason == "UNIT_CONFLICT" and cands:
            return match.Decision("link", "EXACT_KEY", 1.0, cands[0].pid)
        return d

    monkeypatch.setattr(match, "decide", greedy)
    hand(pipeline_tests.REFERENCE)
    with pytest.raises(AssertionError):
        pipeline_tests.test_tower_siblings_with_different_units_never_merge(None, conn, admin, tmp_path)
