"""Read a source feed into `source_record`, byte for byte, and set aside the rows that cannot be stored.

Three sample feeds are known: a CRM export, a listing feed and a sales history. Each has a column map (`SOURCES`). A feed whose header lacks a mapped column is
refused whole, before any row is written, with the missing names. A file is identified by its SHA-256: loading the same bytes again writes nothing.

A load has a `loaded_at` only when it stored a row. A file whose rows were all rejected or unchanged is recorded (with `received_at`) and has no `loaded_at`, so the freshness
monitor measures the last time a feed changed what is stored, never the last time a file arrived.

Rows that cannot be stored go to `ingest_reject` with a reason: more cells than the header has (EXTRA_CELLS), no reference, a blank address, the same reference twice in
one file, or a reference that was stored earlier with different content. Nothing is dropped without a row saying why.

`read_feed` is the one reader of a feed file. `reconcile` uses it too, so the loader and the check can never disagree about what a blank line, a short row or a repeated
reference is.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .db import require_agency


@dataclass(frozen=True)
class SourceSpec:
    """Which columns of a feed hold what. A value of None means the feed does not have that column."""
    file: str
    ref: str
    address: str
    suburb: str | None = None
    state: str | None = None
    postcode: str | None = None
    lat: str | None = None
    lon: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None

    def required(self) -> list[str]:
        return [c for c in (self.ref, self.address, self.suburb, self.state, self.postcode, self.lat, self.lon, self.name, self.email, self.phone) if c]


SOURCES = {
    "crm": SourceSpec("crm_export.csv", ref="contact_ref", address="address", suburb="suburb", state="state", postcode="postcode", name="full_name", email="email", phone="phone"),
    "listing": SourceSpec("listing_feed.csv", ref="listing_ref", address="address", suburb="suburb", state="state", postcode="postcode", lat="latitude", lon="longitude"),
    "sales": SourceSpec("sales_history.csv", ref="sale_ref", address="address_full", name="buyer_name", email="buyer_email", phone="buyer_phone"),
}


class FeedRefused(ValueError):
    """The file cannot be read as this feed (a mapped column is missing, or it is not text)."""


@dataclass
class IngestResult:
    source: str
    file: str
    status: str                 # loaded (stored a row) | stored_nothing (a new file whose rows were all rejected or unchanged) | skipped (same bytes loaded before)
    row_count: int = 0
    stored: int = 0
    rejected: int = 0
    unchanged: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)          # reason code -> rows of this file


def row_sha(raw: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


EXTRA_KEY = "(extra cells)"          # where a rejected row keeps the cells that did not fit the header


@dataclass(frozen=True)
class FeedRow:
    line_no: int                         # the physical line the row ends on (the line it starts on, unless a quoted cell spans lines)
    raw: dict[str, str]                  # header name -> cell as received; a cell the row is too short to have is ""
    extra: tuple[str, ...] = ()          # cells beyond the header that hold something (blank ones are not data and are dropped)


def read_feed(path: Path, source: str) -> tuple[str, list[FeedRow]]:
    """(sha256 of the file, its rows). A blank line is not a row. Raises FeedRefused when the file is not UTF-8 text or the header lacks a mapped column."""
    data = Path(path).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FeedRefused(f"{Path(path).name}: not UTF-8 text ({exc.reason})") from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, [])
    missing = [c for c in SOURCES[source].required() if c not in header]
    if missing:
        raise FeedRefused(f"{Path(path).name}: the {source} feed needs column(s) {', '.join(missing)}")
    rows = []
    for cells in reader:
        if not cells:
            continue
        raw = {name: (cells[i] if i < len(cells) else "") for i, name in enumerate(header)}
        rows.append(FeedRow(reader.line_num, raw, tuple(c for c in cells[len(header):] if c.strip())))
    return sha, rows


def ingest_file(cur: psycopg.Cursor, agency: str, source: str, path: Path, *, now: datetime | None = None, new_agency: bool = False) -> IngestResult:
    """Store one feed file for one agency. Must run inside agency_tx for that agency. An agency that has never been loaded is refused (UnknownAgency) unless `new_agency` says it is meant."""
    spec = SOURCES[source]
    if not new_agency:
        require_agency(cur, agency, can_create=True)
    sha, rows = read_feed(path, source)
    cur.execute("INSERT INTO agency (agency_id) VALUES (%s) ON CONFLICT DO NOTHING", (agency,))
    if cur.execute("SELECT 1 FROM source_load WHERE agency_id = %s AND source = %s AND file_sha256 = %s", (agency, source, sha)).fetchone():
        return IngestResult(source, Path(path).name, "skipped")
    received_at = now or datetime.now(timezone.utc)
    load_id = cur.execute("""INSERT INTO source_load (agency_id, source, file_name, file_sha256, row_count, stored, rejected, unchanged, received_at) VALUES (%s, %s, %s, %s, 0, 0, 0, 0, %s)
                             RETURNING load_id""", (agency, source, Path(path).name, sha, received_at)).fetchone()[0]
    existing = {r[0]: r[1] for r in cur.execute("SELECT source_ref, raw_sha256 FROM source_record WHERE agency_id = %s AND source = %s", (agency, source)).fetchall()}
    seen: set[str] = set()
    stored = rejected = unchanged = 0
    reasons: dict[str, int] = {}
    for line_no, raw, extra in ((r.line_no, r.raw, r.extra) for r in rows):
        ref = raw.get(spec.ref, "").strip()
        reason = None
        if extra:
            reason = ("EXTRA_CELLS", f"the row has {len(extra)} cell(s) beyond the {len(raw)} columns of the header; the row is not stored")
            raw = {**raw, EXTRA_KEY: list(extra)}
        elif not ref:
            reason = ("MISSING_REF", f"the {spec.ref} column is empty")
        elif not raw.get(spec.address, "").strip():
            reason = ("BLANK_ADDRESS", f"the {spec.address} column is empty")
        elif ref in seen:
            reason = ("DUPLICATE_REF", f"{ref} appears more than once in this file; the first row is kept")
        elif ref in existing:
            if existing[ref] == row_sha(raw):
                unchanged += 1
                seen.add(ref)
                continue
            reason = ("CHANGED_AFTER_LOAD", f"{ref} was loaded earlier with different content; the stored row is not replaced")
        if reason:
            cur.execute("""INSERT INTO ingest_reject (agency_id, source, file_sha256, line_no, reason_code, reason, raw) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (agency, source, sha, line_no, reason[0], reason[1], Jsonb(raw)))
            rejected += 1
            reasons[reason[0]] = reasons.get(reason[0], 0) + 1
            continue
        seen.add(ref)
        cur.execute("""INSERT INTO source_record (agency_id, record_id, source, source_ref, load_id, line_no, raw, raw_sha256, loaded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (agency, f"{source}:{ref}", source, ref, load_id, line_no, Jsonb(raw), row_sha(raw), received_at))
        stored += 1
    cur.execute("UPDATE source_load SET row_count = %s, stored = %s, rejected = %s, unchanged = %s, loaded_at = CASE WHEN %s > 0 THEN %s END WHERE agency_id = %s AND load_id = %s",
                (len(rows), stored, rejected, unchanged, stored, received_at, agency, load_id))
    return IngestResult(source, Path(path).name, "loaded" if stored else "stored_nothing", len(rows), stored, rejected, unchanged, reasons)
