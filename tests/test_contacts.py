"""Contact identity resolution: merge on a shared email or phone with the evidence kept, never on a name, never across agencies."""
from __future__ import annotations

import random

import pytest
from hypothesis import given, settings, strategies as st

import helpers as h
from conftest import AGENCY, OTHER, snapshot
from parcelkey import db
from parcelkey.contacts import contact_id_for

pytestmark = pytest.mark.db


def resolve(conn, admin, tmp_path, rows, agency=AGENCY, **kw):
    folder = h.write_feeds(tmp_path / f"{agency}-{len(list(tmp_path.iterdir()))}", crm_rows=rows)
    r = h.run(conn, agency, folder, **kw)
    links = {x[0]: x[1] for x in admin.execute("select record_id, contact_id from contact_link where agency_id = %s", (agency,)).fetchall()}
    return r, links


def person(ref, name="", email="", phone=""):
    return h.crm(ref, "12 Smith St", name=name, email=email, phone=phone)


def test_the_same_email_in_any_case_is_one_contact_with_the_evidence_recorded(clean, conn, admin, tmp_path):
    r, links = resolve(conn, admin, tmp_path, [person("C1", "Kate Smith", "kate@example.com"), person("C2", "K. Smith", " KATE@Example.COM ")])
    assert links["crm:C1"] == links["crm:C2"] == contact_id_for(AGENCY, "crm:C1")
    ev = admin.execute("select rule, evidence from contact_link where record_id = 'crm:C2'").fetchone()
    assert ev[0] == "EMAIL" and ev[1]["matched_on"] == ["email"] and ev[1]["earlier_records"] == ["crm:C1"] and ev[1]["identifiers"]["email"] == "kate@example.com"
    assert admin.execute("select rule from contact_link where record_id = 'crm:C1'").fetchone()[0] == "NEW"


def test_phone_in_several_formats_is_one_contact(clean, conn, admin, tmp_path):
    forms = ["0491 570 123", "(0491) 570 123", "+61 491 570 123", "0491570123", "0491-570-123"]
    r, links = resolve(conn, admin, tmp_path, [person(f"C{i}", f"Name {i}", "", f) for i, f in enumerate(forms)])
    assert len(set(links.values())) == 1
    assert admin.execute("select distinct rule from contact_link where record_id <> 'crm:C0'").fetchall() == [("PHONE",)]
    assert admin.execute("select value from contact_identifier").fetchall() == [("+61491570123",)]


def test_email_and_phone_together_name_both_in_the_evidence(clean, conn, admin, tmp_path):
    resolve(conn, admin, tmp_path, [person("C1", "A", "a@example.com", "0491 570 001"), person("C2", "A", "a@example.com", "(0491) 570 001")])
    rule, ev = admin.execute("select rule, evidence from contact_link where record_id = 'crm:C2'").fetchone()
    assert rule == "EMAIL+PHONE" and ev["matched_on"] == ["email", "phone"]


def test_a_name_alone_is_never_evidence(clean, conn, admin, tmp_path):
    r, links = resolve(conn, admin, tmp_path, [person("C1", "Kate Smith", "kate@example.com", "0491 570 001"), person("C2", "Kate Smith", "other@example.com", "0491 570 002"),
                                               person("C3", "Kate Smith", "", ""), person("C4", "Kate Smith", "", "")])
    assert len(set(links.values())) == 4
    assert admin.execute("select rule from contact_link where record_id in ('crm:C3', 'crm:C4') order by record_id").fetchall() == [("NO_IDENTIFIER",), ("NO_IDENTIFIER",)]


def test_a_mention_with_a_bad_email_and_a_bad_phone_has_no_identifier(clean, conn, admin, tmp_path):
    r, links = resolve(conn, admin, tmp_path, [person("C1", "Kate", "n/a", "tbc"), person("C2", "Kate", "n/a", "tbc")])
    assert len(set(links.values())) == 2 and admin.execute("select count(*) from contact_identifier").fetchone()[0] == 0


