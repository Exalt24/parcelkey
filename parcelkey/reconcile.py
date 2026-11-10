"""Reconcile: prove the counts add up, from the database's own side and from the feed files.

Checks, each reported by name (exit code 1 when any fails):
  FILE_ROWS        every row of a feed file is stored, rejected or unchanged (the file is re-read here with its own small reader, not the loader's)
  RAW_STORED       each stored raw row equals the file row it came from, field by field
  ACCOUNTED        every source record is linked or in open quarantine, never both, never neither, and none is still pending
  NO_ORPHAN        no link points at a missing record or a missing property; no quarantine row at a missing record
  ONE_ID           no property has two ids and no two properties share a G-NAF pid or a derived key; each id is the one its derivation gives
  SUPERSEDED       a derived property this agency uses has the canonical key of a G-NAF address, because a later release added the address. It is listed and never rewritten:
                   a derived id stays derived, and a person decides what to merge
  LINK_FIELDS      every link has a rule and a score between 0 and 1; every open quarantine row has a known reason
  CONTACTS         every record that mentions a contact is linked or set aside, every link points at a surviving contact, every identifier belongs to one surviving contact
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .ingest import SOURCES
from .master import property_id_for_derived, property_id_for_gnaf

QUARANTINE_REASONS = {"UNPARSEABLE", "POSTAL_ADDRESS", "AMBIGUOUS_TIE", "BELOW_THRESHOLD", "UNIT_CONFLICT", "LOCALITY_CONFLICT", "NUMBER_TRANSPOSED", "LOT_NO_GEOCODE", "LOT_TOO_FAR", "NO_LOCALITY"}


@dataclass
class Finding:
    check: str
    detail: str

    def __str__(self) -> str:
        return f"{self.check}: {self.detail}"


@dataclass
class ReconcileResult:
    agency: str
    lines: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _read_file_rows(path: Path) -> list[dict[str, str]]:
    """The reconcile's own reader: plain csv.reader, no DictReader, no shared code with the loader."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr, [])
        return [dict(zip(header, row)) for row in rdr]


