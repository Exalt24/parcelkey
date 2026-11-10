"""The normalise stage on stored records: fill the normalised columns of every record that has not been normalised yet, with the rules that fired.

The raw row is never touched (the app role has no UPDATE on it). A record whose address has no street, or neither a street number nor a lot, is marked
`unparseable`; the match stage sets it aside with that reason instead of guessing.
"""
from __future__ import annotations

import psycopg

from .ingest import SOURCES
from .normalise import normalise_address, normalise_email, normalise_name, normalise_phone

LAT_RANGE = (-44.0, -9.0)          # Australia's mainland and Tasmania sit inside this latitude band
LON_RANGE = (112.0, 154.0)


def parse_coord(raw: str, lo: float, hi: float) -> float | None:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def normalise_record(source: str, raw: dict[str, str]) -> dict:
    """Pure: the normalised columns for one raw row of a source."""
    spec = SOURCES[source]
    get = lambda col: (raw.get(col) or "") if col else ""
    if any((spec.suburb, spec.state, spec.postcode)):
        addr = normalise_address(get(spec.address), get(spec.suburb), get(spec.state), get(spec.postcode))
    else:
        addr = normalise_address(get(spec.address))
    rules = list(addr.rules)
    lat = lon = None
    if spec.lat and spec.lon and (raw.get(spec.lat) or raw.get(spec.lon)):
        lat, lon = parse_coord(get(spec.lat), *LAT_RANGE), parse_coord(get(spec.lon), *LON_RANGE)
        if lat is None or lon is None:
            lat = lon = None
            rules.append("COORDS_OUT_OF_RANGE")
    email, email_rules = normalise_email(get(spec.email))
    phone, phone_rules = normalise_phone(get(spec.phone))
    usable = bool(addr.street_name) and bool(addr.number_first or addr.lot)
    return {
        "status": "ok" if usable else "unparseable", "rules": rules, "addr": addr, "lat": lat, "lon": lon,
        "name": normalise_name(get(spec.name)), "email": email, "phone": phone, "contact_rules": [*email_rules, *phone_rules],
    }


def normalise_pending(cur: psycopg.Cursor, agency: str) -> int:
    """Normalise every pending record of the agency. Returns how many. Run inside agency_tx."""
    rows = cur.execute("SELECT record_id, source, raw FROM source_record WHERE agency_id = %s AND norm_status = 'pending' ORDER BY record_id", (agency,)).fetchall()
    for record_id, source, raw in rows:
        n = normalise_record(source, raw)
        a = n["addr"]
        cur.execute(
            """UPDATE source_record SET norm_status = %s, norm_rules = %s, unit = %s, level = %s, building = %s, lot = %s, number_first = %s, number_last = %s, street_name = %s,
                      street_type = %s, street_key = %s, locality_key = %s, state = %s, postcode = %s, canon_key = %s,
                      geog = CASE WHEN %s::float8 IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(%s::float8, %s::float8), 4326)::geography END,
                      contact_name = %s, contact_email = %s, contact_phone = %s, contact_rules = %s
               WHERE agency_id = %s AND record_id = %s""",
            (n["status"], n["rules"], a.unit, a.level, a.building, a.lot, a.number_first, a.number_last, a.street_name, a.street_type, a.street_key, a.locality, a.state, a.postcode,
             a.canon_key, n["lon"], n["lon"], n["lat"], n["name"], n["email"], n["phone"], n["contact_rules"], agency, record_id),
        )
    return len(rows)
