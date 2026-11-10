"""Blocking: which G-NAF addresses could a record possibly be?

Comparing every record with every address does not scale. A record is only compared with addresses in its block:
  A  same postcode and same street key (street name and type)
  B  same locality and state, and same street key
  C  same postcode or same locality, and a street key that pg_trgm finds similar (the typo case)
The union of the three is the candidate list. Nothing here decides anything; `parcelkey.match` does.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from .normalise import Addr


@dataclass(frozen=True)
class Candidate:
    pid: str
    addr: Addr
    sim: float                      # trigram similarity of the street keys, 1.0 when they are equal
    same_postcode: bool
    dist_m: float | None            # metres between the record's coordinates and the address geocode, when both exist


_SQL = """
WITH r AS (
    SELECT record_id, street_key, locality_key, state, postcode, geog
    FROM source_record WHERE agency_id = %(agency)s AND record_id = ANY(%(ids)s) AND norm_status = 'ok'
)
SELECT r.record_id, g.address_detail_pid, g.unit, g.level, g.building, g.lot, g.number_first, g.number_last, g.street_name, g.street_type, g.locality_key, g.state, g.postcode,
       similarity(g.street_key, r.street_key) AS sim, (r.postcode <> '' AND g.postcode = r.postcode) AS same_postcode,
       CASE WHEN r.geog IS NOT NULL AND g.geog IS NOT NULL THEN ST_Distance(g.geog, r.geog) END AS dist_m
FROM r
JOIN LATERAL (
    SELECT * FROM gnaf_address a WHERE r.postcode <> '' AND a.postcode = r.postcode AND a.street_key = r.street_key
    UNION ALL
    SELECT * FROM gnaf_address a WHERE r.locality_key <> '' AND a.locality_key = r.locality_key AND (r.state = '' OR a.state = r.state) AND a.street_key = r.street_key
    UNION ALL
    SELECT * FROM gnaf_address a WHERE ((r.postcode <> '' AND a.postcode = r.postcode) OR (r.locality_key <> '' AND a.locality_key = r.locality_key AND (r.state = '' OR a.state = r.state)))
                                    AND a.street_key %% r.street_key
) g ON true
"""


def candidates(cur: psycopg.Cursor, agency: str, record_ids: list[str], floor: float) -> dict[str, list[Candidate]]:
    """Candidate addresses for each record id. Runs inside agency_tx. A record with no candidate has an empty list."""
    out: dict[str, list[Candidate]] = {rid: [] for rid in record_ids}
    cur.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(floor),))
    seen: set[tuple[str, str]] = set()
    for row in cur.execute(_SQL, {"agency": agency, "ids": record_ids}).fetchall():
        rid, pid = row[0], row[1]
        if (rid, pid) in seen:
            continue
        seen.add((rid, pid))
        addr = Addr(unit=row[2], level=row[3], building=row[4], lot=row[5], number_first=row[6], number_last=row[7], street_name=row[8], street_type=row[9],
                    locality=row[10], state=row[11], postcode=row[12])
        out[rid].append(Candidate(pid, addr, float(row[13]), bool(row[14]), None if row[15] is None else float(row[15])))
    for lst in out.values():
        lst.sort(key=lambda c: c.pid)
    return out
