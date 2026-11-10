"""Connections, roles, migrations and the agency setting.

Two Postgres roles, so row level security is real and not an accident of being a superuser:
  parcelkey_owner  owns the tables (row level security is FORCED, so it is held to the policy too)
  parcelkey_app    the role the pipeline uses: no superuser, no BYPASSRLS, only the table privileges granted below
`init_db` runs once with an administrator connection and applies the plain SQL files in `parcelkey/migrations` in order.
"""
from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

SCHEMA = "parcelkey"
APP_ROLE = "parcelkey_app"
OWNER_ROLE = "parcelkey_owner"
DEFAULT_PORT = 5436
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
INIT_LOCK = 74160002                      # no-mutate: any number does, it only has to be the same in every call (two init runs at once take turns)

# Tables that hold agency or customer data: each has an agency_id and forced row level security.
AGENCY_TABLES = ("contact_quarantine", "contact_merge", "contact_link", "contact_identifier", "contact", "quarantine", "property_link",
                 "ingest_reject", "source_record", "source_load", "agency")
# Shared reference data, written by the owner side only (loaders), read by the app role.
REFERENCE_TABLES = ("address_default_geocode", "address_detail", "address_alias", "primary_secondary", "street_locality", "locality", "state", "flat_type_aut", "street_type_aut", "level_type_aut",
                    "gnaf_address")
# The master list: the app role may add a property and read all of them, never change or delete one.
MASTER_TABLES = ("property",)
# Columns of source_record the pipeline may change after ingest. The raw row and its hash are never updatable by the app role.
NORMALISED_COLUMNS = ("norm_status", "norm_rules", "unit", "level", "building", "lot", "number_first", "number_last", "street_name", "street_type", "street_key",
                      "locality_key", "state", "postcode", "canon_key", "geog", "contact_name", "contact_email", "contact_phone", "contact_rules")


class UnsafeRole(RuntimeError):
    """The connection's role would skip row level security."""


def _dotenv() -> None:
    """Read KEY=VALUE lines from a .env in the working folder or the repo root. Real environment variables win. tools/pg.py writes this file."""
    for path in (Path.cwd() / ".env", ENV_FILE):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


def password(var: str) -> str:
    _dotenv()
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(f"{var} is not set: run `python tools/pg.py up`, which writes .env, or export it")
    return value


def retarget(dsn: str, **changes: str) -> str:
    """The same connection string with some parts (user, password, dbname, port, host) replaced."""
    parts = conninfo_to_dict(dsn)
    parts.update(changes)
    return make_conninfo(**parts)


def _base(user: str, pw: str) -> str:
    return make_conninfo(host=os.environ.get("PARCELKEY_PG_HOST", "localhost"), port=os.environ.get("PARCELKEY_PG_PORT", str(DEFAULT_PORT)),
                         dbname=os.environ.get("PARCELKEY_PG_DB", "parcelkey"), user=user, password=pw)


def default_dsn() -> str:
    return os.environ.get("PARCELKEY_DSN") or _base(APP_ROLE, password("PARCELKEY_APP_PASSWORD"))


def default_admin_dsn() -> str:
    return os.environ.get("PARCELKEY_ADMIN_DSN") or _base("postgres", password("PARCELKEY_ADMIN_PASSWORD"))


def default_owner_dsn() -> str:
    """The table owner role on the same server and database as the app role (tests use it to show that FORCE row level security binds the owner too)."""
    return retarget(os.environ.get("PARCELKEY_DSN") or _base(APP_ROLE, ""), user=OWNER_ROLE, password=password("PARCELKEY_OWNER_PASSWORD"))


