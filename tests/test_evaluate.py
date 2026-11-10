"""The measurement itself, on a small world whose answers are written by hand, so the arithmetic is checked and not only the code that does it."""
from __future__ import annotations

import csv

import pytest

import helpers as h
from conftest import AGENCY
from parcelkey import db, evaluate as ev
from parcelkey.evaluate import OUTCOMES, load_key, load_truth, render

pytestmark = pytest.mark.db

REFERENCE = [h.addr("G12", "12"), h.addr("G14", "14"), h.addr("GT5", "30", unit="5"), h.addr("GT6", "30", unit="6")]
KEY_COLUMNS = ["agency_id", "record_id", "class", "expected", "truth_pid", "truth_canon_key", "kind", "building_id", "unit", "sibling", "person_id", "contact_class"]


def key_row(rid, cls, expected, pid="", canon="", building="", unit="", person="", cclass=""):
    return {"agency_id": AGENCY, "record_id": rid, "class": cls, "expected": expected, "truth_pid": pid, "truth_canon_key": canon, "kind": "", "building_id": building, "unit": unit,
            "sibling": "0", "person_id": person, "contact_class": cclass}


def write(path, rows, cols):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture()
def finished(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    rows = [h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com"), h.crm("C2", "14 Smyth St"), h.crm("C3", "Unit 5, 30 Smith St"), h.crm("C4", "Unit 9, 30 Smith St"),
            h.crm("C5", "18 Smith St"), h.crm("C6", "21 Smith St"), h.crm("C7", "12 Smith St", name="K Smith", email="KATE@example.com"), h.crm("C8", "14 Smith St", name="Kate Smith")]
    folder = h.write_feeds(tmp_path / "f", crm_rows=rows)
    h.run(conn, AGENCY, folder, h.MatchConfig(threshold=0.5))
    return conn, tmp_path


def truth_file(tmp_path):
    return write(tmp_path / "truth.csv", [{"pid": "G12", "building_id": "", "unit": "", "kind": "house", "canon_key": ""}, {"pid": "GT5", "building_id": "B30", "unit": "5", "kind": "tower_flat", "canon_key": ""},
                                           {"pid": "GT6", "building_id": "B30", "unit": "6", "kind": "tower_flat", "canon_key": ""}, {"pid": "G14", "building_id": "", "unit": "", "kind": "house", "canon_key": ""}],
                 ["pid", "building_id", "unit", "kind", "canon_key"])


def test_precision_recall_and_the_class_table_from_a_hand_made_key(finished):
    conn, tmp_path = finished
    derived = "|||18|SMITH STREET|FAKEVILLE|NSW"
    key = write(tmp_path / "key.csv", [
        key_row("crm:C1", "clean", "link", pid="G12", person="P1", cclass="contact_clean"), key_row("crm:C2", "street_typo", "link", pid="G14"),
        key_row("crm:C3", "unit_word", "link", pid="GT5", building="B30", unit="5"), key_row("crm:C4", "unit_unknown", "quarantine"),
        key_row("crm:C5", "not_in_gnaf", "link", canon=derived), key_row("crm:C6", "number_transposed", "quarantine", pid="G12"),
        key_row("crm:C7", "name_variant", "link", pid="G12", person="P1", cclass="name_variant"), key_row("crm:C8", "clean", "link", pid="G12")], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), load_truth(truth_file(tmp_path)))
    t = res["totals"]
    # C1 ok, C2 ok (near miss), C3 ok, C4 set aside as expected, C5 ok (derived), C6 set aside as expected, C7 ok, C8 linked to G14 but the key says G12: wrong
    assert (t["resolved"], t["wrong"], t["quarantined"], t["missed"], t["unprocessed"]) == (5, 1, 2, 0, 0)
    assert res["links_made"] == 6 and res["precision"] == pytest.approx(5 / 6) and res["recall"] == pytest.approx(5 / 6) and res["records_expected_to_link"] == 6
    assert res["by_class"]["clean"] == {"resolved": 1, "quarantined": 0, "missed": 0, "wrong": 1, "unprocessed": 0}
    assert res["by_class"]["unit_unknown"]["quarantined"] == 1 and res["by_class"]["number_transposed"]["quarantined"] == 1
    assert res["different_unit_merges"] == 0
    c = res["contacts"]
    assert c["mentions"] == 2 and c["pairs_merged"] == 1 and c["pairs_correct"] == 1 and c["pair_precision"] == 1.0 and c["pair_recall"] == 1.0
    assert c["by_class"] == {"contact_clean": {"resolved": 1, "missed": 0, "wrong": 0, "quarantined": 0}, "name_variant": {"resolved": 1, "missed": 0, "wrong": 0, "quarantined": 0}}