def reconcile(cur: psycopg.Cursor, agency: str, feeds_dir: Path | None = None) -> ReconcileResult:
    """Run every check for one agency. Runs inside agency_tx."""
    res = ReconcileResult(agency)

    def fail(check: str, detail: str) -> None:
        res.findings.append(Finding(check, detail))

    # FILE_ROWS and RAW_STORED
    if feeds_dir is not None:
        for source, spec in SOURCES.items():
            path = Path(feeds_dir) / spec.file
            if not path.is_file():
                continue
            rows = _read_file_rows(path)
            loads = cur.execute("SELECT load_id, row_count, stored, rejected, unchanged FROM source_load WHERE agency_id = %s AND source = %s AND file_name = %s ORDER BY load_id",
                                (agency, source, path.name)).fetchall()
            if not loads:
                fail("FILE_ROWS", f"{path.name}: {len(rows)} rows in the file and no load recorded")
                continue
            load_id, row_count, stored, rejected, unchanged = loads[-1]
            if len(rows) != row_count or row_count != stored + rejected + unchanged:
                fail("FILE_ROWS", f"{path.name}: file has {len(rows)} rows, load recorded {row_count}, stored {stored} + rejected {rejected} + unchanged {unchanged}")
            real_stored = cur.execute("SELECT count(*) FROM source_record WHERE agency_id = %s AND load_id = %s", (agency, load_id)).fetchone()[0]
            real_rejected = cur.execute("SELECT count(*) FROM ingest_reject r JOIN source_load l ON l.agency_id = r.agency_id AND l.file_sha256 = r.file_sha256 AND l.source = r.source "
                                        "WHERE r.agency_id = %s AND l.load_id = %s", (agency, load_id)).fetchone()[0]
            if real_stored != stored or real_rejected != rejected:
                fail("FILE_ROWS", f"{path.name}: the load says stored {stored} rejected {rejected}, the tables hold {real_stored} and {real_rejected}")
            by_ref = {r.get(spec.ref, "").strip(): r for r in rows}
            bad = 0
            for source_ref, raw in cur.execute("SELECT source_ref, raw FROM source_record WHERE agency_id = %s AND load_id = %s", (agency, load_id)).fetchall():
                file_row = by_ref.get(source_ref)
                if file_row is None or {k: (v or "") for k, v in file_row.items()} != raw:
                    bad += 1
            if bad:
                fail("RAW_STORED", f"{path.name}: {bad} stored rows differ from the file")
            res.lines.append(f"file       {path.name}: {len(rows)} rows = stored {stored} + rejected {rejected} + unchanged {unchanged}")

    total = cur.execute("SELECT count(*) FROM source_record WHERE agency_id = %s", (agency,)).fetchone()[0]
    linked = cur.execute("SELECT count(*) FROM property_link WHERE agency_id = %s", (agency,)).fetchone()[0]
    open_q = cur.execute("SELECT count(*) FROM quarantine WHERE agency_id = %s AND status = 'open'", (agency,)).fetchone()[0]
    pending = cur.execute("SELECT count(*) FROM source_record WHERE agency_id = %s AND norm_status = 'pending'", (agency,)).fetchone()[0]
    both = cur.execute("""SELECT count(*) FROM property_link l JOIN quarantine q ON q.agency_id = l.agency_id AND q.record_id = l.record_id AND q.status = 'open'
                          WHERE l.agency_id = %s""", (agency,)).fetchone()[0]
    neither = cur.execute("""SELECT count(*) FROM source_record r WHERE r.agency_id = %s
                             AND NOT EXISTS (SELECT 1 FROM property_link l WHERE l.agency_id = r.agency_id AND l.record_id = r.record_id)
                             AND NOT EXISTS (SELECT 1 FROM quarantine q WHERE q.agency_id = r.agency_id AND q.record_id = r.record_id AND q.status = 'open')""", (agency,)).fetchone()[0]
    if both:
        fail("ACCOUNTED", f"{both} records are linked and in open quarantine at once")
    if neither:
        fail("ACCOUNTED", f"{neither} records are neither linked nor in open quarantine")
    if pending:
        fail("ACCOUNTED", f"{pending} records have not been normalised")
    res.lines.append(f"records    {total} = linked {linked} + quarantined {open_q}")

    orphan_link_record = cur.execute("""SELECT count(*) FROM property_link l WHERE l.agency_id = %s
                                        AND NOT EXISTS (SELECT 1 FROM source_record r WHERE r.agency_id = l.agency_id AND r.record_id = l.record_id)""", (agency,)).fetchone()[0]
    orphan_link_property = cur.execute("""SELECT count(*) FROM property_link l WHERE l.agency_id = %s AND NOT EXISTS (SELECT 1 FROM property p WHERE p.property_id = l.property_id)""",
                                       (agency,)).fetchone()[0]
    orphan_q = cur.execute("""SELECT count(*) FROM quarantine q WHERE q.agency_id = %s
                              AND NOT EXISTS (SELECT 1 FROM source_record r WHERE r.agency_id = q.agency_id AND r.record_id = q.record_id)""", (agency,)).fetchone()[0]
    if orphan_link_record or orphan_link_property or orphan_q:
        fail("NO_ORPHAN", f"{orphan_link_record} links without a record, {orphan_link_property} links without a property, {orphan_q} quarantine rows without a record")
    res.lines.append(f"orphans    {orphan_link_record + orphan_link_property + orphan_q}")

    props = cur.execute("""SELECT property_id, origin, gnaf_pid, canon_key FROM property WHERE property_id IN (SELECT property_id FROM property_link WHERE agency_id = %s)""", (agency,)).fetchall()
    seen_pid: dict[str, str] = {}
    seen_key: dict[str, str] = {}
    wrong_ids = 0
    for property_id, origin, gnaf_pid, canon_key in props:
        expected = property_id_for_gnaf(gnaf_pid) if origin == "gnaf" else property_id_for_derived(canon_key)
        if expected != property_id:
            wrong_ids += 1
        if origin == "gnaf":
            if gnaf_pid in seen_pid and seen_pid[gnaf_pid] != property_id:
                fail("ONE_ID", f"G-NAF pid {gnaf_pid} has two ids: {seen_pid[gnaf_pid]} and {property_id}")
            seen_pid[gnaf_pid] = property_id
        else:
            if canon_key in seen_key and seen_key[canon_key] != property_id:
                fail("ONE_ID", f"derived key {canon_key!r} has two ids: {seen_key[canon_key]} and {property_id}")
            seen_key[canon_key] = property_id
    if wrong_ids:
        fail("ONE_ID", f"{wrong_ids} properties carry an id that is not the one its derivation gives")
    dup = cur.execute("""SELECT count(*) FROM (SELECT gnaf_pid FROM property WHERE gnaf_pid IS NOT NULL GROUP BY gnaf_pid HAVING count(*) > 1) d""").fetchone()[0]
    dup_keys = cur.execute("""SELECT count(*) FROM (SELECT canon_key FROM property WHERE origin = 'derived' GROUP BY canon_key HAVING count(*) > 1) d""").fetchone()[0]
    if dup or dup_keys:
        fail("ONE_ID", f"{dup} G-NAF pids and {dup_keys} derived keys appear on more than one property")
    res.lines.append(f"properties {len(props)} used by this agency, ids all match their derivation")

    superseded = cur.execute(
        """SELECT p.property_id, string_agg(DISTINCT coalesce(g.principal_pid, g.address_detail_pid), ', ') FROM property p JOIN gnaf_address g ON g.canon_key = p.canon_key
           WHERE p.origin = 'derived' AND p.property_id IN (SELECT property_id FROM property_link WHERE agency_id = %s) GROUP BY p.property_id ORDER BY p.property_id""", (agency,)).fetchall()
    if superseded:
        listed = "; ".join(f"{property_id} is now G-NAF {gnaf_pids}" for property_id, gnaf_pids in superseded[:5])
        fail("SUPERSEDED", f"{len(superseded)} derived properties now equal a G-NAF address ({listed}); nothing was rewritten, a person decides what to merge")
    res.lines.append(f"superseded {len(superseded)} derived properties now equal a G-NAF address")

    bad_links = cur.execute("""SELECT count(*) FROM property_link WHERE agency_id = %s AND (rule = '' OR score < 0 OR score > 1)""", (agency,)).fetchone()[0]
    bad_reasons = cur.execute("SELECT count(*) FROM quarantine WHERE agency_id = %s AND status = 'open' AND NOT (reason_code = ANY(%s))", (agency, sorted(QUARANTINE_REASONS))).fetchone()[0]
    if bad_links or bad_reasons:
        fail("LINK_FIELDS", f"{bad_links} links without a rule or a score in range, {bad_reasons} open quarantine rows with an unknown reason")

    mentions = cur.execute("""SELECT count(*) FROM source_record r WHERE r.agency_id = %s AND (r.contact_name <> '' OR r.contact_email <> '' OR r.contact_phone <> '')""", (agency,)).fetchone()[0]
    unresolved = cur.execute("""SELECT count(*) FROM source_record r WHERE r.agency_id = %s AND (r.contact_name <> '' OR r.contact_email <> '' OR r.contact_phone <> '')
                                AND NOT EXISTS (SELECT 1 FROM contact_link c WHERE c.agency_id = r.agency_id AND c.record_id = r.record_id)
                                AND NOT EXISTS (SELECT 1 FROM contact_quarantine q WHERE q.agency_id = r.agency_id AND q.record_id = r.record_id)""", (agency,)).fetchone()[0]
    to_merged = cur.execute("""SELECT count(*) FROM contact_link c JOIN contact k ON k.agency_id = c.agency_id AND k.contact_id = c.contact_id WHERE c.agency_id = %s AND k.merged_into IS NOT NULL""",
                            (agency,)).fetchone()[0]
    ident_merged = cur.execute("""SELECT count(*) FROM contact_identifier i JOIN contact k ON k.agency_id = i.agency_id AND k.contact_id = i.contact_id
                                  WHERE i.agency_id = %s AND k.merged_into IS NOT NULL""", (agency,)).fetchone()[0]
    if unresolved or to_merged or ident_merged:
        fail("CONTACTS", f"{unresolved} contact mentions unresolved, {to_merged} links and {ident_merged} identifiers point at a merged contact")
    res.lines.append(f"contacts   {mentions} mentions all linked or set aside")
    return res
