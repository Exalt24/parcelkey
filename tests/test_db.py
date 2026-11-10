"""The database set up: a fresh database, roles and schema made from nothing, what each role may do, the connection settings, and the wipe helpers."""
from __future__ import annotations

import secrets

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

import helpers as h
from conftest import AGENCY
from parcelkey import db

pytestmark = pytest.mark.db


@pytest.fixture()
def scratch(admin_dsn):
    """An empty database with its own schema and role names, so init_db runs its whole first-time path without touching the real one."""
    tag = "pk_t" + secrets.token_hex(3)
    owner, app = f"{tag}_owner", f"{tag}_app"
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{tag}"')
    dsn = db.retarget(admin_dsn, dbname=tag)
    yield tag, dsn, owner, app
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{tag}" WITH (FORCE)')
        c.execute(f'DROP ROLE IF EXISTS "{owner}"')
        c.execute(f'DROP ROLE IF EXISTS "{app}"')


def test_init_db_builds_everything_from_an_empty_database(scratch):
    tag, dsn, owner, app = scratch
    db.init_db(dsn, "app-secret", "owner-secret", schema=tag, app_role=app, owner_role=owner)
    with psycopg.connect(dsn) as c:
        assert {r[0] for r in c.execute("select extname from pg_extension").fetchall()} >= {"postgis", "pg_trgm"}
        assert c.execute("select rolsuper, rolbypassrls, rolcanlogin from pg_roles where rolname = %s", (app,)).fetchone() == (False, False, True)
        assert c.execute("select rolsuper, rolbypassrls, rolcanlogin from pg_roles where rolname = %s", (owner,)).fetchone() == (False, False, True)
        for role in (app, owner):
            assert c.execute("select rolconfig from pg_roles where rolname = %s", (role,)).fetchone()[0] == [f"search_path={tag}, public"]
        assert c.execute("select pg_get_userbyid(nspowner) from pg_namespace where nspname = %s", (tag,)).fetchone()[0] == owner
        tables = c.execute("select tablename, tableowner from pg_tables where schemaname = %s", (tag,)).fetchall()
        assert {t for t, _ in tables} >= set(db.AGENCY_TABLES) | set(db.REFERENCE_TABLES) | set(db.MASTER_TABLES) | {"schema_migration"} and {o for _, o in tables} == {owner}
        applied = c.execute(f'select name, sha256 from "{tag}".schema_migration order by name').fetchall()
        assert [n for n, _ in applied] == [n for n, _ in db.migration_files()] and len(applied) == 4
        assert c.execute("select count(*) from pg_policies where schemaname = %s", (tag,)).fetchone()[0] == len(db.AGENCY_TABLES)
        assert c.execute("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = %s and c.relforcerowsecurity", (tag,)).fetchone()[0] == len(db.AGENCY_TABLES)


