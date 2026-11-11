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
        assert [n for n, _ in applied] == [n for n, _ in db.migration_files()] and len(applied) == 5
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
    assert (app["host"], app["port"], app["dbname"], app["user"], app["password"]) == ("127.0.0.1", "5436", "parcelkey", "parcelkey_app", "a")
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


# ---- the tests and the measure script never touch the demo database --------------------------------------------------------------------------------

DEMO_DSN = "host=127.0.0.1 port=5436 dbname=parcelkey user=postgres password=x"
NOWHERE_DSN = "host=nowhere.invalid port=1 dbname={name} user=postgres password=x connect_timeout=1"


def test_the_tests_run_in_their_own_database_and_not_the_demo_one(admin_dsn, conn):
    """P5-1: the README's Run it steps fill the demo database; the suite empties tables all the time, so it must be somewhere else."""
    assert db.database_name(admin_dsn) != db.DEMO_DB and db.database_name(db.default_dsn()) == db.database_name(admin_dsn)
    assert conn.execute("select current_database()").fetchone()[0] == db.database_name(admin_dsn)
    assert (db.DEMO_DB, db.TEST_DB, db.MEASURE_DB) == ("parcelkey", "parcelkey_test", "parcelkey_measure")


@pytest.mark.parametrize("dsn, refused", [
    (DEMO_DSN, True), ("dbname=parcelkey host=h", True), ("host=h dbname=parcelkey_test", False), ("host=h dbname=parcelkey_measure", False), ("host=h dbname=Parcelkey", False),
    ("host=h dbname=postgres", False)])
def test_refuse_demo_database_names_only_the_demo_database(dsn, refused):
    if refused:
        with pytest.raises(RuntimeError, match="demo database 'parcelkey'"):
            db.refuse_demo_database(dsn)
    else:
        db.refuse_demo_database(dsn)


def test_refuse_demo_database_checks_every_string_it_is_given():
    db.refuse_demo_database("dbname=a", "dbname=b")
    with pytest.raises(RuntimeError):
        db.refuse_demo_database("dbname=a", DEMO_DSN)
    with pytest.raises(RuntimeError):
        db.refuse_demo_database(DEMO_DSN, "dbname=a")


@pytest.mark.parametrize("call", ["wipe", "wipe_reference"])
def test_every_function_that_empties_tables_refuses_the_demo_database_before_it_connects(call):
    """CLASS: a function that truncates takes the guard first. The address is not reachable, so a connection attempt would be a different error."""
    fn = getattr(db, call)
    with pytest.raises(RuntimeError, match="demo database"):
        fn(NOWHERE_DSN.format(name=db.DEMO_DB))
    with pytest.raises(psycopg.OperationalError):          # a database that is not the demo one gets as far as a connection
        fn(NOWHERE_DSN.format(name="somewhere_else"))


def test_every_truncate_in_the_package_and_the_tools_sits_in_a_guarded_function_or_a_reference_rebuild():
    """CLASS: nothing new empties tables without the guard. The only other TRUNCATE is gnaf.build_reference, which rebuilds the flat table from the tables it was just given."""
    import ast
    from pathlib import Path
    root = Path(db.__file__).resolve().parent.parent
    found: dict[str, list[str]] = {}
    for path in [*(root / "parcelkey").glob("*.py"), *(root / "tools").glob("*.py")]:
        source = path.read_text(encoding="utf-8")
        for fn in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)):
            text = ast.get_source_segment(source, fn) or ""
            if "TRUNCATE" in text.upper():
                found.setdefault(path.name, []).append(fn.name)
                if path.name == "db.py":
                    assert "refuse_demo_database(" in text, f"{fn.name} empties tables without refusing the demo database"
    assert found == {"db.py": ["wipe", "wipe_reference"], "gnaf.py": ["build_reference"]}


def test_aim_at_sets_the_database_name_and_drops_a_lone_dsn(clear_database_env):
    import os
    mp = clear_database_env
    db.aim_at("pk_x")
    assert os.environ["PARCELKEY_PG_DB"] == "pk_x"
    mp.setenv("PARCELKEY_DSN", "dbname=elsewhere")
    db.aim_at("pk_y")
    assert os.environ["PARCELKEY_PG_DB"] == "pk_y" and "PARCELKEY_DSN" not in os.environ
    mp.setenv("PARCELKEY_ADMIN_DSN", "dbname=elsewhere")
    db.aim_at("pk_z")
    assert os.environ["PARCELKEY_PG_DB"] == "pk_z" and "PARCELKEY_ADMIN_DSN" not in os.environ


def test_aim_at_keeps_a_pair_of_dsns_the_environment_names(clear_database_env):
    """The mutation check gives each worker its own database through the two DSNs; they win over the name."""
    import os
    mp = clear_database_env
    mp.setenv("PARCELKEY_DSN", "dbname=worker user=a")
    mp.setenv("PARCELKEY_ADMIN_DSN", "dbname=worker user=b")
    db.aim_at("pk_x")
    assert os.environ["PARCELKEY_DSN"] == "dbname=worker user=a" and os.environ["PARCELKEY_ADMIN_DSN"] == "dbname=worker user=b" and "PARCELKEY_PG_DB" not in os.environ


def test_select_database_refuses_the_demo_database_however_it_is_named(clear_database_env):
    mp = clear_database_env
    mp.setenv("PARCELKEY_APP_PASSWORD", "a")
    mp.setenv("PARCELKEY_ADMIN_PASSWORD", "b")
    with pytest.raises(RuntimeError, match="demo database"):
        db.select_database(db.DEMO_DB)
    mp.setenv("PARCELKEY_DSN", f"dbname={db.DEMO_DB} user=parcelkey_app")
    mp.setenv("PARCELKEY_ADMIN_DSN", f"dbname={db.DEMO_DB} user=postgres")
    with pytest.raises(RuntimeError, match="demo database"):
        db.select_database(db.TEST_DB)
    mp.setenv("PARCELKEY_ADMIN_DSN", f"dbname={db.TEST_DB} user=postgres")
    with pytest.raises(RuntimeError, match="demo database"):          # one DSN on the demo database is enough
        db.select_database(db.TEST_DB)


