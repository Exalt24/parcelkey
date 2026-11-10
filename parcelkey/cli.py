"""Command line. Exit codes: 0 ok, 1 a check failed (reconcile findings, a stale source), 2 bad input or a refused file."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from . import db, evaluate as ev, freshness, gnaf, quality
from .contacts import DEFAULT_MAX_CLUSTER
from .ingest import FeedRefused
from .match import MatchConfig
from .pipeline import run_agency
from .reconcile import reconcile


def _cfg(a: argparse.Namespace) -> MatchConfig:
    d = MatchConfig()
    return MatchConfig(threshold=a.threshold if a.threshold is not None else d.threshold, lot_distance_m=a.lot_distance if a.lot_distance is not None else d.lot_distance_m)


def cmd_init_db(a: argparse.Namespace) -> int:
    db.init_db(db.default_admin_dsn())
    print("database ready")
    return 0


def cmd_load_gnaf(a: argparse.Namespace) -> int:
    with psycopg.connect(db.default_admin_dsn()) as conn:
        conn.execute(f"SET search_path = {db.SCHEMA}, public")
        report = gnaf.load_gnaf(conn, Path(a.directory))
        print(f"loaded {report.files} files, {report.rows_read} rows read, {report.rows_inserted} new rows")
        for name, cols in sorted(report.ignored_columns.items()):
            print(f"  {name}: columns not modelled here, skipped: {', '.join(cols)}")
        if report.skipped_files:
            print(f"  files for tables not modelled here, skipped: {len(report.skipped_files)} (for example {report.skipped_files[0]})")
        if not a.no_build:
            built = gnaf.build_reference(conn)
            print(f"reference table built: {built['addresses']} addresses ({built['skipped_no_street']} without a street left out)")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    with db.connect() as conn:
        r = run_agency(conn, a.agency, Path(a.feeds), _cfg(a), max_cluster=a.max_cluster)
    for i in r.ingested:
        print(f"ingest     {i.source:8} {i.file}: {i.status}" + (f", {i.row_count} rows, stored {i.stored}, rejected {i.rejected}, unchanged {i.unchanged}" if i.status == "loaded" else ""))
    m = r.match
    print(f"normalise  {r.normalised} records")
    print(f"match      {m.records} decided: linked {sum(m.linked.values())} ({', '.join(f'{k} {v}' for k, v in sorted(m.linked.items())) or 'none'}), "
          f"quarantined {sum(m.quarantined.values())} ({', '.join(f'{k} {v}' for k, v in sorted(m.quarantined.items())) or 'none'}), properties added {m.new_properties}")
    c = r.contacts
    print(f"contacts   {c.records} mentions: new {c.new}, linked {sum(c.linked.values())} ({', '.join(f'{k} {v}' for k, v in sorted(c.linked.items())) or 'none'}), merged {c.merged}, set aside {c.quarantined}")
    print("timings    " + "  ".join(f"{k} {v:.2f} s" for k, v in r.seconds.items()))
    return 0


def cmd_reconcile(a: argparse.Namespace) -> int:
    with db.connect() as conn, db.agency_tx(conn, a.agency) as cur:
        res = reconcile(cur, a.agency, Path(a.feeds) if a.feeds else None)
    for line in res.lines:
        print(line)
    for f in res.findings:
        print(f"FAIL {f}")
    print("RECONCILED: every record is accounted for and every count adds up" if res.ok else f"NOT RECONCILED: {len(res.findings)} finding(s)")
    return 0 if res.ok else 1


def cmd_freshness(a: argparse.Namespace) -> int:
    try:
        max_age = freshness.parse_max_age(a.max_age or [])
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    now = datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else None
    code = 0
    with db.connect() as conn:
        for agency in a.agency:
            with db.agency_tx(conn, agency) as cur:
                rows = freshness.check(cur, agency, max_age, now)
            print(f"freshness for {agency}")
            for row in rows:
                print("  " + row.line())
            code = max(code, freshness.exit_code(rows))
    print("all sources fresh" if code == 0 else "STALE: at least one source is older than its maximum age or missing")
    return code


def cmd_quality(a: argparse.Namespace) -> int:
    with db.connect() as conn, db.agency_tx(conn, a.agency) as cur:
        rep = quality.quality_report(cur, a.agency)
    print(json.dumps(rep, indent=1, sort_keys=True) if a.json else quality.render(rep))
    return 0


def cmd_evaluate(a: argparse.Namespace) -> int:
    key = ev.load_key(Path(a.key), a.agency)
    truth = ev.load_truth(Path(a.truth))
    with db.connect() as conn, db.agency_tx(conn, a.agency) as cur:
        res = ev.evaluate(cur, a.agency, key, truth)
    print(json.dumps(res, indent=1, sort_keys=True) if a.json else ev.render(res))
    return 0


def cmd_quarantine(a: argparse.Namespace) -> int:
    with db.connect() as conn, db.agency_tx(conn, a.agency) as cur:
        rows = cur.execute("""SELECT q.record_id, q.reason_code, q.status, r.raw ->> CASE r.source WHEN 'sales' THEN 'address_full' ELSE 'address' END, q.detail
                              FROM quarantine q JOIN source_record r ON r.agency_id = q.agency_id AND r.record_id = q.record_id
                              WHERE q.agency_id = %s AND (%s::text IS NULL OR q.reason_code = %s) ORDER BY q.reason_code, q.record_id LIMIT %s""",
                           (a.agency, a.reason, a.reason, a.limit)).fetchall()
    for rid, reason, status, address, detail in rows:
        print(f"{rid:14} {reason:18} {status:8} {address!r} {json.dumps(detail, sort_keys=True)[:140]}")
    print(f"{len(rows)} shown")
    return 0


def cmd_explain(a: argparse.Namespace) -> int:
    with db.connect() as conn, db.agency_tx(conn, a.agency) as cur:
        row = cur.execute("""SELECT raw, norm_status, norm_rules, unit, level, building, lot, number_first, number_last, street_name, street_type, locality_key, state, postcode, contact_email, contact_phone
                             FROM source_record WHERE agency_id = %s AND record_id = %s""", (a.agency, a.record_id)).fetchone()
        if row is None:
            print(f"no record {a.record_id} for {a.agency}", file=sys.stderr)
            return 2
        print(f"raw        {json.dumps(row[0], sort_keys=True)}")
        print(f"normalised {row[1]}: unit {row[3]!r} level {row[4]!r} building {row[5]!r} lot {row[6]!r} number {row[7]!r}-{row[8]!r} street {row[9]!r} {row[10]!r} locality {row[11]!r} {row[12]} {row[13]!r}")
        print(f"  rules    {', '.join(row[2]) or 'none'}")
        link = cur.execute("""SELECT l.property_id, l.rule, l.rules, l.score, l.decided_at, p.display_address, p.origin FROM property_link l JOIN property p ON p.property_id = l.property_id
                              WHERE l.agency_id = %s AND l.record_id = %s""", (a.agency, a.record_id)).fetchone()
        if link:
            print(f"linked     {link[0]} ({link[6]}) {link[5]!r} by {link[1]} score {link[3]} at {link[4]:%Y-%m-%d %H:%M:%S}; rules {', '.join(link[2])}")
        q = cur.execute("SELECT reason_code, status, detail FROM quarantine WHERE agency_id = %s AND record_id = %s", (a.agency, a.record_id)).fetchone()
        if q:
            print(f"quarantine {q[0]} ({q[1]}) {json.dumps(q[2], sort_keys=True)}")
        c = cur.execute("SELECT contact_id, rule, evidence FROM contact_link WHERE agency_id = %s AND record_id = %s", (a.agency, a.record_id)).fetchone()
        if c:
            print(f"contact    {c[0]} by {c[1]} {json.dumps(c[2], sort_keys=True)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parcelkey", description="Resolve messy property addresses and contacts into one master property id, with every decision on record.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create the roles, extensions and tables (administrator connection)").set_defaults(fn=cmd_init_db)
    p = sub.add_parser("load-gnaf", help="load G-NAF layout files and build the flat reference table (administrator connection)")
    p.add_argument("directory")
    p.add_argument("--no-build", action="store_true", help="load the tables only")
    p.set_defaults(fn=cmd_load_gnaf)
    p = sub.add_parser("run", help="ingest, normalise, match and resolve contacts for one agency")
    p.add_argument("--agency", required=True)
    p.add_argument("--feeds", required=True, help="folder with crm_export.csv, listing_feed.csv and sales_history.csv")
    p.add_argument("--threshold", type=float, help="minimum street name similarity for a near miss link (default 0.60)")
    p.add_argument("--lot-distance", type=float, help="metres a lot only record may be from a numbered address and still link (default 50)")
    p.add_argument("--max-cluster", type=int, default=DEFAULT_MAX_CLUSTER, help="records one contact may hold before a mention is set aside")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("reconcile", help="prove the counts add up (exit 1 when they do not)")
    p.add_argument("--agency", required=True)
    p.add_argument("--feeds", help="also re-read the feed files and compare them with what was stored")
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("freshness", help="check each source's last load against its maximum age (exit 1 when stale)")
    p.add_argument("--agency", required=True, action="append")
    p.add_argument("--max-age", action="append", metavar="SOURCE=HOURS")
    p.add_argument("--now", help="ISO time to measure from (default now)")
    p.set_defaults(fn=cmd_freshness)
    p = sub.add_parser("quality", help="data quality report")
    p.add_argument("--agency", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_quality)
    p = sub.add_parser("evaluate", help="precision and recall against the hidden answer key")
    p.add_argument("--agency", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--truth", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_evaluate)
    p = sub.add_parser("quarantine", help="list quarantined records with their reasons")
    p.add_argument("--agency", required=True)
    p.add_argument("--reason")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_quarantine)
    p = sub.add_parser("explain", help="show one record, how it was normalised and what was decided")
    p.add_argument("--agency", required=True)
    p.add_argument("record_id")
    p.set_defaults(fn=cmd_explain)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (FeedRefused, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