def test_the_app_role_gets_exactly_the_privileges_it_needs(scratch):
    tag, dsn, owner, app = scratch
    db.init_db(dsn, "app-secret", "owner-secret", schema=tag, app_role=app, owner_role=owner)
    with psycopg.connect(dsn) as c:
        def can(obj: str, priv: str) -> bool:
            return c.execute("select has_table_privilege(%s, %s, %s)", (app, f'"{tag}".{obj}', priv)).fetchone()[0]
        for t in db.AGENCY_TABLES:
            wanted = ("SELECT", "INSERT", "DELETE") if t == "source_record" else ("SELECT", "INSERT", "UPDATE", "DELETE")          # source_record is updatable by column only
            assert all(can(t, p) for p in wanted), t
            assert not can(t, "TRUNCATE"), t
        assert not can("source_record", "UPDATE")
        for t in db.REFERENCE_TABLES:
            assert can(t, "SELECT") and not any(can(t, p) for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")), t
        assert can("property", "SELECT") and can("property", "INSERT") and not any(can("property", p) for p in ("UPDATE", "DELETE", "TRUNCATE"))
        for view in ("campaign_property", "contact_property"):
            assert can(view, "SELECT") and not can(view, "INSERT")
        assert not can("schema_migration", "SELECT")
        col = lambda name, priv="UPDATE": c.execute("select has_column_privilege(%s, %s, %s, %s)", (app, f'"{tag}".source_record', name, priv)).fetchone()[0]
        assert not any(col(n) for n in ("raw", "raw_sha256", "source_ref", "record_id", "agency_id", "load_id"))
        assert all(col(n) for n in db.NORMALISED_COLUMNS)
        assert c.execute("select has_schema_privilege(%s, %s, 'USAGE')", (app, tag)).fetchone()[0] and c.execute("select has_schema_privilege(%s, 'public', 'USAGE')", (app,)).fetchone()[0]
        assert c.execute("select bool_and(has_sequence_privilege(%s, c.oid, 'USAGE')) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = %s and c.relkind = 'S'", (app, tag)).fetchone()[0]


def test_the_roles_can_log_in_with_the_passwords_given(scratch):
    tag, dsn, owner, app = scratch
    db.init_db(dsn, "app-secret", "owner-secret", schema=tag, app_role=app, owner_role=owner)
    with psycopg.connect(db.retarget(dsn, user=app, password="app-secret")) as c:
        assert c.execute("select current_user").fetchone()[0] == app
        assert c.execute("show search_path").fetchone()[0] == f"{tag}, public"
    with psycopg.connect(db.retarget(dsn, user=owner, password="owner-secret")) as c:
        assert c.execute("select current_user").fetchone()[0] == owner
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(db.retarget(dsn, user=app, password="wrong"))


def test_a_second_init_changes_nothing_and_keeps_the_first_passwords(scratch):
    tag, dsn, owner, app = scratch
    db.init_db(dsn, "app-secret", "owner-secret", schema=tag, app_role=app, owner_role=owner)
    with psycopg.connect(dsn) as c:
        before = c.execute(f'select name, sha256, applied_at from "{tag}".schema_migration order by name').fetchall()
    db.init_db(dsn, "another", "different", schema=tag, app_role=app, owner_role=owner)
    with psycopg.connect(dsn) as c:
        assert c.execute(f'select name, sha256, applied_at from "{tag}".schema_migration order by name').fetchall() == before
    psycopg.connect(db.retarget(dsn, user=app, password="app-secret")).close()


def test_init_db_reads_the_role_passwords_from_the_environment_when_not_given(scratch, monkeypatch):
    tag, dsn, owner, app = scratch
    monkeypatch.setenv("PARCELKEY_APP_PASSWORD", "env-app")
    monkeypatch.setenv("PARCELKEY_OWNER_PASSWORD", "env-owner")
    db.init_db(dsn, schema=tag, app_role=app, owner_role=owner)
    psycopg.connect(db.retarget(dsn, user=app, password="env-app")).close()
    psycopg.connect(db.retarget(dsn, user=owner, password="env-owner")).close()


# ---- settings -----------------------------------------------------------------------------------------------------------------------------------

def test_password_comes_from_the_environment_then_the_env_file_and_a_missing_one_is_an_error(monkeypatch, tmp_path):
    monkeypatch.delenv("PK_TEST_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="PK_TEST_SECRET is not set"):
        db.password("PK_TEST_SECRET")
    (tmp_path / ".env").write_text("# a comment\n\nPK_TEST_SECRET = from-file\nPK_OTHER=x=y\n#PK_HIDDEN=no\nnot a pair\n", encoding="utf-8")
    assert db.password("PK_TEST_SECRET") == "from-file"
    import os
    assert os.environ["PK_OTHER"] == "x=y" and "PK_HIDDEN" not in os.environ
    monkeypatch.setenv("PK_TEST_SECRET", "from-env")
    assert db.password("PK_TEST_SECRET") == "from-env"


def test_an_empty_value_in_the_env_file_counts_as_not_set(monkeypatch, tmp_path):
    monkeypatch.delenv("PK_EMPTY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PK_EMPTY=\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        db.password("PK_EMPTY")


def test_connection_strings(monkeypatch):
    for var in ("PARCELKEY_PG_HOST", "PARCELKEY_PG_PORT", "PARCELKEY_PG_DB", "PARCELKEY_DSN", "PARCELKEY_ADMIN_DSN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PARCELKEY_APP_PASSWORD", "a")
    monkeypatch.setenv("PARCELKEY_ADMIN_PASSWORD", "b")
    monkeypatch.setenv("PARCELKEY_OWNER_PASSWORD", "c")
    app = conninfo_to_dict(db.default_dsn())
    assert (app["host"], app["port"], app["dbname"], app["user"], app["password"]) == ("localhost", "5436", "parcelkey", "parcelkey_app", "a")
    adm = conninfo_to_dict(db.default_admin_dsn())
    assert (adm["user"], adm["password"]) == ("postgres", "b")
    own = conninfo_to_dict(db.default_owner_dsn())
    assert (own["user"], own["password"], own["dbname"]) == ("parcelkey_owner", "c", "parcelkey")
    monkeypatch.setenv("PARCELKEY_PG_HOST", "db.example.test")
    monkeypatch.setenv("PARCELKEY_PG_PORT", "6000")
    monkeypatch.setenv("PARCELKEY_PG_DB", "other")
    got = conninfo_to_dict(db.default_dsn())
    assert (got["host"], got["port"], got["dbname"]) == ("db.example.test", "6000", "other")
    monkeypatch.setenv("PARCELKEY_DSN", "host=x dbname=y user=parcelkey_app password=z")
    monkeypatch.setenv("PARCELKEY_ADMIN_DSN", "host=x dbname=y user=postgres password=w")
    assert db.default_dsn() == "host=x dbname=y user=parcelkey_app password=z" and db.default_admin_dsn() == "host=x dbname=y user=postgres password=w"
    own = conninfo_to_dict(db.default_owner_dsn())
    assert (own["host"], own["dbname"], own["user"], own["password"]) == ("x", "y", "parcelkey_owner", "c")


def test_retarget_changes_only_what_it_is_told_to():
    out = conninfo_to_dict(db.retarget("host=h port=1 dbname=a user=u password=p", dbname="b", user="v"))
    assert (out["host"], out["port"], out["dbname"], out["user"], out["password"]) == ("h", "1", "b", "v", "p")


# ---- the wipe helpers and the connection guard ------------------------------------------------------------------------------------------------

def test_wipe_empties_agency_tables_and_properties_and_leaves_the_reference_unless_asked(hand, conn, admin, admin_dsn, tmp_path):
    hand([h.addr("G12", "12")])
    h.run(conn, AGENCY, h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St", name="K", email="k@example.com")]))
    count = lambda t: admin.execute(f"select count(*) from {t}").fetchone()[0]
    assert count("source_record") == count("property") == count("gnaf_address") == count("address_detail") == count("contact") == 1
    db.wipe_reference(admin_dsn)
    assert (count("gnaf_address"), count("address_detail"), count("state")) == (0, 0, 0) and (count("source_record"), count("property"), count("contact")) == (1, 1, 1)
    hand([h.addr("G12", "12")])
    db.wipe(admin_dsn)
    assert (count("source_record"), count("property"), count("contact"), count("property_link")) == (0, 0, 0, 0) and count("gnaf_address") == 1
    db.wipe(admin_dsn, reference=True)
    assert (count("gnaf_address"), count("address_detail"), count("locality")) == (0, 0, 0)


def test_wipe_restarts_the_sequences(conn, admin, admin_dsn, tmp_path):
    for i in range(2):
        db.wipe(admin_dsn)
        h.run(conn, AGENCY, h.write_feeds(tmp_path / f"f{i}", crm_rows=[h.crm("C1", "12 Smith St")]))
        assert admin.execute("select load_id from source_load").fetchone()[0] == 1


def test_the_unsafe_connection_is_closed_before_the_refusal_is_raised(admin_dsn):
    marker = "pk_unsafe_" + secrets.token_hex(3)
    with pytest.raises(db.UnsafeRole):
        db.connect(db.retarget(admin_dsn, application_name=marker))
    with psycopg.connect(admin_dsn) as c:
        assert c.execute("select count(*) from pg_stat_activity where application_name = %s", (marker,)).fetchone()[0] == 0


def test_a_safe_connection_is_returned_open_and_idle(conn):
    assert conn.closed is False and conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