def test_a_link_to_a_sibling_unit_is_counted_as_a_different_unit_merge(finished):
    conn, tmp_path = finished
    key = write(tmp_path / "key.csv", [key_row("crm:C3", "unit_word", "link", pid="GT6", building="B30", unit="6")], KEY_COLUMNS)           # the key claims C3 is unit 6, the pipeline linked unit 5
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), load_truth(truth_file(tmp_path)))
    assert res["totals"]["wrong"] == 1 and res["different_unit_merges"] == 1


def test_a_record_set_aside_that_the_key_says_could_link_is_missed_and_a_record_never_processed_is_reported(finished):
    conn, tmp_path = finished
    key = write(tmp_path / "key.csv", [key_row("crm:C4", "unit_word", "link", pid="GT5", building="B30", unit="5"), key_row("crm:NOPE", "clean", "link", pid="G12")], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), load_truth(truth_file(tmp_path)))
    assert (res["totals"]["missed"], res["totals"]["unprocessed"], res["totals"]["resolved"]) == (1, 1, 0) and res["recall"] == 0.0


def test_a_link_on_a_record_the_key_says_to_set_aside_lowers_precision(finished):
    conn, tmp_path = finished
    key = write(tmp_path / "key.csv", [key_row("crm:C1", "lot_no_geo", "quarantine"), key_row("crm:C3", "clean", "link", pid="GT5", building="B30", unit="5")], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), load_truth(truth_file(tmp_path)))
    assert res["totals"]["wrong"] == 1 and res["precision"] == 0.5 and res["recall"] == 1.0


def test_contact_outcomes_wrong_missed_and_resolved(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    rows = [h.crm("C1", "12 Smith St", name="A", email="a@example.com"), h.crm("C2", "12 Smith St", name="A", email="a@example.com"), h.crm("C3", "12 Smith St", name="A2", email="a2@example.com"),
            h.crm("C4", "12 Smith St", name="B", email="b@example.com", phone="02 5550 0001"), h.crm("C5", "12 Smith St", name="C", phone="02 5550 0001")]
    h.run(conn, AGENCY, h.write_feeds(tmp_path / "f", crm_rows=rows))
    key = write(tmp_path / "key.csv", [
        key_row("crm:C1", "x", "link", pid="G12", person="PA", cclass="contact_clean"), key_row("crm:C2", "x", "link", pid="G12", person="PA", cclass="email_case"),
        key_row("crm:C3", "x", "link", pid="G12", person="PA", cclass="dup_no_shared_id"),
        key_row("crm:C4", "x", "link", pid="G12", person="PB", cclass="contact_clean"), key_row("crm:C5", "x", "link", pid="G12", person="PC", cclass="same_name_different_person")], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), {})
    by = res["contacts"]["by_class"]
    assert by["email_case"]["resolved"] == 1 and by["dup_no_shared_id"]["missed"] == 1
    assert by["same_name_different_person"]["wrong"] == 1 and by["contact_clean"] == {"resolved": 1, "missed": 0, "wrong": 1, "quarantined": 0}
    c = res["contacts"]
    assert (c["true_pairs"], c["pairs_merged"], c["pairs_correct"]) == (3, 2, 1) and c["pair_precision"] == 0.5 and c["pair_recall"] == pytest.approx(1 / 3)


def test_render_prints_the_table_and_the_numbers(finished):
    conn, tmp_path = finished
    key = write(tmp_path / "key.csv", [key_row("crm:C1", "clean", "link", pid="G12", person="P1", cclass="contact_clean")], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), {})
    text = render(res)
    assert "precision 100.00% (1 of 1)" in text and "recall 100.00% (of 1 records that should link)" in text and "clean" in text and "contacts: 1 mentions" in text
    assert set(OUTCOMES) == {"resolved", "quarantined", "missed", "wrong"}


def test_the_key_of_another_agency_is_ignored(tmp_path):
    rows = [key_row("crm:C1", "clean", "link"), {**key_row("crm:C2", "clean", "link"), "agency_id": "agency-z"}]
    key = write(tmp_path / "key.csv", rows, KEY_COLUMNS)
    assert set(load_key(key, AGENCY)) == {"crm:C1"}
