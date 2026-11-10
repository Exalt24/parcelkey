"""Run the whole pipeline on a generated world and print what it measured: counts, timings, precision, recall and the defect class table.

    python tools/measure.py --seed-dir out/seed                    one run with the default settings, both agencies
    python tools/measure.py --seed-dir out/seed --sweep 0.5,0.6,0.7,0.8   the same data once per street similarity threshold

The database is emptied before each run (agency tables, master properties and the G-NAF tables), then the generated G-NAF files are loaded again. Start PostgreSQL first with
`python tools/pg.py up`.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from parcelkey import db, evaluate as ev, gnaf, quality  # noqa: E402
from parcelkey.match import MatchConfig  # noqa: E402
from parcelkey.pipeline import run_agency  # noqa: E402
from parcelkey.reconcile import reconcile  # noqa: E402

AGENCIES = ("agency-a", "agency-b")


def run_once(seed_dir: Path, cfg: MatchConfig, *, verbose: bool) -> dict:
    admin = db.default_admin_dsn()
    db.init_db(admin)
    db.wipe(admin, reference=True)
    t0 = time.perf_counter()
    with psycopg.connect(admin) as conn:
        conn.execute(f"SET search_path = {db.SCHEMA}, public")
        loaded = gnaf.load_gnaf(conn, seed_dir / "gnaf")
        t1 = time.perf_counter()
        built = gnaf.build_reference(conn)
        t2 = time.perf_counter()
    out = {"gnaf_rows": loaded.rows_inserted, "gnaf_load_s": t1 - t0, "reference_rows": built["addresses"], "reference_build_s": t2 - t1, "agencies": {}}
    key_path, truth_path = seed_dir / "answer_key.csv", seed_dir / "truth_properties.csv"
    truth = ev.load_truth(truth_path)
    with db.connect() as conn:
        for agency in AGENCIES:
            report = run_agency(conn, agency, seed_dir / agency, cfg)
            with db.agency_tx(conn, agency) as cur:
                res = ev.evaluate(cur, agency, ev.load_key(key_path, agency), truth)
                rec = reconcile(cur, agency, seed_dir / agency)
                qual = quality.quality_report(cur, agency)
            out["agencies"][agency] = {"report": report, "evaluation": res, "reconcile": rec, "quality": qual}
            if verbose:
                print(ev.render(res))
                print(quality.render(qual))
                print("timings   " + "  ".join(f"{k} {v:.2f} s" for k, v in report.seconds.items()))
                print("reconcile " + ("ok" if rec.ok else "FAILED " + "; ".join(map(str, rec.findings))))
                print()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed-dir", default="out/seed")
    ap.add_argument("--sweep", help="comma separated street similarity thresholds to try")
    ap.add_argument("--threshold", type=float)
    a = ap.parse_args()
    seed_dir = Path(a.seed_dir)
    if a.sweep:
        print(f"{'threshold':>9} {'links':>6} {'wrong':>6} {'precision':>10} {'recall':>8} {'missed':>7} {'quarantined':>12}   (both agencies)")
        for t in (float(x) for x in a.sweep.split(",")):
            res = run_once(seed_dir, MatchConfig(threshold=t), verbose=False)
            tot = {k: sum(r["evaluation"]["totals"][k] for r in res["agencies"].values()) for k in ("resolved", "wrong", "missed", "quarantined")}
            made = tot["resolved"] + tot["wrong"]
            expected = sum(r["evaluation"]["records_expected_to_link"] for r in res["agencies"].values())
            resolved_link = sum(r["evaluation"]["recall"] * r["evaluation"]["records_expected_to_link"] for r in res["agencies"].values())
            print(f"{t:9.2f} {made:6} {tot['wrong']:6} {tot['resolved'] / made * 100:9.2f}% {resolved_link / expected * 100:7.2f}% {tot['missed']:7} {tot['quarantined']:12}")
        return 0
    cfg = MatchConfig(threshold=a.threshold) if a.threshold is not None else MatchConfig()
    res = run_once(seed_dir, cfg, verbose=True)
    print(f"gnaf load {res['gnaf_load_s']:.2f} s ({res['gnaf_rows']} rows), reference build {res['reference_build_s']:.2f} s ({res['reference_rows']} addresses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
