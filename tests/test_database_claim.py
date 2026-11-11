"""Two test runs on one database corrupt each other (a truncate in one run shows up as stray rows in the other's count); the suite now claims its database and the second run is refused."""
from __future__ import annotations

import psycopg
import pytest

from conftest import claim_database
from parcelkey import db

pytestmark = pytest.mark.db


def test_a_second_run_on_the_database_this_run_holds_is_refused_in_plain_words(admin_dsn):
    with pytest.raises(RuntimeError, match=r"another run is using the database 'parcelkey_test'.*empty each other's tables"):
        claim_database(admin_dsn)


def test_a_claim_is_freed_when_its_connection_closes_and_a_database_holds_one_claim_at_a_time(admin_dsn):
    name = "parcelkey_claimcheck"
    dsn = db.retarget(admin_dsn, dbname=name)
    db.ensure_database(dsn)
    try:
        first = claim_database(dsn)
        with pytest.raises(RuntimeError, match="another run is using"):
            claim_database(dsn)
        first.close()
        claim_database(dsn).close()                       # free again once the holder is gone
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_a_claim_on_one_database_does_not_block_another_database(admin_dsn):
    name = "parcelkey_claimcheck_b"
    dsn = db.retarget(admin_dsn, dbname=name)
    db.ensure_database(dsn)
    try:
        held = claim_database(dsn)                        # admin_dsn's own database is claimed by this run too: a worker with its own database must not collide with it
        held.close()
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
