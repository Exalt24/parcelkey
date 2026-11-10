"""Reconcile. A check that says "match" proves nothing until it has been seen to say "differ": every check is first required to pass, then the damage it should catch is planted."""
from __future__ import annotations

import shutil

import pytest

import helpers as h
from conftest import AGENCY
from parcelkey import db
from parcelkey.master import property_id_for_derived
from parcelkey.reconcile import reconcile

pytestmark = pytest.mark.db

REFERENCE = [h.addr("G12", "12"), h.addr("G14", "14"), h.addr("GT5", "30", unit="5"), h.addr("GT6", "30", unit="6")]


@pytest.fixture()
def done(hand, conn, admin, tmp_path):
    """A finished run: links, a derived property, quarantined records, contacts."""
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[
        h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com"), h.crm("C2", "14 Smith St", name="Kate", email="kate@example.com", phone="02 5550 0001"),
        h.crm("C3", "Unit 9, 30 Smith St", name="Bob"), h.crm("C4", "18 Smith St", name="Bob", phone="(02) 5550 0001"), h.crm("C5", "Flat 5")],
        listing_rows=[h.listing("L1", "Unit 5, 30 Smith St")], sales_rows=[h.sale("S1", "12 Smith St, Fakeville NSW 2000", name="K", email="KATE@example.com")])
    h.run(conn, AGENCY, folder)
    return conn, admin, folder


def check(conn, folder=None):
    with db.agency_tx(conn, AGENCY) as cur:
        return reconcile(cur, AGENCY, folder)


def names(res):
    return sorted({f.check for f in res.findings})


def test_a_finished_run_reconciles_and_says_what_it_counted(done):
    conn, admin, folder = done
    res = check(conn, folder)
    assert res.ok, [str(f) for f in res.findings]
    text = "\n".join(res.lines)
    assert "records    7 = linked 5 + quarantined 2" in text and "orphans    0" in text and "crm_export.csv: 5 rows = stored 5" in text


def test_a_record_with_no_link_and_no_quarantine_is_found(done):
    conn, admin, folder = done
    admin.execute("delete from property_link where record_id = 'crm:C1'")
    assert names(check(conn, folder)) == ["ACCOUNTED"]


def test_a_record_that_is_linked_and_quarantined_at_once_is_found(done):
    conn, admin, folder = done
    admin.execute("insert into quarantine (agency_id, record_id, reason_code) values (%s, 'crm:C1', 'AMBIGUOUS_TIE')", (AGENCY,))
    res = check(conn, folder)
    assert names(res) == ["ACCOUNTED"] and "linked and in open quarantine at once" in str(res.findings[0])


def test_a_record_that_was_never_normalised_is_found(done):
    conn, admin, folder = done
    admin.execute("update source_record set norm_status = 'pending' where record_id = 'crm:C1'")
    assert "ACCOUNTED" in names(check(conn, folder))


def test_a_resolved_quarantine_row_does_not_count_as_open(done):
    conn, admin, folder = done
    admin.execute("update quarantine set status = 'resolved' where record_id = 'crm:C3'")
    assert names(check(conn, folder)) == ["ACCOUNTED"]                      # C3 is now neither linked nor open


def test_a_link_to_a_missing_property_is_an_orphan(done):
    conn, admin, folder = done
    admin.execute("set session_replication_role = replica")                    # lets the test break the foreign key on purpose
    pid = admin.execute("select property_id from property_link where record_id = 'crm:C1'").fetchone()[0]
    admin.execute("delete from property where property_id = %s", (pid,))
    admin.execute("set session_replication_role = origin")
    res = check(conn, folder)
    assert "NO_ORPHAN" in names(res) and "2 links without a property" in str([str(f) for f in res.findings])


def test_a_link_to_a_missing_record_is_an_orphan(done):
    conn, admin, folder = done
    admin.execute("set session_replication_role = replica")
    admin.execute("delete from source_record where record_id = 'crm:C1'")
    admin.execute("set session_replication_role = origin")
    assert "NO_ORPHAN" in names(check(conn, folder))


