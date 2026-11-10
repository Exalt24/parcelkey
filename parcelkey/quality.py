"""Data quality report for one agency: what the feeds looked like, what normalisation had to repair, and what the match stage did with it."""
from __future__ import annotations

import psycopg


def quality_report(cur: psycopg.Cursor, agency: str) -> dict:
    """A plain dict of counts, safe to print as JSON. Runs inside agency_tx."""
    q = lambda sql, *params: cur.execute(sql, params).fetchall()
    sources = {}
    for source, stored, rejected, unchanged in q("SELECT source, sum(stored), sum(rejected), sum(unchanged) FROM source_load WHERE agency_id = %s GROUP BY source ORDER BY source", agency):
        sources[source] = {"stored": int(stored), "rejected": int(rejected), "unchanged": int(unchanged)}
    rejects = {r[0]: r[1] for r in q("SELECT reason_code, count(*) FROM ingest_reject WHERE agency_id = %s GROUP BY reason_code ORDER BY 1", agency)}
    n = q("SELECT count(*), count(*) FILTER (WHERE norm_status = 'ok'), count(*) FILTER (WHERE norm_status = 'unparseable'), count(*) FILTER (WHERE postcode = '' AND norm_status = 'ok'), "
          "count(*) FILTER (WHERE unit <> ''), count(*) FILTER (WHERE lot <> '' AND number_first = ''), count(*) FILTER (WHERE geog IS NOT NULL) FROM source_record WHERE agency_id = %s", agency)[0]
    rules = {r[0]: r[1] for r in q("SELECT rule, count(*) FROM source_record, unnest(norm_rules) AS rule WHERE agency_id = %s GROUP BY rule ORDER BY count(*) DESC, rule", agency)}
    linked = {r[0]: r[1] for r in q("SELECT rule, count(*) FROM property_link WHERE agency_id = %s GROUP BY rule ORDER BY 1", agency)}
    mismatch = q("SELECT count(*) FROM property_link WHERE agency_id = %s AND 'POSTCODE_MISMATCH' = ANY(rules)", agency)[0][0]
    quarantine = {f"{r[0]}/{r[1]}": r[2] for r in q("SELECT reason_code, status, count(*) FROM quarantine WHERE agency_id = %s GROUP BY 1, 2 ORDER BY 1, 2", agency)}
    properties = q("""SELECT p.origin, count(DISTINCT p.property_id) FROM property p JOIN property_link l ON l.property_id = p.property_id WHERE l.agency_id = %s GROUP BY p.origin ORDER BY 1""", agency)
    mentions = q("SELECT count(*) FILTER (WHERE contact_name <> '' OR contact_email <> '' OR contact_phone <> ''), count(*) FILTER (WHERE contact_email = '' AND contact_phone = '' AND contact_name <> ''), "
                 "count(*) FILTER (WHERE 'EMAIL_INVALID' = ANY(contact_rules)), count(*) FILTER (WHERE 'PHONE_INVALID' = ANY(contact_rules)), "
                 "count(*) FILTER (WHERE 'EMAIL_LOWERCASED' = ANY(contact_rules)) FROM source_record WHERE agency_id = %s", agency)[0]
    contacts = q("SELECT count(*) FILTER (WHERE merged_into IS NULL), count(*) FILTER (WHERE merged_into IS NOT NULL) FROM contact WHERE agency_id = %s", agency)[0]
    cq = q("SELECT count(*) FROM contact_quarantine WHERE agency_id = %s", agency)[0][0]
    return {
        "agency": agency,
        "sources": sources, "ingest_rejects": rejects,
        "records": {"total": n[0], "normalised_ok": n[1], "unparseable": n[2], "missing_postcode": n[3], "with_unit": n[4], "lot_only": n[5], "with_coordinates": n[6]},
        "normalisation_rules_fired": rules,
        "links_by_rule": linked, "postcode_mismatch_links": mismatch, "quarantine": quarantine,
        "properties_used": {o: c for o, c in properties},
        "contacts": {"mentions": mentions[0], "mentions_without_identifier": mentions[1], "invalid_emails": mentions[2], "invalid_phones": mentions[3], "emails_lowercased": mentions[4],
                     "contacts": contacts[0], "merged_away": contacts[1], "quarantined": cq},
    }


def render(report: dict) -> str:
    """The report as short plain text."""
    r = report["records"]
    lines = [f"data quality for {report['agency']}"]
    for source, v in report["sources"].items():
        lines.append(f"  feed {source:8} stored {v['stored']:5}  rejected {v['rejected']:3}  unchanged {v['unchanged']:3}")
    if report["ingest_rejects"]:
        lines.append("  rejects: " + ", ".join(f"{k} {v}" for k, v in report["ingest_rejects"].items()))
    lines.append(f"  records {r['total']}: normalised {r['normalised_ok']}, unparseable {r['unparseable']}, no postcode {r['missing_postcode']}, with a unit {r['with_unit']}, lot only {r['lot_only']}, with coordinates {r['with_coordinates']}")
    top = list(report["normalisation_rules_fired"].items())[:8]
    lines.append("  rules fired most: " + ", ".join(f"{k} {v}" for k, v in top))
    lines.append("  links by rule: " + ", ".join(f"{k} {v}" for k, v in report["links_by_rule"].items()) + f" (postcode did not agree on {report['postcode_mismatch_links']})")
    lines.append("  quarantine: " + (", ".join(f"{k} {v}" for k, v in report["quarantine"].items()) or "none"))
    lines.append("  properties used: " + ", ".join(f"{k} {v}" for k, v in report["properties_used"].items()))
    c = report["contacts"]
    lines.append(f"  contact mentions {c['mentions']} -> {c['contacts']} contacts ({c['merged_away']} merged away, {c['quarantined']} set aside); "
                 f"no identifier {c['mentions_without_identifier']}, bad email {c['invalid_emails']}, bad phone {c['invalid_phones']}")
    return "\n".join(lines)
