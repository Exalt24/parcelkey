"""The data quality report and the command line as a real process."""
from __future__ import annotations

import json
import os
import subprocess
import sys


import pytest

import helpers as h
from conftest import AGENCY, ROOT
from parcelkey import db, quality

pytestmark = pytest.mark.db

REFERENCE = [h.addr("G12", "12"), h.addr("GT5", "30", unit="5", level="3"), h.addr("GT6", "30", unit="6", level="3")]


@pytest.fixture()
def finished(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[
        h.crm("C1", "12 smith st", name="Kate", email="Kate@Example.com"), h.crm("C2", "U5/30 Smith St", postcode="", name="Kate", email="kate@example.com", phone="tbc"),
        h.crm("C3", "Unit 99, 30 Smith St"), h.crm("C4", "18 Smith St"), h.crm("C5", "Flat 5", email="nope"), h.crm("", "7 Elm St"), h.crm("C1", "99 Dup St")],
        listing_rows=[h.listing("L1", "Lot 7 Smith Rd", lat="-33.8", lon="151.0")])
    h.run(conn, AGENCY, folder)
    return folder


def test_quality_report_counts_what_the_feeds_looked_like(finished, conn):
    with db.agency_tx(conn, AGENCY) as cur:
        r = quality.quality_report(cur, AGENCY)
    assert r["sources"] == {"crm": {"stored": 5, "rejected": 2, "unchanged": 0}, "listing": {"stored": 1, "rejected": 0, "unchanged": 0}}
    assert r["ingest_rejects"] == {"DUPLICATE_REF": 1, "MISSING_REF": 1}
    assert r["records"] == {"total": 6, "normalised_ok": 5, "unparseable": 1, "missing_postcode": 1, "with_unit": 3, "lot_only": 1, "with_coordinates": 1}
    assert r["normalisation_rules_fired"]["POSTCODE_MISSING"] == 1 and r["normalisation_rules_fired"]["CASE_FOLDED"] >= 3
    assert r["links_by_rule"] == {"EXACT_KEY": 2, "NEW_DERIVED": 2}
    assert r["quarantine"]["UNIT_CONFLICT/open"] == 1 and r["quarantine"]["UNPARSEABLE/open"] == 1
    assert r["contacts"] == {"mentions": 2, "mentions_without_identifier": 0, "invalid_emails": 1, "invalid_phones": 1, "emails_lowercased": 1, "contacts": 1, "merged_away": 0, "quarantined": 0}


def test_quality_text_is_short_and_plain(finished, conn):
    with db.agency_tx(conn, AGENCY) as cur:
        text = quality.render(quality.quality_report(cur, AGENCY))
    assert text.startswith("data quality for agency-a") and "links by rule" in text and "contact mentions" in text and len(text.splitlines()) <= 14


# ---- the command line, as a process --------------------------------------------------------------------------------------------------------

def cli(*args, check=False):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    p = subprocess.run([sys.executable, "-m", "parcelkey", *args], capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT, timeout=120)
    if check:
        assert p.returncode == 0, p.stdout + p.stderr
    return p


def test_run_reconcile_explain_quarantine_quality_through_the_command_line(hand, clean, tmp_path):
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com"), h.crm("C2", "Unit 99, 30 Smith St"), h.crm("C3", "18 Smith St")])
    p = cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency", check=True)
    assert "ingest     crm" in p.stdout and "linked 2 (EXACT_KEY 1, NEW_DERIVED 1)" in p.stdout and "quarantined 1 (UNIT_CONFLICT 1)" in p.stdout and "timings" in p.stdout
    assert "skipped" in cli("run", "--agency", AGENCY, "--feeds", str(folder), check=True).stdout
    p = cli("reconcile", "--agency", AGENCY, "--feeds", str(folder))
    assert p.returncode == 0 and "RECONCILED" in p.stdout
    p = cli("explain", "--agency", AGENCY, "crm:C2")
    assert p.returncode == 0 and "quarantine UNIT_CONFLICT" in p.stdout and "UNIT_MARKER_WORD" in p.stdout
    p = cli("explain", "--agency", AGENCY, "crm:C1")
    assert "linked     pk_g_" in p.stdout and "by EXACT_KEY score 1.0000" in p.stdout and "contact    c_" in p.stdout
    assert cli("explain", "--agency", AGENCY, "crm:NOPE").returncode == 2
    p = cli("quarantine", "--agency", AGENCY)
    assert "UNIT_CONFLICT" in p.stdout and "1 shown" in p.stdout and "Unit 99, 30 Smith St" in p.stdout
    assert "0 shown" in cli("quarantine", "--agency", AGENCY, "--reason", "LOT_TOO_FAR").stdout
    q = json.loads(cli("quality", "--agency", AGENCY, "--json").stdout)
    assert q["records"]["total"] == 3
    assert "data quality" in cli("quality", "--agency", AGENCY).stdout


def test_reconcile_exits_1_when_the_counts_do_not_add_up(hand, clean, admin, tmp_path):
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[h.crm("C1", "12 Smith St")])
    cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency", check=True)
    admin.execute("delete from property_link")
    p = cli("reconcile", "--agency", AGENCY)
    assert p.returncode == 1 and "FAIL ACCOUNTED" in p.stdout and "NOT RECONCILED" in p.stdout


