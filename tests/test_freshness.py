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


# ---- a file that stores nothing never moves the age (P5-3) -------------------------------------------------------------------------------------------

DAY = {"crm": timedelta(hours=24)}


def idle_deliveries(tmp_path):
    """Deliveries that arrive after a first load of C1 and C2 and store nothing, each as (files folder, reject reason or None, the reject count)."""
    first = [h.crm("C1", "12 Smith St"), h.crm("C2", "14 Smith St")]
    return {
        "every row corrected": (h.write_feeds(tmp_path / "corrected", crm_rows=[h.crm("C1", "12 Smith Street New"), h.crm("C2", "14 Smith Street New")]), 2),
        "every row blank": (h.write_feeds(tmp_path / "blank", crm_rows=[h.crm("C1", " "), h.crm("C2", " ")]), 2),
        "the same rows in other bytes": (h.write_crm_text(tmp_path / "bytes", h.crm_text(h.crm_line("C1", "12 Smith St"), "", h.crm_line("C2", "14 Smith St"))), 0),
    }, first


@pytest.mark.db
def test_class_a_delivery_that_stores_nothing_leaves_the_age_where_it_was(conn, clean, admin, tmp_path):
    """CLASS-TABLE: every kind of delivery that applies no row (all corrected, all blank, same rows in other bytes) leaves loaded_at, the age and a STALE status alone, and is counted."""
    deliveries, first = idle_deliveries(tmp_path)
    for name, (folder, rejected) in deliveries.items():
        db.wipe(db.default_admin_dsn())
        c = h.write_feeds(tmp_path / ("first_" + name.replace(" ", "_")), crm_rows=first)
        h.run(conn, AGENCY, c, now=NOW - timedelta(hours=30))
        result = h.run(conn, AGENCY, folder, now=NOW)
        assert [(i.status, i.stored, i.rejected) for i in result.ingested] == [("stored_nothing", 0, rejected)], name
        loads = admin.execute("select stored, loaded_at, received_at from source_load order by load_id").fetchall()
        assert loads == [(2, NOW - timedelta(hours=30), NOW - timedelta(hours=30)), (0, None, NOW)], name
        with db.agency_tx(conn, AGENCY) as cur:
            row = check(cur, AGENCY, DAY, NOW)[0]
        assert (row.status, row.age, row.idle_loads, row.rejected_after) == ("STALE", timedelta(hours=30), 1, rejected), name
        assert f"since then 1 file(s) stored nothing, {rejected} row(s) rejected" in row.line(), name


@pytest.mark.db
def test_a_young_source_whose_latest_file_was_all_rejected_is_rejected_not_fresh(conn, clean, tmp_path):
    first = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")])
    again = h.write_feeds(tmp_path / "b", crm_rows=[h.crm("C1", "12 Smith St Changed")])
    h.run(conn, AGENCY, first, now=NOW - timedelta(hours=2))
    h.run(conn, AGENCY, again, now=NOW)
    with db.agency_tx(conn, AGENCY) as cur:
        row = check(cur, AGENCY, DAY, NOW)[0]
    assert (row.status, row.age, row.rejected_after) == ("REJECTED", timedelta(hours=2), 1) and exit_code([row]) == 1


@pytest.mark.db
def test_a_file_of_unchanged_rows_is_counted_but_does_not_make_the_source_rejected(conn, clean, tmp_path):
    first = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")])
    same = h.write_crm_text(tmp_path / "b", h.crm_text(h.crm_line("C1", "12 Smith St"), ""))
    h.run(conn, AGENCY, first, now=NOW - timedelta(hours=2))
    h.run(conn, AGENCY, same, now=NOW)
    with db.agency_tx(conn, AGENCY) as cur:
        row = check(cur, AGENCY, DAY, NOW)[0]
    assert (row.status, row.age, row.idle_loads, row.rejected_after) == ("FRESH", timedelta(hours=2), 1, 0) and "stored nothing, 0 row(s) rejected" in row.line()


@pytest.mark.db
def test_a_source_that_only_ever_received_rejected_files_is_missing_and_says_how_many(conn, clean, tmp_path):
    only = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", " "), h.crm("C2", " ")])
    h.run(conn, AGENCY, only, now=NOW)
    with db.agency_tx(conn, AGENCY) as cur:
        row = check(cur, AGENCY, DAY, NOW)[0]
    assert (row.status, row.last_loaded_at, row.age, row.idle_loads, row.rejected_after) == ("MISSING", None, None, 1, 2)
    assert "never" in row.line() and "2 row(s) rejected" in row.line()


@pytest.mark.db
def test_a_load_that_stores_a_row_moves_the_age_and_clears_the_count(conn, clean, tmp_path):
    first = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")])
    bad = h.write_feeds(tmp_path / "b", crm_rows=[h.crm("C1", "12 Smith St Changed")])
    good = h.write_feeds(tmp_path / "c", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "14 Smith St")])
    h.run(conn, AGENCY, first, now=NOW - timedelta(hours=40))
    h.run(conn, AGENCY, bad, now=NOW - timedelta(hours=20))
    h.run(conn, AGENCY, good, now=NOW - timedelta(hours=1))
    with db.agency_tx(conn, AGENCY) as cur:
        row = check(cur, AGENCY, DAY, NOW)[0]
    assert (row.status, row.age, row.idle_loads, row.rejected_after) == ("FRESH", timedelta(hours=1), 0, 0) and "since then" not in row.line()


@pytest.mark.db
def test_the_database_holds_a_load_time_exactly_when_the_load_stored_a_row(conn, admin, clean, tmp_path):
    import psycopg
    h.run(conn, AGENCY, h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")]), now=NOW)
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("update source_load set loaded_at = null")                  # stored rows but no load time
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("update source_load set stored = 0")                        # a load time but nothing stored


def test_the_freshness_row_line_names_the_files_and_rows_only_when_there_are_some():
    quiet = FreshnessRow("crm", NOW, timedelta(hours=1), timedelta(hours=24), "FRESH")
    noisy = FreshnessRow("crm", NOW, timedelta(hours=1), timedelta(hours=24), "REJECTED", 2, 7)
    assert "since then" not in quiet.line() and quiet.line().endswith("max 24 h")
    assert noisy.line().startswith("REJECTED") and noisy.line().endswith("since then 2 file(s) stored nothing, 7 row(s) rejected")
