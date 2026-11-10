"""The master property id.

A property id is derived, never taken from a sequence, so the same address gets the same id on every run, on every machine and in every agency:
  matched to a G-NAF address  ->  `pk_g_` + 16 hex digits of sha256("gnaf|" + the G-NAF address detail pid)
  not in G-NAF                ->  `pk_d_` + 16 hex digits of sha256("derived|" + the canonical key of the address)
The canonical key leaves the postcode out (see `Addr.canon_key`), so a feed that drops or mistypes it cannot mint a second id for one address.
"""
from __future__ import annotations

import hashlib

import psycopg

from .normalise import Addr


def _digest(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}|{value}".encode("utf-8")).hexdigest()[:16]


def property_id_for_gnaf(address_detail_pid: str) -> str:
    return f"pk_g_{_digest('gnaf', address_detail_pid)}"


def property_id_for_derived(canon_key: str) -> str:
    return f"pk_d_{_digest('derived', canon_key)}"


def ensure_gnaf_property(cur: psycopg.Cursor, pid: str) -> tuple[str, bool]:
    """Make sure the master list has the property for a G-NAF address. Returns (its id, whether this call added it). An existing row is left alone."""
    property_id = property_id_for_gnaf(pid)
    cur.execute(
        """INSERT INTO property (property_id, origin, gnaf_pid, canon_key, display_address, postcode, state, geog)
           SELECT %s, 'gnaf', g.address_detail_pid, g.canon_key,
                  trim(both ', ' from concat_ws(', ', NULLIF(g.building, ''),
                       concat_ws(' ', CASE WHEN g.level <> '' THEN 'LEVEL ' || g.level END, CASE WHEN g.unit <> '' THEN 'UNIT ' || g.unit END,
                                 CASE WHEN g.lot <> '' THEN 'LOT ' || g.lot END, NULLIF(g.number_first, '') || CASE WHEN g.number_last <> '' THEN '-' || g.number_last ELSE '' END, g.street_key),
                       concat_ws(' ', g.locality_key, g.state, g.postcode))),
                  g.postcode, g.state, g.geog
           FROM gnaf_address g WHERE g.address_detail_pid = %s
           ON CONFLICT (property_id) DO NOTHING""",
        (property_id, pid),
    )
    return property_id, cur.rowcount == 1


def ensure_derived_property(cur: psycopg.Cursor, addr: Addr, lat: float | None = None, lon: float | None = None) -> tuple[str, bool]:
    """Make sure the master list has the property for an address that is not in G-NAF. Returns (its id, whether this call added it)."""
    key = addr.canon_key
    property_id = property_id_for_derived(key)
    cur.execute(
        """INSERT INTO property (property_id, origin, gnaf_pid, canon_key, display_address, postcode, state, geog)
           VALUES (%s, 'derived', NULL, %s, %s, %s, %s, CASE WHEN %s::float8 IS NULL OR %s::float8 IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(%s::float8, %s::float8), 4326)::geography END)
           ON CONFLICT (property_id) DO NOTHING""",
        (property_id, key, addr.line(), addr.postcode, addr.state, lon, lat, lon, lat),
    )
    return property_id, cur.rowcount == 1
