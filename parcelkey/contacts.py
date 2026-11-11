"""Contact identity resolution.

Two contact mentions are the same contact only when they share a normalised email or an E.164 MOBILE number, inside one agency. A landline is a shared office or household line:
it is kept in the evidence (`landline_not_used_to_merge`) and never merges anyone, alone or with anything else, so one reception number cannot join eight people. A name is never
evidence: two people called the same, with different emails and mobiles, stay two contacts, and two mentions of one person with no shared email or mobile stay two as well.

Each decision keeps its evidence: which identifier matched, and which earlier records already carried it. A mention that would join a cluster of more than
`max_cluster` records (a shared reception number, say) is set aside in `contact_quarantine` instead of merging a crowd.

A contact id comes from the record that created the contact and is never reassigned. When a record bridges two contacts, the one with the smaller id survives; the other is
kept with `merged_into` and every link and identifier moves to the survivor, with a `contact_merge` row recording why.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

DEFAULT_MAX_CLUSTER = 8
_MOBILE = re.compile(r"\+614\d{8}")
MOBILE_SQL = r"^\+614[0-9]{8}$"          # the same rule for SQL (the quality report counts mentions with it); a test holds the two together


def is_mobile(e164: str) -> bool:
    """True for an Australian mobile number in E.164 (`+614` and eight digits). Only a mobile identifies one person; a landline (`+612`, `+613`, `+617`, `+618`) is shared."""
    return bool(_MOBILE.fullmatch(e164))


@dataclass
class ContactCounts:
    new: int = 0
    linked: dict[str, int] = field(default_factory=dict)
    merged: int = 0
    quarantined: int = 0
    records: int = 0


def contact_id_for(agency: str, record_id: str) -> str:
    return "c_" + hashlib.sha256(f"contact|{agency}|{record_id}".encode("utf-8")).hexdigest()[:12]


def _cluster_size(cur: psycopg.Cursor, agency: str, contact_ids: list[str]) -> int:
    return cur.execute("SELECT count(*) FROM contact_link WHERE agency_id = %s AND contact_id = ANY(%s)", (agency, contact_ids)).fetchone()[0]


def resolve_pending(cur: psycopg.Cursor, agency: str, max_cluster: int = DEFAULT_MAX_CLUSTER) -> ContactCounts:
    """Resolve every record that mentions a contact and has not been resolved. Runs inside agency_tx."""
    counts = ContactCounts()
    rows = cur.execute(
        """SELECT r.record_id, r.contact_name, r.contact_email, r.contact_phone FROM source_record r
           WHERE r.agency_id = %s AND r.norm_status <> 'pending' AND (r.contact_name <> '' OR r.contact_email <> '' OR r.contact_phone <> '')
             AND NOT EXISTS (SELECT 1 FROM contact_link c WHERE c.agency_id = r.agency_id AND c.record_id = r.record_id)
             AND NOT EXISTS (SELECT 1 FROM contact_quarantine q WHERE q.agency_id = r.agency_id AND q.record_id = r.record_id)
           ORDER BY r.record_id""", (agency,)).fetchall()
    for record_id, name, email, phone in rows:
        counts.records += 1
        idents = [(k, v) for k, v in (("email", email), ("phone", phone if is_mobile(phone) else "")) if v]
        landline = {"landline_not_used_to_merge": phone} if phone and not is_mobile(phone) else {}
        found: dict[str, list[str]] = {}
        for kind, value in idents:
            hit = cur.execute("SELECT contact_id FROM contact_identifier WHERE agency_id = %s AND kind = %s AND value = %s", (agency, kind, value)).fetchone()
            if hit:
                found.setdefault(hit[0], []).append(kind)
        ids = sorted(found)
        if not ids:
            cid = contact_id_for(agency, record_id)
            cur.execute("INSERT INTO contact (agency_id, contact_id, display_name) VALUES (%s, %s, %s)", (agency, cid, name))
            rule = "NEW" if idents else "NO_IDENTIFIER"
            evidence = {"identifiers": dict(idents), **landline}
            counts.new += 1
        else:
            if _cluster_size(cur, agency, ids) + 1 > max_cluster:
                cur.execute("""INSERT INTO contact_quarantine (agency_id, record_id, reason_code, detail) VALUES (%s, %s, 'CLUSTER_TOO_LARGE', %s)""",
                            (agency, record_id, Jsonb({"contacts": ids, "max_cluster": max_cluster})))
                counts.quarantined += 1
                continue
            cid = ids[0]
            matched = sorted({k for kinds in found.values() for k in kinds})
            via = [r[0] for r in cur.execute("SELECT record_id FROM contact_link WHERE agency_id = %s AND contact_id = ANY(%s) ORDER BY record_id LIMIT 3", (agency, ids)).fetchall()]
            evidence = {"matched_on": matched, "identifiers": dict(idents), "earlier_records": via, **landline}
            if len(ids) > 1:
                rule = "BRIDGE"
                for loser in ids[1:]:
                    cur.execute("UPDATE contact_link SET contact_id = %s WHERE agency_id = %s AND contact_id = %s", (cid, agency, loser))
                    cur.execute("UPDATE contact_identifier SET contact_id = %s WHERE agency_id = %s AND contact_id = %s", (cid, agency, loser))
                    cur.execute("UPDATE contact SET merged_into = %s WHERE agency_id = %s AND contact_id = %s", (cid, agency, loser))
                    cur.execute("INSERT INTO contact_merge (agency_id, loser, survivor, evidence) VALUES (%s, %s, %s, %s)", (agency, loser, cid, Jsonb({**evidence, "bridged_by": record_id})))
                    counts.merged += 1
            else:
                rule = "+".join(m.upper() for m in matched)
        for kind, value in idents:
            cur.execute("INSERT INTO contact_identifier (agency_id, kind, value, contact_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (agency, kind, value, cid))
        cur.execute("INSERT INTO contact_link (agency_id, record_id, contact_id, rule, evidence) VALUES (%s, %s, %s, %s, %s)", (agency, record_id, cid, rule, Jsonb(evidence)))
        counts.linked[rule] = counts.linked.get(rule, 0) + 1
    return counts
