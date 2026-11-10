"""Hypothesis properties through the real database: a rerun is a no-op, the order of the rows does not change any decision, different units never merge, every row is accounted for."""
from __future__ import annotations

import random

import pytest
from hypothesis import given, settings, strategies as st

import helpers as h
from conftest import AGENCY, snapshot
from parcelkey import db
from parcelkey.reconcile import reconcile

pytestmark = pytest.mark.db

UNITS = [str(n) for n in range(1, 9)]
REFERENCE = ([h.addr(f"GU{u}", "30", unit=u) for u in UNITS] + [h.addr("G12", "12"), h.addr("G14", "14"), h.addr("G16", "16", street="HARWICK"), h.addr("G16B", "16", street="HARDWICK"),
                                                                    h.addr("GOTHER", "12", locality="OTHERTON"), h.addr("GL", "", street="BASE", type="ROAD", lot="7"),
                                                                    h.addr("GLN", "23", street="BASE", type="ROAD", lot="9", lat=-33.81, lon=151.01)])

FORMS = ["Unit {u}, {n} {s}", "{u}/{n} {s}", "Apt {u} {n} {s}", "U{u}/{n} {s}", "Flat {u}, {n} {s}", "{n} {s}", "Lot {u} {s}"]
STREETS = ["Smith St", "SMITH STREET", "smith st.", "Smyth St", "Harwik St", "Harwick Street", "Base Rd"]


@st.composite
def feed_rows(draw):
    n = draw(st.integers(1, 14))
    rows = []
    for i in range(n):
        form = draw(st.sampled_from(FORMS))
        number = draw(st.sampled_from(["30", "30", "30", "12", "14", "16", "21", "18", "99"]))
        unit = draw(st.sampled_from(UNITS + ["9", "12", "15", "51", "7"]))
        street = draw(st.sampled_from(STREETS))
        suburb = draw(st.sampled_from(["Fakeville", "Fakeville", "Otherton"]))
        pc = draw(st.sampled_from(["2000", "", "2999"]))
        email = draw(st.sampled_from(["", "a@example.com", "A@EXAMPLE.COM", "b@example.com"]))
        phone = draw(st.sampled_from(["", "02 5550 0001", "(02) 5550 0001", "02 5550 0002"]))
        rows.append(h.crm(f"C{i:03d}", form.format(u=unit, n=number, s=street), suburb=suburb, postcode=pc, name="X", email=email, phone=phone))
    return rows


def fresh(clean, hand):
    db.wipe(clean)
    hand(REFERENCE)


@given(feed_rows())
@settings(max_examples=20)
def test_a_second_run_never_writes_anything(clean, hand, conn, admin, tmp_path_factory, rows):
    fresh(clean, hand)
    folder = h.write_feeds(tmp_path_factory.mktemp("p"), crm_rows=rows)
    h.run(conn, AGENCY, folder)
    before = snapshot(admin)
    again = h.run(conn, AGENCY, folder)
    assert snapshot(admin) == before
    assert sum(again.match.linked.values()) == 0 and again.contacts.records == 0 and again.normalised == 0


@given(feed_rows(), st.integers(0, 10_000))
@settings(max_examples=20)
def test_the_order_of_the_rows_never_changes_a_decision(clean, hand, conn, admin, tmp_path_factory, rows, seed):
    fresh(clean, hand)
    h.run(conn, AGENCY, h.write_feeds(tmp_path_factory.mktemp("o1"), crm_rows=rows))
    first = h.decisions(admin, AGENCY)
    scores_first = {r[0]: (r[1], r[2]) for r in admin.execute("select record_id, rule, score from property_link").fetchall()}
    db.wipe(clean)
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    h.run(conn, AGENCY, h.write_feeds(tmp_path_factory.mktemp("o2"), crm_rows=shuffled))
    assert h.decisions(admin, AGENCY) == first
    assert {r[0]: (r[1], r[2]) for r in admin.execute("select record_id, rule, score from property_link").fetchall()} == scores_first


@given(feed_rows())
@settings(max_examples=25)
def test_a_link_never_pairs_two_different_units(clean, hand, conn, admin, tmp_path_factory, rows):
    fresh(clean, hand)
    h.run(conn, AGENCY, h.write_feeds(tmp_path_factory.mktemp("u"), crm_rows=rows))
    ref_unit = {a["pid"]: a["unit"] for a in REFERENCE}
    for rid, pid, unit in admin.execute("""select l.record_id, p.gnaf_pid, r.unit from property_link l join property p using (property_id)
                                           join source_record r on r.agency_id = l.agency_id and r.record_id = l.record_id where p.origin = 'gnaf' and r.lot = ''""").fetchall():
        assert ref_unit[pid] == unit, (rid, pid, unit)
    for rid, canon, unit in admin.execute("""select l.record_id, p.canon_key, r.unit from property_link l join property p using (property_id)
                                              join source_record r on r.agency_id = l.agency_id and r.record_id = l.record_id where p.origin = 'derived'""").fetchall():
        assert canon.split("|")[1] == unit


@given(feed_rows())
@settings(max_examples=20)
def test_every_row_is_linked_or_set_aside_and_reconcile_agrees(clean, hand, conn, admin, tmp_path_factory, rows):
    fresh(clean, hand)
    folder = h.write_feeds(tmp_path_factory.mktemp("r"), crm_rows=rows)
    h.run(conn, AGENCY, folder)
    with db.agency_tx(conn, AGENCY) as cur:
        res = reconcile(cur, AGENCY, folder)
    assert res.ok, [str(f) for f in res.findings]
    linked = admin.execute("select count(*) from property_link").fetchone()[0]
    open_q = admin.execute("select count(*) from quarantine where status = 'open'").fetchone()[0]
    assert linked + open_q == len(rows)
