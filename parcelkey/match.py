"""The match stage: decide, for each record, a property, a new property, or quarantine. Every decision keeps the rule that made it.

`decide` is a pure function of one record's address and its candidate list, so the order records are processed in cannot change a result.

Order of the rules:
  1. EXACT_KEY      the same street, number, unit and locality: one candidate means link; two means AMBIGUOUS_TIE.
  2. TRIGRAM        the street name is a near miss (pg_trgm similarity at or above the threshold), the number and unit are equal, and one candidate is clearly best.
  3. LOT rules      a record with a lot and no street number: an address that is only a lot links by EXACT_LOT; a numbered address that carries the same lot links
                    only when the record has a geocode within `lot_distance_m` (LOT_GEOCODE, ST_Distance on geography); otherwise it is set aside.
  4. NEW_DERIVED    nothing in the reference is near: a property is made with an id derived from the canonical key.

Two hard rules sit above all of these and cannot be configured away:
  * a different unit or flat number never matches (`units_conflict`), even when the street and number are equal
  * a different street suffix never matches (`suffix_conflict`): Smith Street North and Smith Street South are two streets. A record with no suffix is the same street as either
  * a lot only record never merges with a numbered address unless a geocode is within distance

Quarantine reasons: UNPARSEABLE, POSTAL_ADDRESS, AMBIGUOUS_TIE, BELOW_THRESHOLD, UNIT_CONFLICT, LOCALITY_CONFLICT, NUMBER_TRANSPOSED, LOT_NO_GEOCODE, LOT_TOO_FAR.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from . import block
from .block import Candidate
from .master import ensure_derived_property, ensure_gnaf_property
from .normalise import Addr


BATCH = 400          # no-mutate: records decided per query, a memory and speed trade only


@dataclass(frozen=True)
class MatchConfig:
    threshold: float = 0.60        # a near miss street needs at least this similarity to link
    floor: float = 0.30            # below this a street is not a candidate at all
    tie_margin: float = 0.03       # two candidates this close are a tie, not a winner
    lot_distance_m: float = 50.0   # a lot only record links to a numbered address only within this many metres


@dataclass(frozen=True)
class Decision:
    outcome: str                   # link | new | quarantine
    rule: str
    score: float = 0.0
    pid: str = ""
    reason: str = ""               # quarantine reason code
    detail: dict = field(default_factory=dict)
    rules: tuple[str, ...] = ()    # extra rules that fired during the match


def _set_aside(reason: str, detail: dict) -> Decision:
    """A decision to quarantine: the rule and the reason are the same code."""
    return Decision("quarantine", reason, reason=reason, detail=detail)


def units_conflict(a: Addr, b: Addr) -> bool:
    """True when two addresses cannot be the same premises because of the unit. Different flat numbers in one building never merge; a level written on both sides and different also conflicts."""
    if a.unit != b.unit:
        return True
    return bool(a.level and b.level and a.level != b.level)


def suffix_conflict(a: Addr, b: Addr) -> bool:
    """True when both addresses name a street suffix and the two differ. A feed that leaves the suffix out is not in conflict: it may mean either street."""
    return bool(a.street_suffix and b.street_suffix and a.street_suffix != b.street_suffix)


def same_street(a: Addr, b: Addr) -> bool:
    """The same street: name and type equal, and the suffix equal when both write one. A feed that leaves the suffix out may mean either street, so it equals both."""
    return a.street_base == b.street_base and not suffix_conflict(a, b)


def locality_ok(a: Addr, b: Addr) -> bool:
    if not a.locality or not b.locality:
        return True
    return a.locality == b.locality and (not a.state or not b.state or a.state == b.state)


def numbers_equal(a: Addr, b: Addr) -> bool:
    if a.number_first != b.number_first:
        return False
    return not a.number_last or not b.number_last or a.number_last == b.number_last


def is_transposition(a: str, b: str) -> bool:
    """True when two street numbers differ by one swap of neighbouring digits (12 and 21, 1304 and 1034)."""
    if len(a) != len(b) or len(a) < 2 or not a.isdigit() or not b.isdigit() or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _cand_detail(cands: list[Candidate], limit: int = 5) -> list[dict]:
    return [{"pid": c.pid, "sim": round(c.sim, 4), "unit": c.addr.unit, "number": c.addr.number_key, "street": c.addr.street_key}
            for c in sorted(cands, key=lambda c: (-c.sim, c.pid))[:limit]]


def decide(rec: Addr, lat: float | None, lon: float | None, cands: list[Candidate], cfg: MatchConfig = MatchConfig()) -> Decision:
    """Decide one record. `lat` and `lon` only matter for the lot rule (the distances are already in the candidates)."""
    if not rec.street_name or not (rec.number_first or rec.lot):
        return _set_aside("UNPARSEABLE", {"why": "no street, or neither a street number nor a lot"})
    pool = [c for c in cands if c.sim >= cfg.floor]
    if rec.lot and not rec.number_first:
        return _decide_lot(rec, lat, lon, pool, cfg)
    same_number = [c for c in pool if locality_ok(rec, c.addr) and numbers_equal(rec, c.addr) and not suffix_conflict(rec, c.addr)]
    compatible = [c for c in same_number if not units_conflict(rec, c.addr)]
    if compatible:
        exact = [c for c in compatible if same_street(rec, c.addr)]
        if len(exact) == 1:
            extra = ("POSTCODE_MISMATCH",) if rec.postcode and exact[0].addr.postcode != rec.postcode else ()
            return Decision("link", "EXACT_KEY", 1.0, exact[0].pid, rules=extra)
        if len(exact) > 1:
            return _set_aside("AMBIGUOUS_TIE", {"candidates": _cand_detail(exact)})
        ranked = sorted(compatible, key=lambda c: (-c.sim, c.pid))
        top = ranked[0]
        if top.sim >= cfg.threshold:
            if rec.street_name[0].isdigit():          # a house number read into the street name (`12 SMITH`) scores 0.8 against `SMITH`: such a record is never linked on a near miss
                return _set_aside("BELOW_THRESHOLD", {"best": _cand_detail([top]), "threshold": cfg.threshold, "why": "the street name starts with a digit"})
            tied = [c for c in ranked if round(top.sim - c.sim, 4) <= cfg.tie_margin]          # no-mutate: the rounding only removes float noise, any number of places from 4 to 12 does that
            if len(tied) > 1:
                return _set_aside("AMBIGUOUS_TIE", {"candidates": _cand_detail(tied)})
            return Decision("link", "TRIGRAM", round(top.sim, 4), top.pid)
        return _set_aside("BELOW_THRESHOLD", {"best": _cand_detail([top]), "threshold": cfg.threshold})
    if same_number:
        return _set_aside("UNIT_CONFLICT", {"record_unit": rec.unit, "candidate_units": sorted({c.addr.unit for c in same_number})})
    other_locality = [c for c in pool if c.same_postcode and same_street(rec, c.addr) and numbers_equal(rec, c.addr) and not units_conflict(rec, c.addr)]
    if other_locality:
        return _set_aside("LOCALITY_CONFLICT", {"candidates": _cand_detail(other_locality)})
    swapped = [c for c in pool if locality_ok(rec, c.addr) and same_street(rec, c.addr) and is_transposition(rec.number_first, c.addr.number_first) and not units_conflict(rec, c.addr)]
    if swapped:
        return _set_aside("NUMBER_TRANSPOSED", {"candidates": _cand_detail(swapped)})
    return Decision("new", "NEW_DERIVED", 1.0)


def _decide_lot(rec: Addr, lat: float | None, lon: float | None, pool: list[Candidate], cfg: MatchConfig) -> Decision:
    same_lot = [c for c in pool if c.addr.lot == rec.lot and locality_ok(rec, c.addr) and same_street(rec, c.addr)]
    lot_only = [c for c in same_lot if not c.addr.number_first and not c.addr.unit]
    numbered = [c for c in same_lot if c.addr.number_first]
    if lot_only:
        if len(lot_only) == 1:
            return Decision("link", "EXACT_LOT", 1.0, lot_only[0].pid)
        return _set_aside("AMBIGUOUS_TIE", {"candidates": _cand_detail(lot_only)})
    if numbered:
        if lat is None or lon is None:
            return _set_aside("LOT_NO_GEOCODE", {"candidates": _cand_detail(numbered)})
        near = sorted((c for c in numbered if c.dist_m is not None and c.dist_m <= cfg.lot_distance_m), key=lambda c: (c.dist_m, c.pid))
        if not near:
            nearest = min((c.dist_m for c in numbered if c.dist_m is not None), default=None)
            return _set_aside("LOT_TOO_FAR", {"nearest_m": None if nearest is None else round(nearest, 1), "limit_m": cfg.lot_distance_m})
        if len(near) > 1:
            return _set_aside("AMBIGUOUS_TIE", {"candidates": _cand_detail(near)})
        return Decision("link", "LOT_GEOCODE", round(1.0 - near[0].dist_m / cfg.lot_distance_m, 4), near[0].pid, rules=("LOT_RESOLVED_BY_GEOCODE",))
    return Decision("new", "NEW_DERIVED", 1.0)


@dataclass
class MatchCounts:
    linked: dict[str, int] = field(default_factory=dict)
    quarantined: dict[str, int] = field(default_factory=dict)
    new_properties: int = 0
    records: int = 0


def match_pending(cur: psycopg.Cursor, agency: str, cfg: MatchConfig = MatchConfig(), batch: int = BATCH) -> MatchCounts:
    """Decide every record that has no link yet (a record in open quarantine is decided again, so a later G-NAF load can free it). Runs inside agency_tx."""
    counts = MatchCounts()
    pending = cur.execute(
        """SELECT r.record_id FROM source_record r
           WHERE r.agency_id = %s AND r.norm_status IN ('ok', 'unparseable')
             AND NOT EXISTS (SELECT 1 FROM property_link l WHERE l.agency_id = r.agency_id AND l.record_id = r.record_id)
           ORDER BY r.record_id""", (agency,)).fetchall()
    ids = [r[0] for r in pending]
    for start in range(0, len(ids), batch):
        chunk = ids[start:start + batch]
        cands = block.candidates(cur, agency, chunk, cfg.floor)
        rows = cur.execute(
            """SELECT record_id, norm_status, norm_rules, unit, level, building, lot, number_first, number_last, street_name, street_type, locality_key, state, postcode,
                      ST_Y(geog::geometry), ST_X(geog::geometry) FROM source_record WHERE agency_id = %s AND record_id = ANY(%s) ORDER BY record_id""", (agency, chunk)).fetchall()
        for row in rows:
            record_id, status, norm_rules = row[0], row[1], list(row[2])
            addr = Addr(unit=row[3], level=row[4], building=row[5], lot=row[6], number_first=row[7], number_last=row[8], street_name=row[9], street_type=row[10],
                        locality=row[11], state=row[12], postcode=row[13])
            lat, lon = row[14], row[15]
            dec = decide(addr, lat, lon, cands.get(record_id, []), cfg) if status == "ok" else _unusable(norm_rules)
            counts.records += 1
            if dec.outcome == "quarantine":
                _quarantine(cur, agency, record_id, dec)
                counts.quarantined[dec.reason] = counts.quarantined.get(dec.reason, 0) + 1
                continue
            if dec.outcome == "new":
                property_id, added = ensure_derived_property(cur, addr, lat, lon)
            else:
                property_id, added = ensure_gnaf_property(cur, dec.pid)
            counts.new_properties += int(added)
            cur.execute("""INSERT INTO property_link (agency_id, record_id, property_id, rule, rules, score) VALUES (%s, %s, %s, %s, %s, %s)""",
                        (agency, record_id, property_id, dec.rule, [*norm_rules, *dec.rules], dec.score))
            cur.execute("""UPDATE quarantine SET status = 'resolved', resolved_at = now() WHERE agency_id = %s AND record_id = %s AND status = 'open'""", (agency, record_id))
            counts.linked[dec.rule] = counts.linked.get(dec.rule, 0) + 1
    return counts


def _unusable(norm_rules: list[str]) -> Decision:
    """The decision for a record that normalise could not turn into a street address: a mailbox is named as one, anything else is unparseable."""
    if "POSTAL_ADDRESS" in norm_rules:
        return _set_aside("POSTAL_ADDRESS", {"why": "a PO Box, GPO Box, Locked Bag or Private Bag is a mailbox, not a property"})
    return _set_aside("UNPARSEABLE", {"why": "normalise could not read a street and number"})


def _quarantine(cur: psycopg.Cursor, agency: str, record_id: str, dec: Decision) -> None:
    """Write the quarantine row. Writes nothing when an open row already says the same thing."""
    cur.execute(
        """INSERT INTO quarantine (agency_id, record_id, reason_code, detail) VALUES (%s, %s, %s, %s)
           ON CONFLICT (agency_id, record_id) DO UPDATE SET reason_code = EXCLUDED.reason_code, detail = EXCLUDED.detail
           WHERE (quarantine.reason_code, quarantine.detail) IS DISTINCT FROM (EXCLUDED.reason_code, EXCLUDED.detail)""",
        (agency, record_id, dec.reason, Jsonb(dec.detail)),
    )
