"""Freshness monitor: each source's last load against its maximum age, with a nonzero exit when anything is stale or missing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import helpers as h
from conftest import AGENCY, OTHER
from parcelkey import db
from parcelkey.freshness import DEFAULT_MAX_AGE, FreshnessRow, check, exit_code, parse_max_age

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def test_parse_max_age_keeps_defaults_and_overrides_named_sources():
    assert parse_max_age([]) == DEFAULT_MAX_AGE
    got = parse_max_age(["crm=12", "extra=0.5"])
    assert got["crm"] == timedelta(hours=12) and got["extra"] == timedelta(minutes=30) and got["listing"] == DEFAULT_MAX_AGE["listing"]


@pytest.mark.parametrize("bad", ["crm", "=5", "crm=abc", "crm=0", "crm=-3"])
def test_parse_max_age_rejects_nonsense(bad):
    with pytest.raises(ValueError, match="--max-age"):
        parse_max_age([bad])


def row(status, age_h=1.0, max_h=24.0):
    return FreshnessRow("crm", NOW, timedelta(hours=age_h), timedelta(hours=max_h), status)


def test_exit_code():
    assert exit_code([row("FRESH"), row("FRESH")]) == 0
    assert exit_code([row("FRESH"), row("STALE")]) == 1
    assert exit_code([row("MISSING")]) == 1 and exit_code([]) == 0


@pytest.mark.db
def test_fresh_stale_and_missing(conn, clean, tmp_path):
    f = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")], listing_rows=[h.listing("L1", "12 Smith St")])
    h.run(conn, AGENCY, f, now=NOW - timedelta(hours=30))
    with db.agency_tx(conn, AGENCY) as cur:
        rows = {r.source: r for r in check(cur, AGENCY, {"crm": timedelta(days=7), "listing": timedelta(hours=24), "sales": timedelta(days=31)}, NOW)}
    assert (rows["crm"].status, rows["listing"].status, rows["sales"].status) == ("FRESH", "STALE", "MISSING")
    assert rows["listing"].age == timedelta(hours=30) and rows["sales"].last_loaded_at is None and rows["sales"].age is None
    assert "STALE" in rows["listing"].line() and "never" in rows["sales"].line() and "age 30.0 h" in rows["listing"].line()


@pytest.mark.db
def test_exactly_the_maximum_age_is_still_fresh(conn, clean, tmp_path):
    f = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    h.run(conn, AGENCY, f, now=NOW - timedelta(hours=24))
    with db.agency_tx(conn, AGENCY) as cur:
        assert check(cur, AGENCY, {"crm": timedelta(hours=24)}, NOW)[0].status == "FRESH"
        assert check(cur, AGENCY, {"crm": timedelta(hours=23, minutes=59)}, NOW)[0].status == "STALE"


@pytest.mark.db
def test_the_newest_load_counts(conn, clean, tmp_path):
    a = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")])
    b = h.write_feeds(tmp_path / "b", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "14 Smith St")])
    h.run(conn, AGENCY, a, now=NOW - timedelta(days=30))
    h.run(conn, AGENCY, b, now=NOW - timedelta(hours=2))
    with db.agency_tx(conn, AGENCY) as cur:
        r = check(cur, AGENCY, {"crm": timedelta(days=7)}, NOW)[0]
    assert r.status == "FRESH" and r.age == timedelta(hours=2)


@pytest.mark.db
def test_a_feed_delivered_again_unchanged_does_not_refresh_its_age(conn, clean, tmp_path):
    f = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    h.run(conn, AGENCY, f, now=NOW - timedelta(days=10))
    h.run(conn, AGENCY, f, now=NOW)
    with db.agency_tx(conn, AGENCY) as cur:
        assert check(cur, AGENCY, {"crm": timedelta(days=7)}, NOW)[0].status == "STALE"


@pytest.mark.db
def test_each_agency_is_measured_on_its_own(conn, clean, tmp_path):
    f = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    h.run(conn, AGENCY, f, now=NOW)
    with db.agency_tx(conn, OTHER) as cur:
        assert check(cur, OTHER, {"crm": timedelta(days=7)}, NOW)[0].status == "MISSING"


def test_the_default_maximum_ages_are_a_week_a_day_and_a_month():
    assert DEFAULT_MAX_AGE == {"crm": timedelta(days=7), "listing": timedelta(days=1), "sales": timedelta(days=31)}
    assert parse_max_age([]) == {"crm": timedelta(hours=168), "listing": timedelta(hours=24), "sales": timedelta(hours=744)}


def test_a_freshness_row_is_immutable():
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        row("FRESH").status = "STALE"
