"""Load G-NAF layout files and build the flat reference table the matcher reads.

`load_gnaf` reads pipe delimited files named the way the Geoscape unpacking guide shows them (`ACT_ADDRESS_DETAIL_psv.psv`, `Authority_Code_FLAT_TYPE_AUT_psv.psv`),
with a header row. Columns are matched by name, not by position. A column in the file that the table here does not have is skipped and reported; a table that is
not modelled here is skipped and reported. Nothing is guessed: a value that does not cast (a date in an unexpected shape) stops the load with the file name.

This loader was only run on files written by scripts/seed.py in the same layout. It has not read a real Geoscape release.

`build_reference` turns ADDRESS_DETAIL joined to STREET_LOCALITY, LOCALITY, STATE and ADDRESS_DEFAULT_GEOCODE into `gnaf_address`, one normalised row per address.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql

from .normalise import addr_from_gnaf

TABLES = ("state", "locality", "street_locality", "address_detail", "address_default_geocode", "flat_type_aut", "street_type_aut", "level_type_aut")
_STATE_FILE = re.compile(r"^(?P<state>NSW|VIC|QLD|SA|WA|TAS|NT|ACT|OT)_(?P<table>[A-Z0-9_]+)_psv\.psv$")
_AUTH_FILE = re.compile(r"^Authority_Code_(?P<table>[A-Z0-9_]+)_psv\.psv$")


@dataclass
class LoadReport:
    files: int = 0
    rows_read: int = 0
    rows_inserted: int = 0
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
        cur.execute(sql.SQL("INSERT INTO {t} ({cols}) SELECT {sel} FROM stg ON CONFLICT DO NOTHING").format(
            t=sql.Identifier(table), cols=sql.SQL(", ").join(sql.Identifier(c) for c in use), sel=select))
        report.rows_read += n
        report.rows_inserted += cur.rowcount
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
SELECT a.address_detail_pid, a.building_name, a.lot_number_prefix, a.lot_number, a.lot_number_suffix, a.flat_number_prefix, a.flat_number, a.flat_number_suffix,
       a.level_number_prefix, a.level_number, a.level_number_suffix, a.number_first_prefix, a.number_first, a.number_first_suffix, a.number_last_prefix, a.number_last,
       a.number_last_suffix, a.postcode, s.street_name, s.street_type_code, l.locality_name, st.state_abbreviation, g.longitude, g.latitude
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
    """Rebuild gnaf_address from the G-NAF tables. Returns {'addresses': n, 'skipped_no_street': n}. Run as the owner or an administrator."""
    cols = ["address_detail_pid", "building_name", "lot_number_prefix", "lot_number", "lot_number_suffix", "flat_number_prefix", "flat_number", "flat_number_suffix", "level_number_prefix",
            "level_number", "level_number_suffix", "number_first_prefix", "number_first", "number_first_suffix", "number_last_prefix", "number_last", "number_last_suffix", "postcode",
            "street_name", "street_type_code", "locality_name", "state_abbreviation", "longitude", "latitude"]
    done = skipped = 0
    with conn.transaction():
        cur = conn.cursor()
        rows = cur.execute(_FLATTEN).fetchall()
        cur.execute("TRUNCATE gnaf_address")
        cur.execute("CREATE TEMP TABLE ga_stage (LIKE gnaf_address, lon double precision, lat double precision) ON COMMIT DROP")
        with cur.copy("COPY ga_stage (address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode, canon_key, lon, lat) FROM STDIN") as cp:
            for r in rows:
                row = dict(zip(cols, r))
                addr = addr_from_gnaf(row)
                if not addr.street_name:
                    skipped += 1
                    continue
                cp.write_row((row["address_detail_pid"], addr.unit, addr.level, addr.building, addr.lot, addr.number_first, addr.number_last, addr.street_name, addr.street_type,
                              addr.street_key, addr.locality, addr.state, addr.postcode, addr.canon_key,
                              None if row["longitude"] is None else float(row["longitude"]), None if row["latitude"] is None else float(row["latitude"])))
                done += 1
        cur.execute("""INSERT INTO gnaf_address (address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode, canon_key, geog)
                       SELECT address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode, canon_key,
                              CASE WHEN lon IS NULL OR lat IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography END FROM ga_stage""")
        cur.execute("ANALYZE gnaf_address")
    return {"addresses": done, "skipped_no_street": skipped}
