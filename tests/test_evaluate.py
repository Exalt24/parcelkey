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
    assert res["records_set_aside_by_policy"] == 2 and res["honest_recall"] == pytest.approx(5 / 8)          # the two records the key expects to be set aside count as misses here
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


# ---- cases added after the mutation check found lines no test pinned ---------------------------------------------------------------------------------

def evaluate_with(conn, tmp_path, rows, truth=None):
    key = write(tmp_path / "key.csv", rows, KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        return ev.evaluate(cur, AGENCY, load_key(key, AGENCY), truth if truth is not None else load_truth(truth_file(tmp_path)))


def test_a_link_is_right_only_when_the_kind_of_property_and_the_key_agree(finished):
    conn, tmp_path = finished
    derived = "|||18|SMITH STREET|FAKEVILLE|NSW"
    res = evaluate_with(conn, tmp_path, [
        key_row("crm:C1", "x", "link", pid="G12"),                       # linked to G12: right
        key_row("crm:C5", "x", "link", pid="G12"),                       # linked to a derived property but the key names a pid: wrong
        key_row("crm:C2", "x", "link", canon=derived),                   # linked to G14 but the key names a derived key: wrong
        key_row("crm:C7", "x", "link", pid="", canon="")])               # the key names nothing: wrong
    assert (res["totals"]["resolved"], res["totals"]["wrong"]) == (1, 3)


def test_different_unit_merges_are_counted_only_for_a_wrong_link_to_another_unit_of_the_same_building(finished):
    conn, tmp_path = finished
    truth = {"GT5": {"pid": "GT5", "building_id": "B30", "unit": "5"}, "GT6": {"pid": "GT6", "building_id": "B30", "unit": "6"}, "G12": {"pid": "G12", "building_id": "B30", "unit": "5"},
             "G14": {"pid": "G14", "building_id": "B99", "unit": "6"}}
    rows = [key_row("crm:C3", "x", "link", pid="GT6", building="B30", unit="6"),          # linked GT5: another unit of the same building: counted
            key_row("crm:C1", "x", "link", pid="GT5", building="B30", unit="5"),          # linked G12: same building, same unit: wrong but not a unit merge
            key_row("crm:C2", "x", "link", pid="GT5", building="B77", unit="6"),          # linked G14: another building: not counted
            key_row("crm:C5", "x", "link", pid="GT5", building="", unit="6")]             # no building in the key: not counted
    assert evaluate_with(conn, tmp_path, rows, truth)["different_unit_merges"] == 1


def test_a_link_to_a_derived_property_is_never_a_unit_merge(finished):
    conn, tmp_path = finished
    truth = {"GT5": {"pid": "GT5", "building_id": "B30", "unit": "5"}}
    res = evaluate_with(conn, tmp_path, [key_row("crm:C5", "x", "link", pid="GT5", building="B30", unit="6")], truth)
    assert res["totals"]["wrong"] == 1 and res["different_unit_merges"] == 0


def test_a_person_with_no_contact_record_is_not_a_mention(finished):
    conn, tmp_path = finished
    res = evaluate_with(conn, tmp_path, [key_row("crm:NOPE", "x", "link", pid="G12", person="PX", cclass="contact_clean")])
    assert res["contacts"]["mentions"] == 0 and res["contacts"]["by_class"] == {}


def test_a_mention_set_aside_by_the_cap_is_reported_as_set_aside(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    rows = [h.crm(f"C{i}", "12 Smith St", name="X", email=f"p{i}@example.com", phone="02 5550 0100") for i in range(4)]
    h.run(conn, AGENCY, h.write_feeds(tmp_path / "f", crm_rows=rows), max_cluster=3)
    res = evaluate_with(conn, tmp_path, [key_row(f"crm:C{i}", "x", "link", pid="G12", person=f"P{i}", cclass="contact_clean") for i in range(4)], {})
    assert res["contacts"]["by_class"]["contact_clean"]["quarantined"] == 1 and res["contacts"]["mentions"] == 4


def test_render_prints_each_class_row_and_each_contact_row():
    ev_ = {"agency": "agency-a", "records": 3, "totals": {"resolved": 1, "wrong": 1, "quarantined": 1, "missed": 0, "unprocessed": 0}, "links_made": 2, "precision": 0.5, "recall": 0.25,
           "honest_recall": 0.125, "records_expected_to_link": 4, "records_set_aside_by_policy": 4, "different_unit_merges": 2,
           "by_class": {"clean": {"resolved": 1, "wrong": 1, "quarantined": 0, "missed": 0, "unprocessed": 0}, "lot_no_geo": {"resolved": 0, "wrong": 0, "quarantined": 1, "missed": 0, "unprocessed": 0}},
           "contacts": {"mentions": 4, "true_pairs": 2, "pairs_merged": 1, "pairs_correct": 1, "pair_precision": 1.0, "pair_recall": 0.5,
                        "by_class": {"name_variant": {"resolved": 3, "missed": 1, "wrong": 0, "quarantined": 0}}}}
    lines = render(ev_).splitlines()
    assert lines[0] == "measurement for agency-a against the answer key: 3 records"
    assert "precision 50.00% (1 of 2)" in lines[2] and "recall 25.00% (of 4 records that should link)" in lines[2]
    assert lines[3] == "  honest recall 12.50% (1 of 3: the 4 records the matcher's own policy sets aside count as misses)"
    assert lines[4] == "  merges of two different units in one building: 2"
    assert any(line.split() == ["clean", "2", "1", "0", "0", "1"] for line in lines) and any(line.split() == ["lot_no_geo", "1", "0", "1", "0", "0"] for line in lines)
    assert any(line.split() == ["name_variant", "4", "3", "1", "0", "0"] for line in lines)
    assert any("pairwise precision 100.00% (1 of 1 merged pairs), recall 50.00% (1 of 2 true pairs)" in line for line in lines)
    empty = render({**ev_, "precision": None, "recall": None, "honest_recall": None})
    assert "precision n/a" in empty and "recall n/a" in empty and "honest recall n/a" in empty


def test_a_correct_link_is_never_a_unit_merge_even_when_the_truth_table_disagrees_about_the_unit(finished):
    conn, tmp_path = finished
    truth = {"GT5": {"pid": "GT5", "building_id": "B30", "unit": "9"}}
    res = evaluate_with(conn, tmp_path, [key_row("crm:C3", "x", "link", pid="GT5", building="B30", unit="5")], truth)
    assert res["totals"]["resolved"] == 1 and res["different_unit_merges"] == 0


def test_class_honest_recall_counts_the_records_the_matchers_own_policy_sets_aside_as_misses(finished):
    """CLASS-TABLE: F11. recall = resolved / records expected to link; honest recall = resolved / (those + records the key expects to be set aside), so a policy record is a miss in the second
    figure and not in the first. Extra rows of either kind move only the figure they belong to."""
    import itertools
    conn, tmp_path = finished
    derived = "|||18|SMITH STREET|FAKEVILLE|NSW"
    base = [key_row("crm:C1", "clean", "link", pid="G12"), key_row("crm:C2", "street_typo", "link", pid="G14"), key_row("crm:C3", "unit_word", "link", pid="GT5", building="B30", unit="5"),
            key_row("crm:C4", "unit_unknown", "quarantine"), key_row("crm:C5", "not_in_gnaf", "link", canon=derived), key_row("crm:C6", "number_transposed", "quarantine", pid="G12"),
            key_row("crm:C7", "name_variant", "link", pid="G12"), key_row("crm:C8", "clean", "link", pid="G12")]
    rows = []
    for extra_link, extra_policy in itertools.product(range(3), range(4)):
        extra = [key_row(f"crm:X{i}", "clean", "link", pid="G12") for i in range(extra_link)] + [key_row(f"crm:Y{i}", "lot_no_geo", "quarantine") for i in range(extra_policy)]
        key = write(tmp_path / f"key-{extra_link}-{extra_policy}.csv", base + extra, KEY_COLUMNS)
        with db.agency_tx(conn, AGENCY) as cur:
            res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), load_truth(truth_file(tmp_path)))
        should, policy = 6 + extra_link, 2 + extra_policy
        rows.append(((extra_link, extra_policy), (res["records_expected_to_link"], res["records_set_aside_by_policy"], res["recall"], res["honest_recall"], res["precision"]),
                     (should, policy, pytest.approx(5 / should), pytest.approx(5 / (should + policy)), pytest.approx(5 / 6))))
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, bad[:3]
    only_links = write(tmp_path / "links.csv", base[:3] + base[4:5], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(only_links, AGENCY), load_truth(truth_file(tmp_path)))
    assert res["records_set_aside_by_policy"] == 0 and res["honest_recall"] == res["recall"] == 1.0
    empty = write(tmp_path / "empty.csv", [], KEY_COLUMNS)
    with db.agency_tx(conn, AGENCY) as cur:
        assert ev.evaluate(cur, AGENCY, load_key(empty, AGENCY), {})["honest_recall"] is None


