"""Agency separation. Every claim starts with a positive control: the same query that must come back empty is first shown to return rows for its own agency."""
from __future__ import annotations

import psycopg
import pytest
from psycopg import sql

import helpers as h
from conftest import AGENCY, OTHER
from parcelkey import db
from parcelkey.db import AGENCY_TABLES, MASTER_TABLES, REFERENCE_TABLES, UnsafeRole, agency_tx

pytestmark = pytest.mark.db

REFERENCE = [h.addr("G12", "12"), h.addr("G14", "14"), h.addr("GT5", "30", unit="5")]


@pytest.fixture()
def two(hand, conn, admin, tmp_path):
    """Agency A and agency B each with links, quarantine, contacts and campaigns. They even share some record ids and some contact identifiers."""
    hand(REFERENCE)
    for agency, suffix in ((AGENCY, "a"), (OTHER, "b")):
        folder = h.write_feeds(tmp_path / suffix, crm_rows=[h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com", phone="02 5550 0001"), h.crm("C2", "Unit 9, 30 Smith St", name="Bob")],
                               listing_rows=[h.listing("L1", "14 Smith St", campaign=f"CMP-{suffix}")], sales_rows=[h.sale("S1", "12 Smith St, Fakeville NSW 2000", email="kate@example.com")])
        h.run(conn, agency, folder)
    return conn


def count(conn, agency, table):
    with agency_tx(conn, agency) as cur:
        return cur.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(table))).fetchone()[0]


def test_positive_control_an_agency_sees_its_own_rows(two):
    for table in ("source_record", "source_load", "property_link", "quarantine", "contact", "contact_identifier", "contact_link", "campaign_property", "contact_property", "agency"):
        assert count(two, AGENCY, table) > 0, table
        assert count(two, OTHER, table) > 0, table


def test_agency_b_cannot_read_agency_a_links_or_contacts(two, admin):
    with agency_tx(two, OTHER) as cur:
        cur.execute("select record_id, property_id from property_link")
        mine = cur.fetchall()
        assert {r[0] for r in mine} == {"crm:C1", "listing:L1", "sales:S1"}
    a_links = admin.execute("select count(*) from property_link where agency_id = %s", (AGENCY,)).fetchone()[0]
    assert a_links == 3
    with agency_tx(two, OTHER) as cur:
        assert cur.execute("select count(*) from property_link where agency_id = %s", (AGENCY,)).fetchone()[0] == 0
        assert cur.execute("select count(*) from contact where agency_id = %s", (AGENCY,)).fetchone()[0] == 0
        assert cur.execute("select count(*) from contact_link where agency_id = %s", (AGENCY,)).fetchone()[0] == 0
        assert cur.execute("select count(*) from contact_identifier where agency_id = %s", (AGENCY,)).fetchone()[0] == 0
        assert cur.execute("select count(*) from quarantine where agency_id = %s", (AGENCY,)).fetchone()[0] == 0
    ids_a = {r[0] for r in admin.execute("select contact_id from contact where agency_id = %s", (AGENCY,)).fetchall()}
    with agency_tx(two, OTHER) as cur:
        assert cur.execute("select count(*) from contact where contact_id = any(%s)", (sorted(ids_a),)).fetchone()[0] == 0


@pytest.mark.parametrize("table", AGENCY_TABLES)
def test_no_setting_means_no_rows_in_any_agency_table(two, table):
    assert two.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(table))).fetchone()[0] == 0
    two.rollback()


@pytest.mark.parametrize("table", AGENCY_TABLES)
def test_the_other_agency_sees_none_of_the_first_agencys_rows_in_any_table(two, admin, table):
    mine = admin.execute(sql.SQL("select count(*) from {} where agency_id = %s").format(sql.Identifier(table)), (AGENCY,)).fetchone()[0]
    assert mine > 0 or table in ("ingest_reject", "contact_merge", "contact_quarantine")          # control: the table has rows to hide
    with agency_tx(two, OTHER) as cur:
        assert cur.execute(sql.SQL("select count(*) from {} where agency_id = %s").format(sql.Identifier(table)), (AGENCY,)).fetchone()[0] == 0