def migration_files() -> list[tuple[str, str]]:
    """(file name, sql text) of every migration, in name order."""
    root = resources.files("parcelkey").joinpath("migrations")
    files = sorted((p for p in root.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def _set_search_path(conn: psycopg.Connection, role: str, schema: str) -> None:
    """Give a role its default search path. A role setting is cluster wide, so two databases starting at once must not both write it: it is read first and a lost race is retried."""
    wanted = f"search_path={schema}, public"
    have = conn.execute("SELECT coalesce(rolconfig, '{}') FROM pg_roles WHERE rolname = %s", (role,)).fetchone()[0]
    if wanted in have:          # no-mutate: only avoids a write when nothing changes
        return
    for attempt in range(5):          # no-mutate: retry plumbing for a race between two test databases, not behaviour
        try:
            conn.execute(sql.SQL("ALTER ROLE {} SET search_path = {}, public").format(sql.Identifier(role), sql.Identifier(schema)))
            return
        except psycopg.errors.InternalError_:          # no-mutate: tuple concurrently updated, the other writer set it
            time.sleep(0.2 * (attempt + 1))          # no-mutate: back off


def init_db(admin_dsn: str, app_password: str | None = None, owner_password: str | None = None, *, schema: str = SCHEMA, app_role: str = APP_ROLE, owner_role: str = OWNER_ROLE) -> None:
    """Create the roles, the schema, the extensions and the tables. Safe to run again: a migration that was applied is not run twice."""
    app_password = app_password or password("PARCELKEY_APP_PASSWORD")
    owner_password = owner_password or password("PARCELKEY_OWNER_PASSWORD")
    sch, owner, app = sql.Identifier(schema), sql.Identifier(owner_role), sql.Identifier(app_role)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (INIT_LOCK,))          # no-mutate: serialises two inits at once, released when this connection closes
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for role, pw in ((owner_role, owner_password), (app_role, app_password)):
            try:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(pw)))
            except psycopg.errors.DuplicateObject:
                pass                                                      # a role that exists keeps its password, whether it was made earlier or by another init a moment ago
            _set_search_path(conn, role, schema)
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(sch, owner))
    with psycopg.connect(admin_dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (INIT_LOCK,))          # no-mutate: serialises two inits at once
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(owner))
        conn.execute(sql.SQL("SET LOCAL search_path = {}, public").format(sch))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migration (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0]: r[1] for r in conn.execute("SELECT name, sha256 FROM schema_migration").fetchall()}
        for name, text in migration_files():
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if name in done:
                if done[name] != digest:
                    raise RuntimeError(f"migration {name} was changed after it was applied; add a new migration instead")
                continue
            conn.execute(text)
            conn.execute("INSERT INTO schema_migration (name, sha256) VALUES (%s, %s)", (name, digest))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sch, app))
        for t in AGENCY_TABLES:
            conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(sql.Identifier(t), app))
        for t in REFERENCE_TABLES:
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(t), app))
        for t in MASTER_TABLES:
            conn.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(sql.Identifier(t), app))
        conn.execute(sql.SQL("REVOKE UPDATE ON source_record FROM {}").format(app))
        conn.execute(sql.SQL("GRANT UPDATE ({}) ON source_record TO {}").format(sql.SQL(", ").join(sql.Identifier(c) for c in NORMALISED_COLUMNS), app))
        for v in ("campaign_property", "contact_property"):
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(v), app))
        conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(sch, app))


def wipe(admin_dsn: str, *, reference: bool = False) -> None:
    """Empty the agency tables, the master property list and (with reference=True) the G-NAF tables. Administrator connection, for tests and demos."""
    tables = [*AGENCY_TABLES, *MASTER_TABLES, *(REFERENCE_TABLES if reference else ())]
    with psycopg.connect(admin_dsn) as conn:                       # the with block commits
        conn.execute(f"TRUNCATE {', '.join(SCHEMA + '.' + t for t in tables)} RESTART IDENTITY")


def wipe_reference(admin_dsn: str) -> None:
    """Empty only the G-NAF tables and the flat reference, leaving every agency table and the master property list as they are (tests that change the reference mid-run)."""
    with psycopg.connect(admin_dsn) as conn:
        conn.execute(f"TRUNCATE {', '.join(SCHEMA + '.' + t for t in REFERENCE_TABLES)}")


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Open a connection as the app role and refuse a role that would bypass row level security."""
    conn = psycopg.connect(dsn or default_dsn(), connect_timeout=10)          # no-mutate: the timeout is a number of seconds, any will do
    row = conn.execute("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    conn.rollback()
    if row is None or row[1] or row[2]:
        conn.close()          # no-mutate: garbage collection would close it anyway, this only makes it prompt
        raise UnsafeRole(f"role {row[0] if row else '?'} is a superuser or bypasses row level security; connect as {APP_ROLE}")
    return conn


@contextmanager
def agency_tx(conn: psycopg.Connection, agency: str) -> Iterator[psycopg.Cursor]:
    """One transaction in which row level security shows only `agency`. Commits on exit, rolls back on an exception."""
    if not agency or not agency.strip():
        raise ValueError("agency id must not be empty")
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError("agency_tx needs a connection with no open transaction (a nested block would not commit)")
    with conn.transaction():
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.agency_id', %s, true)", (agency,))
        yield cur
