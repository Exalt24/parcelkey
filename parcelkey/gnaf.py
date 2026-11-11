"""Load G-NAF layout files and build the flat reference table the matcher reads.

`load_gnaf` reads pipe delimited files named the way the Geoscape unpacking guide shows them (`ACT_ADDRESS_DETAIL_psv.psv`, `Authority_Code_FLAT_TYPE_AUT_psv.psv`),
with a header row. Columns are matched by name, not by position. A column in the file that the table here does not have is skipped and reported; a table that is
not modelled here is skipped and reported. Nothing is guessed: a value that does not cast (a date in an unexpected shape) stops the load with the file name.

This loader was only run on files written by scripts/seed.py in the same layout. It has not read a real Geoscape release.

A reload is an upsert by primary key. A pid that is new is inserted; a pid whose row differs in any column the file carries is updated (a corrected street, a new retired date); a pid
the file leaves out is left as it is, because a release marks an address gone with DATE_RETIRED and does not drop the row. The report counts rows read, inserted, changed and, of the
changed, how many gained a retired date. `refresh_properties` then brings the master list's copy of each G-NAF address in line with the rebuilt reference.

`build_reference` turns ADDRESS_DETAIL joined to STREET_LOCALITY, LOCALITY, STATE and ADDRESS_DEFAULT_GEOCODE into `gnaf_address`, one normalised row per address.
An address G-NAF marks as an alias (ALIAS_PRINCIPAL = A) is kept as one more spelling of its principal address (ADDRESS_ALIAS names the principal): a record that matches
the alias spelling links to the principal, so the alias is never a property of its own. A row with no ALIAS_PRINCIPAL value counts as a principal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql

from .normalise import addr_from_gnaf, gnaf_display_line

TABLES = ("state", "locality", "street_locality", "address_detail", "address_alias", "primary_secondary", "address_default_geocode", "flat_type_aut", "street_type_aut", "level_type_aut")
_STATE_FILE = re.compile(r"^(?P<state>NSW|VIC|QLD|SA|WA|TAS|NT|ACT|OT)_(?P<table>[A-Z0-9_]+)_psv\.psv$")
_AUTH_FILE = re.compile(r"^Authority_Code_(?P<table>[A-Z0-9_]+)_psv\.psv$")


@dataclass
class LoadReport:
    files: int = 0
    rows_read: int = 0
    rows_inserted: int = 0
    rows_changed: int = 0          # a row that was already there and differs in a column the file carries
    rows_retired: int = 0          # of the changed rows, those that had no retired date and now have one
    skipped_files: list[str] = field(default_factory=list)
    ignored_columns: dict[str, list[str]] = field(default_factory=dict)


def table_for_file(name: str) -> str | None:
    """The table a G-NAF file feeds, from its name, or None when the file is not one of the tables modelled here."""
    m = _STATE_FILE.match(name) or _AUTH_FILE.match(name)
    if not m:
        return None
    table = m.group("table").lower()
    return table if table in TABLES else None


def _columns(cur: psycopg.Cursor, table: str) -> dict[str, str]:
    """column name -> type for a table, from the catalogue."""
    rows = cur.execute(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (table,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _primary_key(cur: psycopg.Cursor, table: str) -> list[str]:
    """The primary key columns of a table, from the catalogue."""
    rows = cur.execute("SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) WHERE i.indrelid = %s::regclass AND i.indisprimary "
                       "ORDER BY array_position(i.indkey::int2[], a.attnum)", (table,)).fetchall()
    return [r[0] for r in rows]


def load_file(cur: psycopg.Cursor, path: Path, table: str, report: LoadReport) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        header = f.readline().rstrip("\r\n")
        file_cols = [c.strip().lower() for c in header.split("|")]
        known = _columns(cur, table)
        ignored = [c for c in file_cols if c not in known]
        use = [c for c in file_cols if c in known]
        if ignored:
            report.ignored_columns[path.name] = ignored
        cur.execute(sql.SQL("CREATE TEMP TABLE stg ({}) ON COMMIT DROP").format(sql.SQL(", ").join(sql.SQL("{} text").format(sql.Identifier(c)) for c in file_cols)))
        n = 0
        with cur.copy(sql.SQL("COPY stg ({}) FROM STDIN WITH (FORMAT csv, DELIMITER '|', QUOTE E'\\x1f')").format(sql.SQL(", ").join(sql.Identifier(c) for c in file_cols))) as cp:
            for line in f:
                if line.strip():
                    cp.write(line if line.endswith("\n") else line + "\n")
                    n += 1
        select = sql.SQL(", ").join(sql.SQL("NULLIF({}, '')::{}").format(sql.Identifier(c), sql.SQL(known[c])) for c in use)
        key = _primary_key(cur, table)
        missing_key = [k for k in key if k not in use]
        if missing_key:
            raise ValueError(f"{path.name}: the primary key column(s) {', '.join(missing_key)} are not in the file")
        change = [c for c in use if c not in key]
        if not change:
            raise ValueError(f"{path.name}: the file has only key columns")
        ident = sql.Identifier(table)
        if "date_retired" in change:          # rows that are live here and carry a retired date in the file: counted before the upsert changes them
            on = sql.SQL(" AND ").join(sql.SQL("x.{k} = NULLIF(s.{k}, '')::{t}").format(k=sql.Identifier(k), t=sql.SQL(known[k])) for k in key)
            report.rows_retired += cur.execute(sql.SQL("SELECT count(*) FROM (SELECT DISTINCT {keys} FROM stg s JOIN {t} x ON {on} WHERE x.date_retired IS NULL AND NULLIF(s.date_retired, '') IS NOT NULL) d").format(
                keys=sql.SQL(", ").join(sql.SQL("s.{}").format(sql.Identifier(k)) for k in key), t=ident, on=on)).fetchone()[0]
        keycols = sql.SQL(", ").join(sql.Identifier(k) for k in key)
        action = sql.SQL("DO UPDATE SET {sets} WHERE ({old}) IS DISTINCT FROM ({new})").format(
            sets=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in change),
            old=sql.SQL(", ").join(sql.SQL("{}.{}").format(ident, sql.Identifier(c)) for c in change), new=sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in change))
        rows = cur.execute(sql.SQL("INSERT INTO {t} ({cols}) SELECT {sel} FROM (SELECT DISTINCT ON ({keys}) * FROM stg ORDER BY {keys}, ctid DESC) stg ON CONFLICT ({keys}) {action} RETURNING (xmax = 0)").format(
            t=ident, cols=sql.SQL(", ").join(sql.Identifier(c) for c in use), sel=select, keys=keycols, action=action)).fetchall()
        inserted = sum(1 for (new_row,) in rows if new_row)
        report.rows_inserted += inserted
        report.rows_changed += len(rows) - inserted
        report.rows_read += n
        cur.execute("DROP TABLE stg")


def load_gnaf(conn: psycopg.Connection, directory: Path) -> LoadReport:
    """Load every modelled table file found under `directory` (any depth), in the order the tables depend on each other. Run as the owner or an administrator."""
    report = LoadReport()
    found: dict[str, list[Path]] = {t: [] for t in TABLES}
    for path in sorted(Path(directory).rglob("*.psv")):
        table = table_for_file(path.name)
        if table is None:
            report.skipped_files.append(path.name)
        else:
            found[table].append(path)
    with conn.transaction():
        cur = conn.cursor()
        for table in TABLES:
            for path in found[table]:
                try:
                    load_file(cur, path, table, report)
                except psycopg.Error as exc:
                    raise ValueError(f"{path.name}: {str(exc).splitlines()[0]}") from exc
                report.files += 1
    return report


_FLATTEN = """
SELECT a.address_detail_pid, a.building_name, a.lot_number_prefix, a.lot_number, a.lot_number_suffix, a.flat_type_code, a.flat_number_prefix, a.flat_number, a.flat_number_suffix,
       a.level_type_code, a.level_number_prefix, a.level_number, a.level_number_suffix, a.number_first_prefix, a.number_first, a.number_first_suffix, a.number_last_prefix,
       a.number_last, a.number_last_suffix, a.postcode, a.alias_principal, s.street_name, s.street_type_code, s.street_suffix_code, l.locality_name, st.state_abbreviation,
       g.longitude, g.latitude