def test_a_quarantine_row_with_no_record_is_an_orphan(done):
    conn, admin, folder = done
    admin.execute("set session_replication_role = replica")
    admin.execute("insert into quarantine (agency_id, record_id, reason_code) values (%s, 'crm:GONE', 'AMBIGUOUS_TIE')", (AGENCY,))
    admin.execute("set session_replication_role = origin")
    assert "NO_ORPHAN" in names(check(conn, folder))


def test_a_property_with_an_id_that_is_not_its_derivation_is_found(done):
    conn, admin, folder = done
    admin.execute("set session_replication_role = replica")
    old = admin.execute("select property_id from property where gnaf_pid = 'G12'").fetchone()[0]
    admin.execute("update property_link set property_id = 'pk_g_deadbeefdeadbeef' where property_id = %s", (old,))
    admin.execute("update property set property_id = 'pk_g_deadbeefdeadbeef' where property_id = %s", (old,))
    admin.execute("set session_replication_role = origin")
    res = check(conn, folder)
    assert "ONE_ID" in names(res) and "not the one its derivation gives" in " ".join(str(f) for f in res.findings)


def test_one_g_naf_address_with_two_property_ids_is_found(done):
    conn, admin, folder = done
    admin.execute("alter table property drop constraint property_gnaf_pid_key")
    admin.execute("""insert into property (property_id, origin, gnaf_pid, canon_key, display_address) select 'pk_g_0000000000000000', origin, gnaf_pid, canon_key, display_address
                     from property where gnaf_pid = 'G12'""")
    admin.execute("update property_link set property_id = 'pk_g_0000000000000000' where record_id = 'crm:C1'")
    res = check(conn, folder)
    assert "ONE_ID" in names(res) and any("has two ids" in str(f) for f in res.findings)
    admin.execute("delete from property_link where property_id = 'pk_g_0000000000000000'")
    admin.execute("delete from property where property_id = 'pk_g_0000000000000000'")
    admin.execute("alter table property add constraint property_gnaf_pid_key unique (gnaf_pid)")


def test_one_derived_key_with_two_property_ids_is_found(done):
    conn, admin, folder = done
    admin.execute("drop index property_derived_key_ux")
    key = admin.execute("select canon_key from property where origin = 'derived'").fetchone()[0]
    admin.execute("insert into property (property_id, origin, canon_key, display_address) values ('pk_d_0000000000000000', 'derived', %s, 'x')", (key,))
    admin.execute("update property_link set property_id = 'pk_d_0000000000000000' where record_id = 'crm:C4'")
    res = check(conn, folder)
    assert "ONE_ID" in names(res)
    assert property_id_for_derived(key) != "pk_d_0000000000000000"
    admin.execute("delete from property_link where property_id = 'pk_d_0000000000000000'")
    admin.execute("delete from property where property_id = 'pk_d_0000000000000000'")
    admin.execute("create unique index property_derived_key_ux on property (canon_key) where origin = 'derived'")


def test_a_link_without_a_rule_or_with_a_score_out_of_range_is_found(done):
    conn, admin, folder = done
    admin.execute("alter table property_link drop constraint property_link_score_check")
    admin.execute("update property_link set score = 1.5 where record_id = 'crm:C1'")
    assert names(check(conn, folder)) == ["LINK_FIELDS"]
    admin.execute("update property_link set score = 1 where record_id = 'crm:C1'")
    admin.execute("update property_link set rule = '' where record_id = 'crm:C1'")
    assert names(check(conn, folder)) == ["LINK_FIELDS"]
    admin.execute("update property_link set rule = 'EXACT_KEY'")
    assert check(conn, folder).ok
    admin.execute("alter table property_link add constraint property_link_score_check check (score >= 0 and score <= 1)")


def test_a_quarantine_row_with_an_unknown_reason_is_found(done):
    conn, admin, folder = done
    admin.execute("update quarantine set reason_code = 'WHATEVER' where record_id = 'crm:C3'")
    assert names(check(conn, folder)) == ["LINK_FIELDS"]


def test_a_contact_mention_with_no_contact_is_found(done):
    conn, admin, folder = done
    admin.execute("delete from contact_link where record_id = 'crm:C1'")
    res = check(conn, folder)
    assert names(res) == ["CONTACTS"] and "1 contact mentions unresolved" in str(res.findings[0])