def test_the_two_agency_totals_of_tools_measure_sum_the_counts_and_never_average_the_rates():
    import measure
    a = {"totals": {"resolved": 90, "wrong": 10, "missed": 5, "quarantined": 8}, "records": 130, "records_expected_to_link": 100, "records_set_aside_by_policy": 30}
    b = {"totals": {"resolved": 40, "wrong": 0, "missed": 2, "quarantined": 3}, "records": 60, "records_expected_to_link": 45, "records_set_aside_by_policy": 15}
    t = measure.both_agencies({"agencies": {"agency-a": {"evaluation": a}, "agency-b": {"evaluation": b}}})
    assert (t["records"], t["links_made"], t["resolved"], t["wrong"], t["missed"], t["quarantined"]) == (190, 140, 130, 10, 7, 11)
    assert t["precision"] == pytest.approx(130 / 140) and t["recall"] == pytest.approx(130 / 145) and t["honest_recall"] == pytest.approx(130 / 190)
    assert (t["records_expected_to_link"], t["policy_records"]) == (145, 45)


def test_honest_recall_is_defined_when_the_two_kinds_of_record_are_equal_in_number(finished):
    conn, tmp_path = finished
    key = write(tmp_path / "equal.csv", [key_row("crm:C1", "clean", "link", pid="G12"), key_row("crm:C4", "unit_unknown", "quarantine")], KEY_COLUMNS)          # one to link, one set aside by policy
    with db.agency_tx(conn, AGENCY) as cur:
        res = ev.evaluate(cur, AGENCY, load_key(key, AGENCY), load_truth(truth_file(tmp_path)))
    assert (res["records_expected_to_link"], res["records_set_aside_by_policy"], res["recall"], res["honest_recall"]) == (1, 1, 1.0, 0.5)
