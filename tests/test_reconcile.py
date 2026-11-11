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


PLANTED_IDS = ("pk_g_0000000000000000", "pk_d_0000000000000000", "pk_g_deadbeefdeadbeef")


def restore_damage(admin):
    """Put back what a test may have dropped or planted, so a test that fails before its own restore cannot break the next one."""
    admin.execute("set session_replication_role = replica")
    admin.execute("delete from property_link where property_id = any(%s)", (list(PLANTED_IDS),))
    admin.execute("delete from property where property_id = any(%s)", (list(PLANTED_IDS),))
    admin.execute("set session_replication_role = origin")
    admin.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'property_gnaf_pid_key') THEN
            ALTER TABLE property ADD CONSTRAINT property_gnaf_pid_key UNIQUE (gnaf_pid); END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'property_link_score_check') THEN
            ALTER TABLE property_link ADD CONSTRAINT property_link_score_check CHECK (score >= 0 AND score <= 1); END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'property_derived_key_ux') THEN
            CREATE UNIQUE INDEX property_derived_key_ux ON property (canon_key) WHERE origin = 'derived'; END IF;
        END $$""")


@pytest.fixture()
def done(hand, conn, admin, tmp_path):
    """A finished run: links, a derived property, quarantined records, contacts. Teardown restores any schema a test broke."""
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[
        h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com"), h.crm("C2", "14 Smith St", name="Kate", email="kate@example.com", phone="02 5550 0001"),
        h.crm("C3", "Unit 9, 30 Smith St", name="Bob"), h.crm("C4", "18 Smith St", name="Bob", phone="(02) 5550 0001"), h.crm("C5", "Flat 5")],
        listing_rows=[h.listing("L1", "Unit 5, 30 Smith St")], sales_rows=[h.sale("S1", "12 Smith St, Fakeville NSW 2000", name="K", email="KATE@example.com")])
    h.run(conn, AGENCY, folder)
    yield conn, admin, folder
    restore_damage(admin)


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
    assert "ids all match their derivation" in text and "mentions all linked or set aside" in text and "superseded 0 derived properties" in text      # every check says what it counted, so a dropped line is found


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
    original = admin.execute("select property_id from property where origin = 'derived' and canon_key = %s and property_id <> 'pk_d_0000000000000000'", (key,)).fetchone()[0]
    admin.execute("set session_replication_role = replica")
    admin.execute("insert into property_link (agency_id, record_id, property_id, rule, score) select agency_id, 'crm:EXTRA', %s, rule, score from property_link where record_id = 'crm:C4'", (original,))
    admin.execute("set session_replication_role = origin")
    res = check(conn, folder)
    assert "ONE_ID" in names(res)
    assert any(f.check == "ONE_ID" and "derived key" in str(f) and "has two ids" in str(f) for f in res.findings), [str(f) for f in res.findings]       # the per-row check names the key, not only the later count
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


# ---- F12: a derived property that a later release now covers is reported and never rewritten --------------------------------------------------------

def test_class_a_derived_property_a_release_now_covers_is_reported_and_never_rewritten(hand, conn, admin, tmp_path):
    """CLASS-TABLE: F12. After a release adds an address, the derived property for it is listed as SUPERSEDED (also when the release adds it as an alias spelling) and nothing is rewritten;
    a G-NAF property, a derived one the release does not cover, and another agency's reconcile are not reported."""
    hand(REFERENCE)
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "18 Smith St"), h.crm("C3", "20 Smith St"), h.crm("C4", "22 Smith St")])
    h.run(conn, AGENCY, folder)
    before = check(conn, folder)
    assert before.ok and "superseded 0 derived properties now equal a G-NAF address" in before.lines
    ids = {r[0]: r[1] for r in admin.execute("select l.record_id, l.property_id from property_link l").fetchall()}
    snapshot_before = (admin.execute("select * from property order by property_id").fetchall(), admin.execute("select * from property_link order by record_id").fetchall())
    hand(REFERENCE + [h.addr("G20", "20"), h.addr("A22", "22", alias_of="G14")])                                    # the "release": C3's address is a new principal, C4's an alias spelling
    after = check(conn, folder)
    assert names(after) == ["SUPERSEDED"] and len(after.findings) == 1
    detail = after.findings[0].detail
    assert detail.startswith("2 derived properties now equal a G-NAF address (")
    assert f"{ids['crm:C3']} is now G-NAF G20" in detail and f"{ids['crm:C4']} is now G-NAF G14" in detail
    assert ids["crm:C1"] not in detail and ids["crm:C2"] not in detail and "nothing was rewritten" in detail
    assert "superseded 2 derived properties now equal a G-NAF address" in after.lines
    assert (admin.execute("select * from property order by property_id").fetchall(), admin.execute("select * from property_link order by record_id").fetchall()) == snapshot_before
    h.run(conn, AGENCY, folder)                                                                                      # a rerun decides nothing again and writes nothing
    assert {r[0]: r[1] for r in admin.execute("select record_id, property_id from property_link").fetchall()} == ids
    with db.agency_tx(conn, "agency-b") as cur:
        assert reconcile(cur, "agency-b").ok