def test_empty_agency_setting_means_no_rows(two):
    with two.transaction():
        two.execute("select set_config('app.agency_id', '', true)")
        assert two.execute("select count(*) from source_record").fetchone()[0] == 0


def test_agency_tx_refuses_an_empty_agency_and_a_nested_transaction(two):
    with pytest.raises(ValueError):
        with agency_tx(two, ""):
            pass
    with pytest.raises(ValueError):
        with agency_tx(two, "   "):
            pass
    with pytest.raises(RuntimeError):
        two.execute("select 1")
        with agency_tx(two, AGENCY):
            pass
    two.rollback()


def test_cannot_write_a_row_for_another_agency(two):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with agency_tx(two, AGENCY) as cur:
            cur.execute("insert into agency (agency_id) values (%s)", (OTHER + "-x",))
    with agency_tx(two, AGENCY) as cur:                                                                    # control: the same insert for the own agency works
        cur.execute("insert into agency (agency_id) values (%s) on conflict do nothing", (AGENCY,))


def test_cannot_update_or_delete_another_agencys_rows(two, admin):
    before = admin.execute("select count(*), count(distinct property_id) from property_link where agency_id = %s", (OTHER,)).fetchone()
    with agency_tx(two, AGENCY) as cur:
        cur.execute("update property_link set score = 0.5 where agency_id = %s", (OTHER,))
        assert cur.rowcount == 0
        cur.execute("delete from property_link where agency_id = %s", (OTHER,))
        assert cur.rowcount == 0
        cur.execute("update quarantine set status = 'resolved' where agency_id = %s", (OTHER,))
        assert cur.rowcount == 0
        cur.execute("update contact set display_name = 'x' where agency_id = %s", (OTHER,))
        assert cur.rowcount == 0
        cur.execute("update contact set display_name = display_name where agency_id = %s", (AGENCY,))               # control
        assert cur.rowcount > 0
    assert admin.execute("select count(*), count(distinct property_id) from property_link where agency_id = %s", (OTHER,)).fetchone() == before


def test_cannot_move_a_row_into_another_agency_by_update(two):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with agency_tx(two, AGENCY) as cur:
            cur.execute("update contact set agency_id = %s where agency_id = %s", (OTHER, AGENCY))


def test_the_same_record_ids_in_two_agencies_do_not_collide(two, admin):
    assert admin.execute("select count(*) from source_record where record_id = 'crm:C1'").fetchone()[0] == 2
    with agency_tx(two, AGENCY) as cur:
        cur.execute("select distinct agency_id from source_record")
        assert cur.fetchall() == [(AGENCY,)]


def test_views_are_scoped_to_the_caller(two):
    for view in ("campaign_property", "contact_property"):
        with agency_tx(two, AGENCY) as cur:
            assert {r[0] for r in cur.execute(sql.SQL("select agency_id from {}").format(sql.Identifier(view))).fetchall()} == {AGENCY}
    with agency_tx(two, AGENCY) as cur:
        assert {r[0] for r in cur.execute("select campaign_id from campaign_property").fetchall()} == {"CMP-a"}
    assert two.execute("select count(*) from campaign_property").fetchone()[0] == 0
    two.rollback()


def test_the_same_address_has_the_same_property_id_for_both_agencies_but_each_sees_only_its_own_links(two, admin):
    ids = {a: {r[0] for r in admin.execute("select property_id from property_link where agency_id = %s", (a,)).fetchall()} for a in (AGENCY, OTHER)}
    assert ids[AGENCY] == ids[OTHER] and len(ids[AGENCY]) == 2
    with agency_tx(two, AGENCY) as cur:
        assert cur.execute("select count(*) from property_link").fetchone()[0] == 3


# ---- the database itself ------------------------------------------------------------------------------------------------------------------