def test_select_database_wants_one_database_for_both_roles(clear_database_env):
    mp = clear_database_env
    mp.setenv("PARCELKEY_DSN", "dbname=one user=parcelkey_app")
    mp.setenv("PARCELKEY_ADMIN_DSN", "dbname=two user=postgres")
    with pytest.raises(RuntimeError, match="must be one database"):
        db.select_database("whatever")


def test_select_database_returns_both_strings_aimed_at_the_name(clear_database_env):
    mp = clear_database_env
    mp.setenv("PARCELKEY_APP_PASSWORD", "a")
    mp.setenv("PARCELKEY_ADMIN_PASSWORD", "b")
    admin, app = db.select_database("pk_named")
    assert (db.database_name(admin), db.database_name(app)) == ("pk_named", "pk_named")


def test_use_own_database_makes_the_database_and_its_schema_and_runs_again_without_harm(clear_database_env, admin_dsn):
    tag = "pk_t" + secrets.token_hex(3)
    try:
        admin, app = db.use_own_database(tag)
        with psycopg.connect(admin) as c:
            assert c.execute("select current_database()").fetchone()[0] == tag
            assert c.execute("select count(*) from pg_tables where schemaname = %s and tablename = 'source_record'", (db.SCHEMA,)).fetchone()[0] == 1
        db.use_own_database(tag)                          # a second call finds it and leaves it
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            assert c.execute("select count(*) from pg_database where datname = %s", (tag,)).fetchone()[0] == 1
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{tag}" WITH (FORCE)')


def test_ensure_database_creates_a_missing_database_and_leaves_an_existing_one(admin_dsn):
    tag = "pk_t" + secrets.token_hex(3)
    dsn = db.retarget(admin_dsn, dbname=tag)
    try:
        for _ in range(2):
            db.ensure_database(dsn)
            with psycopg.connect(admin_dsn) as c:
                assert c.execute("select count(*) from pg_database where datname = %s", (tag,)).fetchone()[0] == 1
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{tag}" WITH (FORCE)')


# ---- an agency that was never loaded (P5-13) ----------------------------------------------------------------------------------------------------

def test_require_agency_passes_a_started_agency_and_refuses_any_other(conn, admin):
    admin.execute("insert into agency (agency_id) values (%s), ('agency-b')", (AGENCY,))
    with db.agency_tx(conn, AGENCY) as cur:
        assert db.agency_exists(cur, AGENCY) is True and db.agency_exists(cur, "agency-b") is False          # row level security shows a transaction only its own agency
        db.require_agency(cur, AGENCY)
        with pytest.raises(db.UnknownAgency, match="no agency 'typo' has been loaded: check the spelling$"):
            db.require_agency(cur, "typo")
        with pytest.raises(db.UnknownAgency, match="check the spelling, or pass --new-agency to start it with this run$"):
            db.require_agency(cur, "typo", can_create=True)
    assert issubclass(db.UnknownAgency, ValueError)


# ---- migration 005 on a database that already holds loads (P5-3) --------------------------------------------------------------------------------------

def test_the_load_time_migration_upgrades_a_database_that_already_holds_loads(scratch, monkeypatch):
    """A database made before migration 005 has loads whose loaded_at is the arrival time: it becomes received_at, a load that stored nothing loses its loaded_at, row level security stays forced."""
    from datetime import datetime, timezone
    tag, dsn, owner, app = scratch
    everything = db.migration_files()
    assert everything[-1][0] == "005_load_time.sql"
    monkeypatch.setattr(db, "migration_files", lambda: everything[:-1])
    db.init_db(dsn, "app-secret", "owner-secret", schema=tag, app_role=app, owner_role=owner)
    first, second = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    with psycopg.connect(dsn) as c:
        c.execute(f'set search_path = "{tag}", public')
        c.execute("insert into agency (agency_id) values ('a')")
        c.execute("""insert into source_load (agency_id, source, file_name, file_sha256, row_count, stored, rejected, unchanged, loaded_at)
                     values ('a', 'crm', 'f1.csv', 's1', 2, 2, 0, 0, %s), ('a', 'crm', 'f2.csv', 's2', 2, 0, 2, 0, %s)""", (first, second))
    monkeypatch.setattr(db, "migration_files", lambda: everything)
    db.init_db(dsn, "app-secret", "owner-secret", schema=tag, app_role=app, owner_role=owner)
    with psycopg.connect(dsn) as c:
        c.execute(f'set search_path = "{tag}", public')
        assert c.execute("select file_name, stored, loaded_at, received_at from source_load order by load_id").fetchall() == [("f1.csv", 2, first, first), ("f2.csv", 0, None, second)]
        assert c.execute("select relrowsecurity, relforcerowsecurity from pg_class where relname = 'source_load' and relnamespace = %s::regnamespace", (f'"{tag}"',)).fetchone() == (True, True)
        assert c.execute("select a.attnotnull from pg_attribute a where a.attrelid = 'source_load'::regclass and a.attname in ('loaded_at', 'received_at') order by a.attname").fetchall() == [(False,), (True,)]
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("""insert into source_load (agency_id, source, file_name, file_sha256, row_count, stored, rejected, unchanged) values ('a', 'crm', 'f3.csv', 's3', 1, 1, 0, 0)""")