def test_a_link_or_identifier_pointing_at_a_merged_contact_is_found(done):
    conn, admin, folder = done
    cid = admin.execute("select contact_id from contact_link where record_id = 'crm:C1'").fetchone()[0]
    admin.execute("update contact set merged_into = 'c_other' where contact_id = %s", (cid,))
    res = check(conn, folder)
    assert names(res) == ["CONTACTS"] and "links and" in str(res.findings[0])


def test_a_stored_row_that_differs_from_the_file_is_found(done):
    conn, admin, folder = done
    admin.execute("""update source_record set raw = jsonb_set(raw, '{address}', '"99 Changed Rd"') where record_id = 'crm:C1'""")
    res = check(conn, folder)
    assert names(res) == ["RAW_STORED"] and "1 stored rows differ" in str(res.findings[0])


def test_a_stored_row_missing_from_the_file_is_found(done, tmp_path):
    conn, admin, folder = done
    other = tmp_path / "other"
    shutil.copytree(folder, other)
    text = (other / "crm_export.csv").read_text(encoding="utf-8").replace("C1,", "C9,", 1)
    (other / "crm_export.csv").write_text(text, encoding="utf-8")
    res = check(conn, other)
    assert "RAW_STORED" in names(res)


def test_the_file_row_count_is_checked_against_the_load(done, tmp_path):
    conn, admin, folder = done
    other = tmp_path / "other"
    shutil.copytree(folder, other)
    with (other / "crm_export.csv").open("a", encoding="utf-8") as f:
        f.write("C6,,,,1 Extra St,Fakeville,NSW,2000,2026-01-01\n")
    res = check(conn, other)
    assert "FILE_ROWS" in names(res) and "6 rows" in str(res.findings[0])


def test_a_load_that_lies_about_what_it_stored_is_found(done):
    conn, admin, folder = done
    admin.execute("update source_load set stored = stored + 1, rejected = rejected - 1 where source = 'crm'")
    res = check(conn, folder)
    assert "FILE_ROWS" in names(res)