def test_freshness_exits_1_when_stale_and_0_when_fresh(hand, clean, tmp_path):
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[h.crm("C1", "12 Smith St")])
    cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency", check=True)
    fresh = cli("freshness", "--agency", AGENCY, "--max-age", "crm=24", "--max-age", "listing=24", "--max-age", "sales=24")
    assert fresh.returncode == 1 and "MISSING" in fresh.stdout                           # listing and sales never arrived
    only = cli("freshness", "--agency", AGENCY, "--max-age", "listing=24", "--max-age", "sales=24", "--now", "2000-01-01T00:00:00+00:00")
    assert only.returncode == 1
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    late = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert "FRESH" in cli("freshness", "--agency", AGENCY, "--now", soon).stdout
    stale = cli("freshness", "--agency", AGENCY, "--now", late)
    assert stale.returncode == 1 and "STALE" in stale.stdout
    assert cli("freshness", "--agency", AGENCY, "--max-age", "bad").returncode == 2


def test_a_refused_feed_exits_2_and_writes_nothing(hand, clean, admin, tmp_path):
    hand(REFERENCE)
    folder = tmp_path / "feeds"
    folder.mkdir()
    (folder / "crm_export.csv").write_text("contact_ref,address\nC1,12 Smith St\n", encoding="utf-8")
    p = cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency")
    assert p.returncode == 2 and "REFUSED" in p.stderr and "full_name" in p.stderr
    assert admin.execute("select count(*) from source_record").fetchone()[0] == 0


def test_evaluate_and_threshold_options(world, tmp_path):
    p = cli("run", "--agency", "agency-a", "--feeds", str(world / "agency-a"), "--threshold", "0.5", "--lot-distance", "80", "--new-agency", check=True)
    assert "match" in p.stdout
    e = cli("evaluate", "--agency", "agency-a", "--key", str(world / "answer_key.csv"), "--truth", str(world / "truth_properties.csv"), check=True)
    assert "precision" in e.stdout and "contact class" in e.stdout
    j = json.loads(cli("evaluate", "--agency", "agency-a", "--key", str(world / "answer_key.csv"), "--truth", str(world / "truth_properties.csv"), "--json", check=True).stdout)
    assert j["precision"] > 0.9 and j["recall"] > 0.9


def test_load_gnaf_through_the_command_line(clean, world_dir):
    db.wipe(clean, reference=True)
    from conftest import _STATE
    _STATE["reference"] = ""
    p = cli("load-gnaf", str(world_dir / "gnaf"), check=True)
    assert "loaded 33 files" in p.stdout and "reference table built" in p.stdout
    p = cli("load-gnaf", str(world_dir / "gnaf"), "--no-build", check=True)
    assert "0 new rows" in p.stdout and "reference table built" not in p.stdout


def test_init_db_is_safe_to_run_again():
    assert "database ready" in cli("init-db", check=True).stdout


def test_help_lists_every_command():
    out = cli("--help", check=True).stdout
    for name in ("init-db", "load-gnaf", "run", "reconcile", "freshness", "quality", "evaluate", "quarantine", "explain"):
        assert name in out


def test_render_prints_every_line_of_a_known_report():
    report = {
        "agency": "agency-a", "sources": {"crm": {"stored": 5, "rejected": 2, "unchanged": 1}}, "ingest_rejects": {"DUPLICATE_REF": 2},
        "records": {"total": 6, "normalised_ok": 5, "unparseable": 1, "missing_postcode": 2, "with_unit": 3, "lot_only": 1, "with_coordinates": 4},
        "normalisation_rules_fired": {"CASE_FOLDED": 6, "POSTCODE_MISSING": 2}, "links_by_rule": {"EXACT_KEY": 4, "NEW_DERIVED": 1}, "postcode_mismatch_links": 3,
        "quarantine": {"UNIT_CONFLICT/open": 1}, "properties_used": {"derived": 1, "gnaf": 4},
        "contacts": {"mentions": 4, "mentions_without_identifier": 1, "invalid_emails": 2, "invalid_phones": 3, "emails_lowercased": 4, "contacts": 3, "merged_away": 1, "quarantined": 0},
    }
    text = quality.render(report)
    assert text.splitlines() == [
        "data quality for agency-a",
        "  feed crm      stored     5  rejected   2  unchanged   1",
        "  rejects: DUPLICATE_REF 2",
        "  records 6: normalised 5, unparseable 1, no postcode 2, with a unit 3, lot only 1, with coordinates 4",
        "  rules fired most: CASE_FOLDED 6, POSTCODE_MISSING 2",
        "  links by rule: EXACT_KEY 4, NEW_DERIVED 1 (postcode did not agree on 3)",
        "  quarantine: UNIT_CONFLICT/open 1",
        "  properties used: derived 1, gnaf 4",
        "  contact mentions 4 -> 3 contacts (1 merged away, 0 set aside); no identifier 1, bad email 2, bad phone 3",
    ]
    quiet = {**report, "ingest_rejects": {}, "quarantine": {}}
    lines = quality.render(quiet).splitlines()
    assert len(lines) == 8 and "  quarantine: none" in lines and not any(line.startswith("  rejects") for line in lines)
