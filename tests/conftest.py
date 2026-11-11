"""Shared fixtures. Tests that need PostgreSQL FAIL when it is not there; they never skip.

Start the server with `python tools/pg.py up` (Docker, PostGIS image, port 5436). CI starts a service container on the same port.
The tests empty tables all the time, so they run in their own database on that server (`parcelkey_test`, made on the first run) and never in the demo database the README's
Run it steps fill. A run that would point at the demo database fails before it writes anything.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, settings

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from parcelkey import db, gnaf  # noqa: E402

import seed as gen  # noqa: E402

db.aim_at(db.TEST_DB)          # before any test reads a connection string; a mutation worker that names both DSNs keeps its own database

settings.register_profile("default", max_examples=int(os.environ.get("PARCELKEY_EXAMPLES", "60")), deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
settings.load_profile("default")

AGENCY = "agency-a"
OTHER = "agency-b"
_STATE = {"reference": ""}        # which reference the database holds right now: "", "world" or "hand"


CLAIM_KEY = 5436001            # one advisory lock key; the lock is per database, so a mutation worker with its own database never meets another run's claim


def claim_database(dsn: str) -> psycopg.Connection:
    """Hold this database for one test run: the tests empty and refill its tables, so a second run on the same database corrupts the first one's counts (a test that truncates a table and
    then counts it sees the other run's rows and fails with a number that points at the code, not at the second run). The lock lives as long as the returned connection stays open."""
    conn = psycopg.connect(dsn, connect_timeout=5, autocommit=True)
    if not conn.execute("select pg_try_advisory_lock(%s)", (CLAIM_KEY,)).fetchone()[0]:
        conn.close()
        raise RuntimeError(f"another run is using the database {db.database_name(dsn)!r} right now; two runs on one test database empty each other's tables and fail with wrong counts. Wait for it to finish, then run again.")
    return conn


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    try:
        dsn, _ = db.select_database(db.TEST_DB)
        db.ensure_database(dsn)
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("select 1")
        held = claim_database(dsn)
    except psycopg.Error as exc:
        pytest.fail(f"PostgreSQL is not reachable ({exc}). Start it with: python tools/pg.py up", pytrace=False)
    except RuntimeError as exc:
        pytest.fail(f"{exc}", pytrace=False)
    db.init_db(dsn)
    yield dsn
    held.close()


@pytest.fixture()
def clear_database_env(monkeypatch):
    """Forget the database settings for one test, and put them back after it (the setenv first makes monkeypatch remember a variable that was not set)."""
    for var in ("PARCELKEY_DSN", "PARCELKEY_ADMIN_DSN", "PARCELKEY_PG_DB"):
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    return monkeypatch


@pytest.fixture()
def clean(admin_dsn):
    """Empty agency tables and an empty master property list for one test. The G-NAF reference is left as it is."""
    db.wipe(admin_dsn)
    return admin_dsn


@pytest.fixture()
def conn(clean):
    c = db.connect()
    yield c
    c.close()


@pytest.fixture()
def admin(clean):
    """Superuser connection with the parcelkey search path, used only to damage data on purpose and to look past row level security."""
    c = psycopg.connect(clean, autocommit=True)
    c.execute(f"set search_path = {db.SCHEMA}, public")
    yield c
    c.close()


@pytest.fixture(scope="session")
def world_dir(tmp_path_factory) -> Path:
    """A small generated world: made-up G-NAF layout files, two agencies of messy feeds and the answer key."""
    out = tmp_path_factory.mktemp("world")
    gen.generate(out, seed=5, addresses=500, per_agency=420)
    return out


@pytest.fixture()
def world(world_dir, clean):
    """The generated G-NAF files loaded and flattened. Reloaded only when a test replaced the reference in between."""
    if _STATE["reference"] != "world":
        db.wipe(clean, reference=True)
        with psycopg.connect(clean) as c:
            c.execute(f"set search_path = {db.SCHEMA}, public")
            gnaf.load_gnaf(c, world_dir / "gnaf")
            gnaf.build_reference(c)
        _STATE["reference"] = "world"
    return world_dir


@pytest.fixture()
def hand(clean):
    """Replace the reference with hand written addresses: hand(list of dicts) (see tests/helpers.py)."""
    import helpers

    def put(addresses):
        helpers.set_reference(clean, addresses)
        _STATE["reference"] = "hand"

    return put


def snapshot(admin_conn, agency: str | None = None) -> dict[str, list[tuple]]:
    """Every pipeline table as sorted rows (decided_at included), for proving that a second run writes nothing."""
    out: dict[str, list[tuple]] = {}
    for t in ("agency", "source_load", "source_record", "ingest_reject", "property", "property_link", "quarantine", "contact", "contact_identifier", "contact_link", "contact_merge",
              "contact_quarantine"):
        rows = admin_conn.execute(f"select xmin::text, * from {t}").fetchall()          # xmin changes on every write, even one that stores the same values
        out[t] = sorted(tuple(str(x) for x in r) for r in rows)
    return out


def pytest_collection_modifyitems(items):
    """Mark the two kinds of test a mutant must be killed by in the class-only mutation run: a generated table (`CLASS-TABLE:` in its docstring or its module's) and a corpus replay."""
    for item in items:
        function = getattr(item, "function", None)
        if "CLASS-TABLE:" in ((function.__doc__ if function else "") or "") + (item.module.__doc__ or ""):
            item.add_marker(pytest.mark.class_table)
        if item.module.__name__.endswith("test_corpus"):
            item.add_marker(pytest.mark.corpus)


os.environ.setdefault("PYTHONHASHSEED", "0")