def test_a_feed_file_with_no_recorded_load_is_found(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    res = check(conn, folder)
    assert names(res) == ["FILE_ROWS"] and "no load recorded" in str(res.findings[0])


def test_without_a_feed_folder_only_the_database_side_is_checked(done):
    conn, admin, folder = done
    admin.execute("""update source_record set raw = jsonb_set(raw, '{address}', '"99 Changed Rd"') where record_id = 'crm:C1'""")
    assert check(conn).ok


def test_each_agency_is_reconciled_on_its_own(done):
    conn, admin, folder = done
    with db.agency_tx(conn, "agency-b") as cur:
        res = reconcile(cur, "agency-b")
    assert res.ok and "records    0 = linked 0 + quarantined 0" in "\n".join(res.lines)


# ---- cases added after the mutation check found lines no test pinned -------------------------------------------------------------------------------

def test_every_reason_the_matcher_can_give_is_a_known_reason_and_the_reverse():
    import re
    from pathlib import Path
    from parcelkey import match
    from parcelkey.reconcile import QUARANTINE_REASONS
    emitted = set(re.findall(r'_set_aside\("([A-Z_]+)"', Path(match.__file__).read_text(encoding="utf-8")))
    assert emitted == set(QUARANTINE_REASONS)


@pytest.mark.parametrize("reason", sorted(__import__("parcelkey.reconcile", fromlist=["x"]).QUARANTINE_REASONS))
def test_a_known_quarantine_reason_is_never_reported(done, reason):
    conn, admin, folder = done
    admin.execute("update quarantine set reason_code = %s where record_id = 'crm:C3'", (reason,))
    assert check(conn, folder).ok


def test_the_duplicate_findings_name_the_pid_and_the_key(done):
    conn, admin, folder = done
    admin.execute("alter table property drop constraint property_gnaf_pid_key")
    admin.execute("""insert into property (property_id, origin, gnaf_pid, canon_key, display_address) select 'pk_g_0000000000000000', origin, gnaf_pid, canon_key, display_address
                     from property where gnaf_pid = 'G12'""")
    admin.execute("update property_link set property_id = 'pk_g_0000000000000000' where record_id = 'crm:C1'")
    res = check(conn, folder)
    assert any(f.check == "ONE_ID" and "G-NAF pid G12 has two ids" in f.detail for f in res.findings)
    assert any(f.check == "ONE_ID" and "1 G-NAF pids and 0 derived keys appear on more than one property" in f.detail for f in res.findings)
    admin.execute("delete from property_link where property_id = 'pk_g_0000000000000000'")
    admin.execute("delete from property where property_id = 'pk_g_0000000000000000'")
    admin.execute("alter table property add constraint property_gnaf_pid_key unique (gnaf_pid)")


def test_a_duplicate_among_properties_this_agency_does_not_use_is_still_found(done):
    conn, admin, folder = done
    admin.execute("drop index property_derived_key_ux")
    key = admin.execute("select canon_key from property where origin = 'derived'").fetchone()[0]
    admin.execute("insert into property (property_id, origin, canon_key, display_address) values ('pk_d_0000000000000000', 'derived', %s, 'x')", (key,))
    res = check(conn, folder)
    assert [f.detail for f in res.findings if f.check == "ONE_ID"] == ["0 G-NAF pids and 1 derived keys appear on more than one property"]
    admin.execute("delete from property where property_id = 'pk_d_0000000000000000'")
    admin.execute("create unique index property_derived_key_ux on property (canon_key) where origin = 'derived'")


def test_a_derived_key_with_two_ids_is_named(done):
    conn, admin, folder = done
    admin.execute("drop index property_derived_key_ux")
    key = admin.execute("select canon_key from property where origin = 'derived'").fetchone()[0]
    admin.execute("insert into property (property_id, origin, canon_key, display_address) values ('pk_d_0000000000000000', 'derived', %s, 'x')", (key,))
    admin.execute("update property_link set property_id = 'pk_d_0000000000000000' where record_id = 'crm:C4'")
    res = check(conn, folder)
    assert any("derived key" in f.detail and "has two ids" in f.detail for f in res.findings) or any("not the one its derivation gives" in f.detail for f in res.findings)
    admin.execute("delete from property_link where property_id = 'pk_d_0000000000000000'")
    admin.execute("delete from property where property_id = 'pk_d_0000000000000000'")
    admin.execute("create unique index property_derived_key_ux on property (canon_key) where origin = 'derived'")


def test_one_wrong_id_is_counted_as_one(done):
    conn, admin, folder = done
    old = admin.execute("select property_id from property where gnaf_pid = 'G14'").fetchone()[0]
    admin.execute("set session_replication_role = replica")
    admin.execute("update property_link set property_id = 'pk_g_deadbeefdeadbeef' where property_id = %s", (old,))
    admin.execute("update property set property_id = 'pk_g_deadbeefdeadbeef' where property_id = %s", (old,))
    admin.execute("set session_replication_role = origin")
    res = check(conn, folder)
    assert [f.detail for f in res.findings if "derivation" in f.detail] == ["1 properties carry an id that is not the one its derivation gives"]


def test_a_load_whose_counts_do_not_add_up_is_found_whichever_count_is_off(done):
    conn, admin, folder = done
    admin.execute("update source_load set unchanged = unchanged + 1 where source = 'crm'")
    res = check(conn, folder)
    assert "FILE_ROWS" in names(res) and "stored 5 + rejected 0 + unchanged 1" in str(res.findings[0])
    admin.execute("update source_load set unchanged = 0 where source = 'crm'")
    admin.execute("update source_load set stored = stored - 1, unchanged = 1 where source = 'crm'")                    # the sum still equals the file, the stored count is wrong
    res = check(conn, folder)
    assert names(res) == ["FILE_ROWS"] and "the tables hold 5 and 0" in str(res.findings[0])
    admin.execute("update source_load set stored = 5, unchanged = -1, rejected = 1 where source = 'crm'")              # the sum still equals the file, the rejected count is wrong
    res = check(conn, folder)
    assert names(res) == ["FILE_ROWS"] and "the tables hold 5 and 0" in str(res.findings[0])


def test_the_accounting_messages_say_which_count_is_wrong(done):
    conn, admin, folder = done
    admin.execute("update source_record set norm_status = 'pending' where record_id = 'crm:C1'")
    admin.execute("update source_record set norm_status = 'ok' where record_id = 'crm:C1'")
    admin.execute("delete from property_link where record_id = 'crm:C1'")
    res = check(conn, folder)
    assert [f.detail for f in res.findings] == ["1 records are neither linked nor in open quarantine"]