FROM address_detail a
JOIN locality l ON l.locality_pid = a.locality_pid
JOIN state st ON st.state_pid = l.state_pid
LEFT JOIN street_locality s ON s.street_locality_pid = a.street_locality_pid
LEFT JOIN LATERAL (SELECT longitude, latitude FROM address_default_geocode g WHERE g.address_detail_pid = a.address_detail_pid AND g.date_retired IS NULL
                   ORDER BY g.address_default_geocode_pid LIMIT 1) g ON true
WHERE a.date_retired IS NULL
ORDER BY a.address_detail_pid
"""


def build_reference(conn: psycopg.Connection) -> dict[str, int]:
    """Rebuild gnaf_address from the G-NAF tables. Returns {'addresses': n, 'aliases': n, 'skipped_no_street': n, 'skipped_alias_without_principal': n}.
    Run as the owner or an administrator."""
    cols = ["address_detail_pid", "building_name", "lot_number_prefix", "lot_number", "lot_number_suffix", "flat_type_code", "flat_number_prefix", "flat_number", "flat_number_suffix",
            "level_type_code", "level_number_prefix", "level_number", "level_number_suffix", "number_first_prefix", "number_first", "number_first_suffix", "number_last_prefix",
            "number_last", "number_last_suffix", "postcode", "alias_principal", "street_name", "street_type_code", "street_suffix_code", "locality_name", "state_abbreviation",
            "longitude", "latitude"]
    done = aliases_done = skipped = orphans = 0
    with conn.transaction():
        cur = conn.cursor()
        rows = cur.execute(_FLATTEN).fetchall()
        principal_of = dict(cur.execute("SELECT DISTINCT ON (alias_pid) alias_pid, principal_pid FROM address_alias WHERE date_retired IS NULL ORDER BY alias_pid, address_alias_pid").fetchall())
        kept = []
        for r in rows:
            row = dict(zip(cols, r))
            addr = addr_from_gnaf(row)
            if addr.street_name:
                kept.append((row, addr))
            else:
                skipped += 1
        principals = {row["address_detail_pid"] for row, _ in kept if row["alias_principal"] != "A"}
        cur.execute("TRUNCATE gnaf_address")
        cur.execute("CREATE TEMP TABLE ga_stage (LIKE gnaf_address, lon double precision, lat double precision) ON COMMIT DROP")
        with cur.copy("COPY ga_stage (address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode, canon_key,"
                      " principal_pid, display_address, lon, lat) FROM STDIN") as cp:
            for row, addr in kept:
                pid = row["address_detail_pid"]
                principal = None
                if row["alias_principal"] == "A":
                    principal = principal_of.get(pid)
                    if principal not in principals:
                        orphans += 1
                        continue
                cp.write_row((pid, addr.unit, addr.level, addr.building, addr.lot, addr.number_first, addr.number_last, addr.street_name, addr.street_type,
                              addr.street_key, addr.locality, addr.state, addr.postcode, addr.canon_key, principal, gnaf_display_line(row),
                              None if row["longitude"] is None else float(row["longitude"]), None if row["latitude"] is None else float(row["latitude"])))
                if principal is None:
                    done += 1
                else:
                    aliases_done += 1
        cur.execute("""INSERT INTO gnaf_address (address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode, canon_key,
                                                 principal_pid, display_address, geog)
                       SELECT address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode, canon_key,
                              principal_pid, display_address, CASE WHEN lon IS NULL OR lat IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography END FROM ga_stage""")
        cur.execute("ANALYZE gnaf_address")          # no-mutate: planner statistics only
    return {"addresses": done, "aliases": aliases_done, "skipped_no_street": skipped, "skipped_alias_without_principal": orphans}


def refresh_properties(conn: psycopg.Connection) -> dict[str, int]:
    """After a reload and a rebuild: make the master list's copy of each G-NAF address (display line, postcode, state, canonical key, point) the one `gnaf_address` now holds. The id
    never changes: it hashes the pid. Returns {'refreshed': n, 'retired': n}, where 'retired' counts G-NAF properties whose pid is no longer in the reference (the release retired it or
    left it out); those are left as they are, because an agency's links point at them and a person decides what to merge. Run as the owner or an administrator."""
    with conn.transaction():
        cur = conn.cursor()
        cur.execute("""UPDATE property p SET display_address = g.display_address, postcode = g.postcode, state = g.state, canon_key = g.canon_key, geog = g.geog
                       FROM gnaf_address g WHERE p.origin = 'gnaf' AND p.gnaf_pid = g.address_detail_pid
                        AND (p.display_address, p.postcode, p.state, p.canon_key, coalesce(ST_AsText(p.geog), '')) IS DISTINCT FROM
                            (g.display_address, g.postcode, g.state, g.canon_key, coalesce(ST_AsText(g.geog), ''))""")
        refreshed = cur.rowcount
        retired = cur.execute("""SELECT count(*) FROM property p WHERE p.origin = 'gnaf' AND NOT EXISTS (SELECT 1 FROM gnaf_address g WHERE g.address_detail_pid = p.gnaf_pid)""").fetchone()[0]
    return {"refreshed": refreshed, "retired": retired}
