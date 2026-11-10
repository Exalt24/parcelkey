"""Measure the links against the hidden answer key that scripts/seed.py writes. Nothing else in the package reads the key.

Per source record the outcome is one of:
  resolved     linked to the property the key names
  quarantined  set aside, and the key says setting it aside is the right outcome (a lot with no geocode, a transposed street number, a unit that is not there)
  missed       set aside, and the key names a property it could have been linked to
  wrong        linked to a property the key does not name

precision = resolved / (resolved + wrong), over every link made.
recall    = resolved / records whose key outcome is a link.
A record the key says to set aside, that was linked anyway, counts as wrong and lowers precision.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import psycopg

OUTCOMES = ("resolved", "quarantined", "missed", "wrong")


def load_key(path: Path, agency: str) -> dict[str, dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return {r["record_id"]: r for r in csv.DictReader(f) if r["agency_id"] == agency}


def load_truth(path: Path) -> dict[str, dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return {r["pid"]: r for r in csv.DictReader(f) if r["pid"]}


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def evaluate(cur: psycopg.Cursor, agency: str, key: dict[str, dict[str, str]], truth: dict[str, dict[str, str]]) -> dict:
    """The measurement as a plain dict. Runs inside agency_tx."""
    links = {r[0]: r[1:] for r in cur.execute("""SELECT l.record_id, p.origin, p.gnaf_pid, p.canon_key FROM property_link l JOIN property p ON p.property_id = l.property_id
                                                 WHERE l.agency_id = %s""", (agency,)).fetchall()}
    quarantined = {r[0] for r in cur.execute("SELECT record_id FROM quarantine WHERE agency_id = %s AND status = 'open'", (agency,)).fetchall()}
    by_class: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(OUTCOMES, 0) | {"unprocessed": 0})
    unit_merges = 0
    expected_link = resolved_on_link = 0
    for rid, k in key.items():
        outcome = "unprocessed"
        if rid in links:
            origin, gnaf_pid, canon = links[rid]
            ok = (bool(k["truth_pid"]) and origin == "gnaf" and gnaf_pid == k["truth_pid"]) or (bool(k["truth_canon_key"]) and origin == "derived" and canon == k["truth_canon_key"])
            outcome = "resolved" if ok else "wrong"
            if not ok and origin == "gnaf" and k["building_id"]:
                other = truth.get(gnaf_pid or "")
                if other and other["building_id"] == k["building_id"] and other["unit"] != k["unit"]:
                    unit_merges += 1
        elif rid in quarantined:
            outcome = "quarantined" if k["expected"] == "quarantine" else "missed"
        by_class[k["class"]][outcome] += 1
        if k["expected"] == "link":
            expected_link += 1
            resolved_on_link += outcome == "resolved"
    totals = {o: sum(c[o] for c in by_class.values()) for o in (*OUTCOMES, "unprocessed")}
    made = totals["resolved"] + totals["wrong"]
    out = {
        "agency": agency, "records": len(key), "totals": totals, "links_made": made,
        "precision": totals["resolved"] / made if made else None,
        "recall": resolved_on_link / expected_link if expected_link else None,
        "records_expected_to_link": expected_link, "different_unit_merges": unit_merges,
        "by_class": {c: dict(v) for c, v in sorted(by_class.items())},
    }
    out["contacts"] = _evaluate_contacts(cur, agency, key)
    return out


def _evaluate_contacts(cur: psycopg.Cursor, agency: str, key: dict[str, dict[str, str]]) -> dict:
    cl = {r[0]: r[1] for r in cur.execute("SELECT record_id, contact_id FROM contact_link WHERE agency_id = %s", (agency,)).fetchall()}
    cq = {r[0] for r in cur.execute("SELECT record_id FROM contact_quarantine WHERE agency_id = %s", (agency,)).fetchall()}
    mentions = {rid: k for rid, k in key.items() if k["person_id"] and (rid in cl or rid in cq)}
    pred: dict[str, set[str]] = defaultdict(set)
    truth: dict[str, set[str]] = defaultdict(set)
    for rid, k in mentions.items():
        if rid in cl:
            pred[cl[rid]].add(rid)
        truth[k["person_id"]].add(rid)
    cell: dict[tuple[str, str], int] = defaultdict(int)
    for rid, k in mentions.items():
        if rid in cl:
            cell[(cl[rid], k["person_id"])] += 1
    tp = sum(_pairs(n) for n in cell.values())
    pred_pairs = sum(_pairs(len(v)) for v in pred.values())
    truth_pairs = sum(_pairs(len(v)) for v in truth.values())
    anchor = {person: min(rids) for person, rids in truth.items()}          # a person's first mention (by record id) is the one the others should join
    by_class: dict[str, dict[str, int]] = defaultdict(lambda: {"resolved": 0, "missed": 0, "wrong": 0, "quarantined": 0})
    for rid, k in mentions.items():
        if rid in cq:
            by_class[k["contact_class"]]["quarantined"] += 1
            continue
        cluster = pred[cl[rid]]
        if any(mentions[o]["person_id"] != k["person_id"] for o in cluster):
            outcome = "wrong"
        elif rid == anchor[k["person_id"]] or anchor[k["person_id"]] in cluster:
            outcome = "resolved"
        else:
            outcome = "missed"
        by_class[k["contact_class"]][outcome] += 1
    return {"mentions": len(mentions), "true_pairs": truth_pairs, "pairs_merged": pred_pairs, "pairs_correct": tp,
            "pair_precision": tp / pred_pairs if pred_pairs else None, "pair_recall": tp / truth_pairs if truth_pairs else None,
            "by_class": {c: dict(v) for c, v in sorted(by_class.items())}}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.2f}%"


def render(ev: dict) -> str:
    t = ev["totals"]
    lines = [f"measurement for {ev['agency']} against the answer key: {ev['records']} records",
             f"  links made {ev['links_made']}: resolved {t['resolved']}, wrong {t['wrong']}; quarantined as expected {t['quarantined']}, missed {t['missed']}, unprocessed {t['unprocessed']}",
             f"  precision {_pct(ev['precision'])} ({t['resolved']} of {ev['links_made']})   recall {_pct(ev['recall'])} (of {ev['records_expected_to_link']} records that should link)",
             f"  merges of two different units in one building: {ev['different_unit_merges']}",
             "", f"  {'defect class':24} {'records':>8} {'resolved':>9} {'quarantined':>12} {'missed':>7} {'wrong':>6}"]
    for cls, v in ev["by_class"].items():
        lines.append(f"  {cls:24} {sum(v[o] for o in OUTCOMES):8} {v['resolved']:9} {v['quarantined']:12} {v['missed']:7} {v['wrong']:6}")
    c = ev["contacts"]
    lines += ["", f"  contacts: {c['mentions']} mentions, pairwise precision {_pct(c['pair_precision'])} ({c['pairs_correct']} of {c['pairs_merged']} merged pairs), "
                  f"recall {_pct(c['pair_recall'])} ({c['pairs_correct']} of {c['true_pairs']} true pairs)",
              f"  {'contact class':28} {'mentions':>9} {'resolved':>9} {'missed':>7} {'wrong':>6} {'set aside':>10}"]
    for cls, v in c["by_class"].items():
        lines.append(f"  {cls:28} {sum(v.values()):9} {v['resolved']:9} {v['missed']:7} {v['wrong']:6} {v['quarantined']:10}")
    return "\n".join(lines)