def test_class_every_release_kind_against_every_agency_is_reported_only_to_the_agency_that_uses_it(hand, conn, admin, tmp_path):
    """CLASS-TABLE: F12. The table is built by code: every release kind (the release adds the address as a principal, as an alias spelling, or not at all) against every agency that
    ingested it. SUPERSEDED reaches an agency exactly when it uses a derived property the release now covers, and nothing is rewritten for any of them."""
    import itertools
    hand(REFERENCE)
    table = [(kind, agency, 40 + 2 * i) for i, (kind, agency) in enumerate(itertools.product(("principal", "alias", "none"), (AGENCY, "agency-b")))]
    feeds = {}
    for agency in (AGENCY, "agency-b"):
        rows = [h.crm(f"C{number}", f"{number} Smith St") for kind, a, number in table if a == agency]
        feeds[agency] = h.write_feeds(tmp_path / agency, crm_rows=rows)
        h.run(conn, agency, feeds[agency])
    snapshot = (admin.execute("select * from property order by property_id").fetchall(), admin.execute("select * from property_link order by agency_id, record_id").fetchall())
    release = list(REFERENCE)
    for kind, agency, number in table:
        if kind == "principal":
            release.append(h.addr(f"G{number}", str(number)))
        elif kind == "alias":
            release.append(h.addr(f"A{number}", str(number), alias_of="G14"))
    hand(release)
    for agency in (AGENCY, "agency-b"):
        expected = sorted(number for kind, a, number in table if a == agency and kind != "none")
        with db.agency_tx(conn, agency) as cur:
            res = reconcile(cur, agency, feeds[agency])
        assert (names(res) == ["SUPERSEDED"]) == bool(expected), (agency, expected, res.findings)
        assert f"superseded {len(expected)} derived properties now equal a G-NAF address" in res.lines, (agency, expected, res.lines)
    assert (admin.execute("select * from property order by property_id").fetchall(), admin.execute("select * from property_link order by agency_id, record_id").fetchall()) == snapshot


def test_a_derived_property_only_another_agency_uses_is_not_reported_to_this_one(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    other = h.write_feeds(tmp_path / "other", crm_rows=[h.crm("C1", "20 Smith St")])
    h.run(conn, "agency-b", other)
    mine = h.write_feeds(tmp_path / "mine", crm_rows=[h.crm("C1", "12 Smith St")])
    h.run(conn, AGENCY, mine)
    hand(REFERENCE + [h.addr("G20", "20")])
    assert check(conn, mine).ok
    with db.agency_tx(conn, "agency-b") as cur:
        assert [f.check for f in reconcile(cur, "agency-b", other).findings] == ["SUPERSEDED"]


def test_five_superseded_properties_are_listed_by_name_and_the_rest_by_count(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    numbers = [str(n) for n in range(50, 58)]
    folder = h.write_feeds(tmp_path / "feeds", crm_rows=[h.crm(f"C{n}", f"{n} Smith St") for n in numbers])
    h.run(conn, AGENCY, folder)
    hand(REFERENCE + [h.addr(f"G{n}", n) for n in numbers])
    detail = check(conn, folder).findings[0].detail
    assert detail.startswith("8 derived properties now equal a G-NAF address (") and detail.count(" is now G-NAF ") == 5


# ---- reconcile reads a feed file the way the loader does --------------------------------------------------------------------------------------------

def test_class_reconcile_accepts_every_shape_of_feed_file_the_loader_settled(hand, conn, admin, tmp_path):
    """CLASS-TABLE (P5-11): reconcile --feeds once said NOT RECONCILED for a blank line, a repeated reference and a short row, because it read the file with a reader of its own. One reader now, so every shape the
    loader handled reconciles, and an extra cell row (rejected, kept in the reject table) does too."""
    hand(REFERENCE)
    ok1, ok2 = h.crm_line("C1", "12 Smith St"), h.crm_line("C2", "14 Smith St")
    shapes = {
        "plain": h.crm_text(ok1, ok2),
        "blank line between rows": h.crm_text(ok1, "", ok2),
        "blank lines at the end": h.crm_text(ok1, ok2) + "\n\n",
        "a repeated reference": h.crm_text(ok1, h.crm_line("C1", "99 Other St"), ok2),
        "a short row": h.crm_text(ok1, "C2,Bob,,,14 Smith St"),
        "extra cells": h.crm_text(ok1, ok2 + ",STRAY"),
        "a missing reference": h.crm_text(ok1, h.crm_line("", "14 Smith St")),
        "a blank address": h.crm_text(ok1, h.crm_line("C2", "   ")),
        "windows line ends": h.crm_text(ok1, ok2, eol="\r\n"),
        "a byte order mark": "﻿" + h.crm_text(ok1, ok2),
        "a quoted cell spanning lines": h.crm_text(ok1, h.crm_line("C2", '"14 Smith\nSt"')),
    }
    for name, text in shapes.items():
        db.wipe(db.default_admin_dsn())
        folder = h.write_crm_text(tmp_path / name.replace(" ", "_"), text)
        h.run(conn, AGENCY, folder)
        res = check(conn, folder)
        assert res.ok, (name, [str(f) for f in res.findings])
        assert any(line.startswith("file       crm_export.csv: ") for line in res.lines), name


def test_reconcile_does_not_keep_a_reader_of_its_own():
    import parcelkey.ingest as ingest
    import parcelkey.reconcile as reconcile_module
    assert reconcile_module.read_feed is ingest.read_feed and not hasattr(reconcile_module, "csv") and not hasattr(reconcile_module, "_read_file_rows")


@pytest.mark.parametrize("name, content, why", [
    ("not utf8", "contact_ref,address\nC1,Caf\xe9 St\n".encode("latin-1"), "not UTF-8"),
    ("a missing column", b"contact_ref,address\nC1,12 Smith St\n", "needs column"),
    ("an empty file", b"", "needs column"),
])
def test_a_feed_file_that_cannot_be_read_is_a_finding_and_not_a_crash(done, tmp_path, name, content, why):
    conn, admin, folder = done
    other = tmp_path / "other"
    shutil.copytree(folder, other)
    (other / "crm_export.csv").write_bytes(content)
    res = check(conn, other)
    assert names(res) == ["FILE_ROWS"] and "cannot be read as the crm feed" in str(res.findings[0]) and why in str(res.findings[0])
