"""tools/measure.py runs in its own database and never in the demo one."""
from __future__ import annotations

import psycopg
import pytest

import measure
from conftest import gen
from parcelkey import db
from parcelkey.match import MatchConfig

pytestmark = pytest.mark.db


def test_measure_refuses_a_connection_that_names_the_demo_database(clear_database_env, tmp_path):
    mp = clear_database_env
    mp.setenv("PARCELKEY_APP_PASSWORD", "a")
    mp.setenv("PARCELKEY_ADMIN_PASSWORD", "b")
    mp.setenv("PARCELKEY_DSN", f"host=nowhere.invalid dbname={db.DEMO_DB} user=parcelkey_app password=a")
    mp.setenv("PARCELKEY_ADMIN_DSN", f"host=nowhere.invalid dbname={db.DEMO_DB} user=postgres password=b")
    with pytest.raises(RuntimeError, match="demo database"):          # refused before any connection: the host does not exist
        measure.run_once(tmp_path, MatchConfig(), verbose=False)


def test_measure_fills_its_own_database_and_leaves_the_one_the_tests_use_alone(clear_database_env, admin_dsn, tmp_path):
    """P5-1: a row in the tests' database survives a measure run, and the measure database holds the run."""
    db.wipe(admin_dsn)                  # every table, not only agency: source_record has no foreign key to it, so whatever an earlier test left there would still be counted below
    with psycopg.connect(admin_dsn) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        c.execute("insert into agency (agency_id) values ('agency-a')")
    gen.generate(tmp_path, seed=5, addresses=120, per_agency=60)
    clear_database_env.delenv("PARCELKEY_PG_DB", raising=False)
    try:
        res = measure.run_once(tmp_path, MatchConfig(), verbose=False)
        assert res["agencies"]["agency-a"]["reconcile"].ok and res["gnaf_rows"] > 0
        with psycopg.connect(db.retarget(admin_dsn, dbname=db.MEASURE_DB)) as c:
            assert c.execute(f"select count(*) from {db.SCHEMA}.source_record").fetchone()[0] > 0
        with psycopg.connect(admin_dsn) as c:
            assert c.execute(f"select count(*) from {db.SCHEMA}.agency").fetchone()[0] == 1
            assert c.execute(f"select count(*) from {db.SCHEMA}.source_record").fetchone()[0] == 0
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{db.MEASURE_DB}" WITH (FORCE)')
