"""Shared fixtures. Tests that need PostgreSQL FAIL when it is not there; they never skip.

Start the database with `python tools/pg.py up` (Docker, PostGIS image, port 5436). CI starts a service container on the same port.
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

settings.register_profile("default", max_examples=int(os.environ.get("PARCELKEY_EXAMPLES", "60")), deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
settings.load_profile("default")

AGENCY = "agency-a"
OTHER = "agency-b"
_STATE = {"reference": ""}        # which reference the database holds right now: "", "world" or "hand"


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    try:
        dsn = db.default_admin_dsn()
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("select 1")
    except psycopg.Error as exc:
        pytest.fail(f"PostgreSQL is not reachable ({exc}). Start it with: python tools/pg.py up", pytrace=False)
    except RuntimeError as exc:
        pytest.fail(f"{exc}", pytrace=False)
    db.init_db(dsn)
    return dsn


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