def test_every_table_is_classified_and_every_agency_table_has_forced_row_security(admin):
    rows = admin.execute("""select c.relname, c.relrowsecurity, c.relforcerowsecurity,
                                   exists (select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'agency_id' and not a.attisdropped)
                            from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'parcelkey' and c.relkind = 'r' and c.relname <> 'schema_migration'""").fetchall()
    declared = set(AGENCY_TABLES) | set(REFERENCE_TABLES) | set(MASTER_TABLES)
    assert {r[0] for r in rows} == declared, "a table exists that is not classified as agency, reference or master data"
    for name, rls, forced, has_agency in rows:
        if name in AGENCY_TABLES:
            assert has_agency and rls and forced, name
        else:
            assert not has_agency and not rls, name


def test_every_agency_table_has_a_policy_that_checks_reads_and_writes(admin):
    rows = admin.execute("select tablename, qual, with_check from pg_policies where schemaname = 'parcelkey'").fetchall()
    assert {r[0] for r in rows} == set(AGENCY_TABLES)
    assert all("app.agency_id" in r[1] and "app.agency_id" in r[2] for r in rows)


def test_the_table_owner_is_held_to_the_policy_too(two, admin):
    """FORCE matters for the owner role: it is not a superuser, so without FORCE it would see everything."""
    with psycopg.connect(db.default_owner_dsn()) as owner:
        assert owner.execute("select rolsuper or rolbypassrls from pg_roles where rolname = current_user").fetchone()[0] is False
        assert owner.execute("select count(*) from property_link").fetchone()[0] == 0
        with owner.transaction():
            owner.execute("select set_config('app.agency_id', %s, true)", (AGENCY,))
            assert owner.execute("select count(*) from property_link").fetchone()[0] == 3
    admin.execute("alter table property_link no force row level security")                         # control: without FORCE the owner would see all six rows
    try:
        with psycopg.connect(db.default_owner_dsn()) as owner:
            assert owner.execute("select count(*) from property_link").fetchone()[0] == 6
    finally:
        admin.execute("alter table property_link force row level security")


def test_the_app_role_is_not_a_superuser_and_does_not_bypass_row_security(conn):
    assert conn.execute("select rolsuper, rolbypassrls from pg_roles where rolname = current_user").fetchone() == (False, False)
    conn.rollback()


def test_connecting_as_a_superuser_is_refused(admin_dsn):
    with pytest.raises(UnsafeRole):
        db.connect(admin_dsn)


def test_the_app_role_can_read_the_reference_but_not_change_it(two, admin):
    assert two.execute("select count(*) from gnaf_address").fetchone()[0] == 3
    for stmt in ("insert into address_detail (address_detail_pid, date_created, locality_pid, address_site_pid, level_geocoded_code) values ('X', now(), 'L', 'S', 7)",
                 "update gnaf_address set postcode = '9999'", "delete from gnaf_address", "truncate gnaf_address", "update state set state_name = 'x'"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            two.execute(stmt)
        two.rollback()


def test_the_app_role_can_add_a_property_but_not_change_or_remove_one(two, admin):
    assert two.execute("select count(*) from property").fetchone()[0] == 2
    for stmt in ("update property set display_address = 'x'", "delete from property", "truncate property"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            two.execute(stmt)
        two.rollback()
    two.execute("insert into property (property_id, origin, canon_key, display_address) values ('pk_d_test', 'derived', 'k', 'x')")
    two.rollback()
    assert admin.execute("select count(*) from property where property_id = 'pk_d_test'").fetchone()[0] == 0


def test_the_app_role_cannot_rewrite_history(two):
    for stmt in ("update schema_migration set sha256 = 'x'", "drop table property_link", "alter table property_link disable row level security",
                 "create policy extra on property_link using (true)"):
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege)):
            two.execute(stmt)
        two.rollback()


def test_a_migration_changed_after_it_was_applied_is_refused(admin_dsn, monkeypatch):
    from parcelkey import db as dbmod
    files = dbmod.migration_files()
    monkeypatch.setattr(dbmod, "migration_files", lambda: [(files[0][0], files[0][1] + "\n-- changed\n"), *files[1:]])
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        dbmod.init_db(admin_dsn)