def test_a_record_with_nothing_to_say_about_a_contact_has_no_contact(clean, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", listing_rows=[h.listing("L1", "12 Smith St")])
    r = h.run(conn, AGENCY, folder)
    assert r.contacts.records == 0 and admin.execute("select count(*) from contact").fetchone()[0] == 0


def test_a_second_identifier_is_added_to_the_contact(clean, conn, admin, tmp_path):
    resolve(conn, admin, tmp_path, [person("C1", "A", "a@example.com", ""), person("C2", "A", "a@example.com", "0491 570 009"), person("C3", "A", "", "0491 570 009")])
    assert admin.execute("select count(distinct contact_id) from contact_link").fetchone()[0] == 1
    assert admin.execute("select kind, value from contact_identifier order by kind").fetchall() == [("email", "a@example.com"), ("phone", "+61491570009")]


def test_a_mention_that_joins_two_contacts_merges_them_and_the_older_id_survives(clean, conn, admin, tmp_path):
    rows = [person("C1", "A", "a@example.com", ""), person("C2", "A", "", "0491 570 001"), person("C3", "A", "a@example.com", "0491 570 001")]
    r, links = resolve(conn, admin, tmp_path, rows)
    survivor = contact_id_for(AGENCY, "crm:C1")
    assert set(links.values()) == {survivor} and r.contacts.merged == 1
    loser = contact_id_for(AGENCY, "crm:C2")
    assert admin.execute("select loser, survivor, evidence from contact_merge").fetchone()[:2] == (loser, survivor)
    assert admin.execute("select merged_into from contact where contact_id = %s", (loser,)).fetchone()[0] == survivor
    assert admin.execute("select merged_into from contact where contact_id = %s", (survivor,)).fetchone()[0] is None
    assert admin.execute("select distinct contact_id from contact_identifier").fetchall() == [(survivor,)]
    ev = admin.execute("select evidence from contact_merge").fetchone()[0]
    assert ev["bridged_by"] == "crm:C3" and ev["matched_on"] == ["email", "phone"]


def test_a_contact_id_is_never_reassigned_by_a_later_run(clean, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "a", crm_rows=[person("C1", "A", "a@example.com")])
    h.run(conn, AGENCY, folder)
    before = admin.execute("select record_id, contact_id, decided_at from contact_link").fetchall()
    folder2 = h.write_feeds(tmp_path / "b", crm_rows=[person("C1", "A", "a@example.com"), person("C0", "A", "a@example.com")])      # an earlier record id shows up later
    h.run(conn, AGENCY, folder2)
    assert admin.execute("select record_id, contact_id, decided_at from contact_link where record_id = 'crm:C1'").fetchall() == before
    assert admin.execute("select contact_id from contact_link where record_id = 'crm:C0'").fetchone()[0] == before[0][1]


def test_agencies_never_share_a_contact(clean, conn, admin, tmp_path):
    resolve(conn, admin, tmp_path, [person("C1", "Kate", "kate@example.com", "0491 570 001")], agency=AGENCY)
    resolve(conn, admin, tmp_path, [person("C1", "Kate", "kate@example.com", "0491 570 001")], agency=OTHER)
    got = admin.execute("select agency_id, contact_id from contact_link order by 1").fetchall()
    assert got[0][1] != got[1][1] and got[0][0] != got[1][0]
    assert admin.execute("select count(*) from contact_identifier").fetchone()[0] == 4


def test_a_crowd_on_one_number_is_not_merged(clean, conn, admin, tmp_path):
    rows = [person(f"C{i}", f"Person {i}", f"p{i}@example.com", "0491 570 100") for i in range(6)]
    r, links = resolve(conn, admin, tmp_path, rows, max_cluster=4)
    assert len(links) == 4 and r.contacts.quarantined == 2
    assert admin.execute("select record_id, reason_code, detail from contact_quarantine order by 1").fetchall() == [
        ("crm:C4", "CLUSTER_TOO_LARGE", {"contacts": [contact_id_for(AGENCY, "crm:C0")], "max_cluster": 4}), ("crm:C5", "CLUSTER_TOO_LARGE", {"contacts": [contact_id_for(AGENCY, "crm:C0")], "max_cluster": 4})]
    r2 = h.run(conn, AGENCY, None, max_cluster=4)
    assert r2.contacts.records == 0                                          # the same cap: set aside once, not decided again


def test_the_cap_counts_every_cluster_a_mention_would_join(clean, conn, admin, tmp_path):
    rows = [person("C1", "A", "a@example.com"), person("C2", "A", "a@example.com"), person("C3", "B", "", "0491 570 002"), person("C4", "B", "", "0491 570 002"),
            person("C5", "AB", "a@example.com", "0491 570 002")]
    r, links = resolve(conn, admin, tmp_path, rows, max_cluster=4)
    assert "crm:C5" not in links and r.contacts.quarantined == 1 and len(set(links.values())) == 2


def test_sales_buyers_are_resolved_with_the_same_rules(clean, conn, admin, tmp_path):
    crm_folder = h.write_feeds(tmp_path / "c", crm_rows=[person("C1", "Kate Smith", "kate@example.com")])
    sales_folder = h.write_feeds(tmp_path / "s", sales_rows=[h.sale("S1", "12 Smith St, Fakeville NSW 2000", name="Katherine Smith", email="KATE@EXAMPLE.COM")])
    h.run(conn, AGENCY, crm_folder)
    h.run(conn, AGENCY, sales_folder)
    assert admin.execute("select count(distinct contact_id) from contact_link").fetchone()[0] == 1


def test_contacts_are_linked_to_their_property_through_the_record(clean, hand, conn, admin, tmp_path):
    hand([h.addr("G12", "12")])
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com"), h.crm("C2", "Unit 5, 99 Nowhere Rd", name="Kate", email="kate@example.com")])
    h.run(conn, AGENCY, folder)
    with db.agency_tx(conn, AGENCY) as cur:
        rows = cur.execute("select contact_id, property_id, record_id, relationship from contact_property order by record_id").fetchall()
    assert len(rows) == 2 and rows[0][0] == rows[1][0] and rows[0][1] != rows[1][1] and {r[3] for r in rows} == {"crm"}


def test_a_second_run_writes_nothing(clean, conn, admin, tmp_path):
    rows = [person("C1", "A", "a@example.com", ""), person("C2", "A", "", "0491 570 001"), person("C3", "A", "a@example.com", "0491 570 001"), person("C4", "B", "", "")]
    folder = h.write_feeds(tmp_path / "f", crm_rows=rows)
    h.run(conn, AGENCY, folder)
    before = snapshot(admin)
    h.run(conn, AGENCY, folder)
    assert snapshot(admin) == before


@given(st.integers(0, 10_000))
@settings(max_examples=25)
def test_the_clusters_do_not_depend_on_the_order_of_the_rows(clean, conn, admin, tmp_path_factory, seed):
    rng = random.Random(seed)
    emails = [f"p{i}@example.com" for i in range(5)]
    phones = [f"0491 570 {i:03d}" for i in range(5)]
    rows = [person(f"C{i:02d}", "X", rng.choice(emails + [""]), rng.choice(phones + [""])) for i in range(10)]
    clusters = []
    for order in (rows, rng.sample(rows, len(rows))):
        db.wipe(clean)
        folder = h.write_feeds(tmp_path_factory.mktemp("o"), crm_rows=order)
        h.run(conn, AGENCY, folder, max_cluster=100)
        by = {}
        for rid, cid in admin.execute("select record_id, contact_id from contact_link").fetchall():
            by.setdefault(cid, set()).add(rid)
        clusters.append(sorted(sorted(v) for v in by.values()))
    assert clusters[0] == clusters[1]


# ---- cases added after the mutation check found lines no test pinned ---------------------------------------------------------------------------------

def test_the_default_cap_is_eight_records_to_a_contact(clean, conn, admin, tmp_path):
    from parcelkey.contacts import DEFAULT_MAX_CLUSTER, ContactCounts
    assert DEFAULT_MAX_CLUSTER == 8 and ContactCounts().new == 0 and ContactCounts().records == 0
    rows = [person(f"C{i}", f"P{i}", f"p{i}@example.com", "0491 570 100") for i in range(9)]
    r, links = resolve(conn, admin, tmp_path, rows)
    assert len(links) == 8 and r.contacts.quarantined == 1 and set(admin.execute("select record_id from contact_quarantine").fetchone()) == {"crm:C8"}


def test_the_counts_say_how_each_mention_was_resolved(clean, conn, admin, tmp_path):
    rows = [person("C1", "A", "a@example.com"), person("C2", "A", "A@example.com"), person("C3", "B", "", "0491 570 002"), person("C4", "B", "", "0491 570 002"), person("C5", "C", "", "")]
    r, _ = resolve(conn, admin, tmp_path, rows)
    c = r.contacts
    assert (c.records, c.new, c.merged, c.quarantined) == (5, 3, 0, 0) and c.linked == {"NEW": 2, "NO_IDENTIFIER": 1, "EMAIL": 1, "PHONE": 1}


def test_a_bridging_mention_is_ruled_a_bridge_and_a_contact_with_nothing_to_match_on_has_its_own_rule(clean, conn, admin, tmp_path):
    rows = [person("C1", "A", "a@example.com", ""), person("C2", "A", "", "0491 570 001"), person("C3", "A", "a@example.com", "0491 570 001"), person("C4", "Z", "", "")]
    r, _ = resolve(conn, admin, tmp_path, rows)
    assert admin.execute("select record_id, rule from contact_link order by 1").fetchall() == [("crm:C1", "NEW"), ("crm:C2", "NEW"), ("crm:C3", "BRIDGE"), ("crm:C4", "NO_IDENTIFIER")]
    assert r.contacts.linked == {"NEW": 2, "BRIDGE": 1, "NO_IDENTIFIER": 1} and r.contacts.new == 3 and r.contacts.merged == 1


def test_class_a_placeholder_phone_or_email_never_merges_strangers_and_a_real_one_still_does(clean, conn, admin, tmp_path):
    """CLASS-TABLE: F9 (round 5). Ten records that carry `0400 000 000` or `test@test.com` are ten contacts, none set aside as CLUSTER_TOO_LARGE and no identifier stored, while two records that share a real number
    or address are still one contact."""
    placeholders = [("0400 000 000", ""), ("0411 111 111", ""), ("", "noemail@noemail.com"), ("", "test@test.com"), ("0400 000 000", "test@test.com")]
    rows = [person(f"P{i}", f"Stranger {i}", email, phone) for i, (phone, email) in enumerate(placeholders * 2)]
    rows += [person("R1", "Kate", "kate@example.com", "0412 345 678"), person("R2", "K Smith", "kate@example.com", ""), person("R3", "Jim", "", "0412 345 678"), person("R4", "Other", "other@example.com", "0412 345 679")]
    r, links = resolve(conn, admin, tmp_path, rows)
    stranger_contacts = {links[f"crm:P{i}"] for i in range(len(placeholders) * 2)}
    assert len(stranger_contacts) == len(placeholders) * 2 and r.contacts.quarantined == 0
    assert links["crm:R1"] == links["crm:R2"] == links["crm:R3"] and links["crm:R4"] != links["crm:R1"]
    assert {v for (v,) in admin.execute("select value from contact_identifier").fetchall()} == {"kate@example.com", "+61412345678", "other@example.com", "+61412345679"}
    assert admin.execute("select count(*) from contact_quarantine").fetchone()[0] == 0


# ---- only a mobile or an email identifies a person: a landline never merges (P5-6) -----------------------------------------------------------------

IS_MOBILE = [("+61491570156", True), ("+61412345678", True), ("+61255501234", False), ("+61355501234", False), ("+61755501234", False), ("+61855501234", False), ("", False),
             ("+6149157015", False), ("+614915701567", False), ("0491570156", False), ("+61 491 570 156", False)]


@pytest.mark.parametrize("number, mobile", IS_MOBILE)
def test_is_mobile_names_an_australian_mobile_in_e164_and_nothing_else(number, mobile):
    from parcelkey.contacts import MOBILE_SQL, is_mobile
    assert is_mobile(number) is mobile


@pytest.mark.db
def test_the_sql_rule_and_the_python_rule_for_a_mobile_agree(admin):
    from parcelkey.contacts import MOBILE_SQL, is_mobile
    for number, mobile in IS_MOBILE:
        assert admin.execute("select %s ~ %s", (number, MOBILE_SQL)).fetchone()[0] is mobile is is_mobile(number), number


def test_class_one_number_shared_by_two_people_merges_them_only_when_it_is_a_mobile(clean, conn, admin, tmp_path):
    """CLASS-TABLE (P5-6): every kind of phone number two different people can give, written the ways people write it, with no email: two contacts for a landline of any state, one for a mobile."""
    table = {
        "a Sydney landline": (["02 5550 1234", "(02) 5550 1234"], False), "a Melbourne landline": (["03 5550 1234", "+61 3 5550 1234"], False), "a Brisbane landline": (["07 5550 1234", "0755501234"], False),
        "an Adelaide landline": (["08 5550 1234", "08-5550-1234"], False), "a mobile": (["0491 570 156", "+61 491 570 156"], True), "a mobile in other forms": (["0491570156", "(0491) 570 156"], True),
        "another mobile": (["0412 345 678", "0412345678"], True),
    }
    for name, (forms, merges) in table.items():
        db.wipe(db.default_admin_dsn())
        r, links = resolve(conn, admin, tmp_path, [person("C1", "Ann Office", "", forms[0]), person("C2", "Bob Other", "", forms[1])])
        assert len(set(links.values())) == (1 if merges else 2), name
        assert r.contacts.linked == ({"NEW": 1, "PHONE": 1} if merges else {"NO_IDENTIFIER": 2}), name
        assert admin.execute("select count(*) from contact_identifier").fetchone()[0] == (1 if merges else 0), name


def test_an_office_landline_given_by_eight_people_is_eight_contacts_none_set_aside_and_the_number_is_kept_as_evidence(clean, conn, admin, tmp_path):
    rows = [person(f"C{i}", f"Person {i}", "", "(02) 5550 0100") for i in range(8)]
    r, links = resolve(conn, admin, tmp_path, rows)
    assert len(set(links.values())) == 8 and r.contacts.quarantined == 0 and r.contacts.linked == {"NO_IDENTIFIER": 8}
    rule, evidence = admin.execute("select rule, evidence from contact_link where record_id = 'crm:C3'").fetchone()
    assert rule == "NO_IDENTIFIER" and evidence == {"identifiers": {}, "landline_not_used_to_merge": "+61255500100"}
    assert admin.execute("select count(*) from contact_identifier").fetchone()[0] == 0


def test_a_landline_beside_an_email_is_kept_as_evidence_and_the_email_alone_decides(clean, conn, admin, tmp_path):
    rows = [person("C1", "Kate", "kate@example.com", "02 5550 0100"), person("C2", "Kate S", "kate@example.com", "02 5550 0200"), person("C3", "Jim", "jim@example.com", "02 5550 0100")]
    r, links = resolve(conn, admin, tmp_path, rows)
    assert links["crm:C1"] == links["crm:C2"] != links["crm:C3"]
    new_ev = admin.execute("select evidence from contact_link where record_id = 'crm:C1'").fetchone()[0]
    joined = admin.execute("select rule, evidence from contact_link where record_id = 'crm:C2'").fetchone()
    assert new_ev == {"identifiers": {"email": "kate@example.com"}, "landline_not_used_to_merge": "+61255500100"}
    assert joined[0] == "EMAIL" and joined[1]["matched_on"] == ["email"] and joined[1]["landline_not_used_to_merge"] == "+61255500200" and joined[1]["identifiers"] == {"email": "kate@example.com"}
    assert admin.execute("select kind from contact_identifier order by 1").fetchall() == [("email",), ("email",)]


def test_a_mobile_beside_a_landline_merges_on_the_mobile(clean, conn, admin, tmp_path):
    rows = [person("C1", "Kate", "", "0491 570 156"), person("C2", "Kate S", "", "0491 570 156")]
    r, links = resolve(conn, admin, tmp_path, rows)
    ev = admin.execute("select evidence from contact_link where record_id = 'crm:C2'").fetchone()[0]
    assert links["crm:C1"] == links["crm:C2"] and ev["matched_on"] == ["phone"] and "landline_not_used_to_merge" not in ev


def test_a_mobile_mention_bridges_two_contacts_but_a_shared_landline_does_not(clean, conn, admin, tmp_path):
    rows = [person("C1", "A", "a@example.com", "02 5550 0001"), person("C2", "B", "b@example.com", "02 5550 0001"), person("C3", "B", "b@example.com", "0491 570 001"),
            person("C4", "A", "a@example.com", "0491 570 001")]
    r, links = resolve(conn, admin, tmp_path, rows)
    assert r.contacts.merged == 1 and links["crm:C1"] == links["crm:C2"] == links["crm:C3"] == links["crm:C4"]
    assert admin.execute("select evidence from contact_merge").fetchone()[0]["matched_on"] == ["email", "phone"]


# ---- a mention set aside by the cap is decided again when the cap is raised (P5-12) ------------------------------------------------------------------------

def one_email(n):
    return [person(f"C{i:02d}", f"Person {i}", "office@example.com") for i in range(n)]


def test_class_a_raised_cap_decides_the_set_aside_mentions_again_and_only_then(clean, conn, admin, tmp_path):
    """CLASS-TABLE (P5-12): 8 mentions of one email under a first cap, then a second run under another. Each case gives (first cap, second cap, linked after, set aside after, decided again by the second run)."""
    table = {
        "a much higher cap": (4, 100, 8, 0, 4), "a cap that lets only some in": (4, 5, 5, 3, 4), "the same cap": (4, 4, 4, 4, 0), "a lower cap": (4, 3, 4, 4, 0),
        "a cap that lets all but one in": (4, 7, 7, 1, 4), "a cap that never set any aside": (100, 200, 8, 0, 0),
    }
    folder = h.write_feeds(tmp_path / "f", crm_rows=one_email(8))
    for name, (first, second, linked, aside, retried) in table.items():
        db.wipe(db.default_admin_dsn())
        h.run(conn, AGENCY, folder, max_cluster=first)
        r = h.run(conn, AGENCY, None, max_cluster=second)
        got = (admin.execute("select count(*) from contact_link").fetchone()[0], admin.execute("select count(*) from contact_quarantine").fetchone()[0], r.contacts.retried)
        assert got == (linked, aside, retried), name
        assert r.contacts.records == retried and sum(r.contacts.linked.values()) + r.contacts.quarantined == retried, name
        again = h.run(conn, AGENCY, None, max_cluster=second)                                              # the same cap again: nothing is decided twice
        assert (again.contacts.records, again.contacts.retried) == (0, 0), name
        if aside:
            assert {d["max_cluster"] for (d,) in admin.execute("select detail from contact_quarantine").fetchall()} == {max(first, second)}, name


def test_a_mention_that_is_still_too_large_under_the_new_cap_is_set_aside_again_with_the_new_cap(clean, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=one_email(8))
    h.run(conn, AGENCY, folder, max_cluster=4)
    r = h.run(conn, AGENCY, None, max_cluster=5)
    assert (r.contacts.records, r.contacts.retried, r.contacts.quarantined, sum(r.contacts.linked.values())) == (4, 4, 3, 1)
    assert admin.execute("select record_id from contact_quarantine order by 1").fetchall() == [("crm:C05",), ("crm:C06",), ("crm:C07",)]
    assert admin.execute("select record_id, rule from contact_link where record_id = 'crm:C04'").fetchone() == ("crm:C04", "EMAIL")


def test_a_retried_mention_is_reconciled_and_leaves_no_quarantine_row_behind(clean, conn, admin, tmp_path):
    from parcelkey.reconcile import reconcile
    folder = h.write_feeds(tmp_path / "f", crm_rows=one_email(6))
    h.run(conn, AGENCY, folder, max_cluster=3)
    h.run(conn, AGENCY, None, max_cluster=50)
    assert admin.execute("select count(*) from contact_quarantine").fetchone()[0] == 0 and admin.execute("select count(distinct contact_id) from contact_link").fetchone()[0] == 1
    with db.agency_tx(conn, AGENCY) as cur:
        res = reconcile(cur, AGENCY)
    assert not [f for f in res.findings if f.check == "CONTACTS"]


def test_the_run_command_says_how_many_set_aside_mentions_it_decided_again(clean, conn, tmp_path):
    from test_quality_cli import cli
    folder = h.write_feeds(tmp_path / "f", crm_rows=one_email(5))
    first = cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency", "--max-cluster", "3", check=True).stdout
    assert "set aside 2" in first and "decided again" not in first
    second = cli("run", "--agency", AGENCY, "--feeds", str(folder), "--max-cluster", "10", check=True).stdout
    assert "set aside 0, decided again after a higher cap 2" in second
