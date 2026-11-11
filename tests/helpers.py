"""Small builders for tests: a hand written G-NAF reference, feed files and a one call pipeline run."""
from __future__ import annotations

import csv
from pathlib import Path

import psycopg

from parcelkey import db, gnaf
from parcelkey.ingest import SOURCES
from parcelkey.match import MatchConfig
from parcelkey.pipeline import run_agency

STATE_NAMES = {"NSW": "NEW SOUTH WALES", "VIC": "VICTORIA", "QLD": "QUEENSLAND", "WA": "WESTERN AUSTRALIA", "SA": "SOUTH AUSTRALIA"}


def addr(pid, number="", street="SMITH", type="STREET", locality="FAKEVILLE", state="NSW", postcode="2000", unit="", level="", lot="", building="", lat=-33.80, lon=151.00,
         suffix="", flat_type="", level_type="", alias_of=""):
    """One hand written G-NAF address. `suffix` is a STREET_SUFFIX_AUT code, `flat_type` and `level_type` are authority codes, and `alias_of` makes this row an alias of that pid."""
    return dict(pid=pid, number=number, street=street, type=type, locality=locality, state=state, postcode=postcode, unit=unit, level=level, lot=lot, building=building, lat=lat, lon=lon,
                suffix=suffix, flat_type=flat_type, level_type=level_type, alias_of=alias_of)


def set_reference(admin_dsn: str, addresses: list[dict]) -> None:
    """Replace the G-NAF tables and the flat reference with these addresses (the same road the real loader fills)."""
    db.wipe_reference(admin_dsn)
    with psycopg.connect(admin_dsn) as conn:
        conn.execute(f"set search_path = {db.SCHEMA}, public")
        cur = conn.cursor()
        for state in sorted({a["state"] for a in addresses}):
            cur.execute("insert into state values (%s, '2016-02-01', null, %s, %s)", (state, STATE_NAMES[state], state))
        localities = {}
        streets = {}
        for a in addresses:
            lk = (a["state"], a["locality"], a["postcode"])
            if lk not in localities:
                localities[lk] = f"L{len(localities) + 1}"
                cur.execute("insert into locality values (%s, '2016-02-01', null, %s, %s, 'G', %s, null, null)", (localities[lk], a["locality"], a["postcode"], a["state"]))
            sk = (localities[lk], a["street"], a["type"], a["suffix"])
            if sk not in streets:
                streets[sk] = f"S{len(streets) + 1}"
                cur.execute("insert into street_locality values (%s, '2016-02-01', null, 'C', %s, %s, %s, %s, null, null, null)", (streets[sk], a["street"], a["type"], a["suffix"] or None, localities[lk]))
            flat = a["unit"]
            suffix = flat[-1] if flat and flat[-1].isalpha() else None
            flat_number = flat[:-1] if suffix else flat
            cur.execute(
                """insert into address_detail (address_detail_pid, date_created, building_name, lot_number, flat_type_code, flat_number, flat_number_suffix, level_type_code, level_number, number_first,
                                               street_locality_pid, locality_pid, postcode, address_site_pid, level_geocoded_code, alias_principal) values (%s, '2016-02-01', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 7, %s)""",
                (a["pid"], a["building"] or None, a["lot"] or None, a["flat_type"] or None, flat_number or None, suffix, a["level_type"] or None, a["level"] or None, a["number"] or None,
                 streets[sk], localities[lk], a["postcode"], a["pid"] + "S", "A" if a["alias_of"] else None))
            if a["alias_of"]:
                cur.execute("insert into address_alias values (%s, '2016-02-01', null, %s, %s, 'SYN', null)", (a["pid"] + "AL", a["alias_of"], a["pid"]))
            cur.execute("insert into address_default_geocode values (%s, '2016-02-01', null, %s, 'PC', %s, %s)", (a["pid"] + "G", a["pid"], a["lon"], a["lat"]))
        gnaf.build_reference(conn)


def crm(ref, address, suburb="Fakeville", state="NSW", postcode="2000", name="", email="", phone=""):
    return {"contact_ref": ref, "full_name": name, "email": email, "phone": phone, "address": address, "suburb": suburb, "state": state, "postcode": postcode, "created_on": "2026-01-01"}


def listing(ref, address, suburb="Fakeville", state="NSW", postcode="2000", lat="", lon="", campaign="CMP-001"):
    return {"listing_ref": ref, "campaign_id": campaign, "address": address, "suburb": suburb, "state": state, "postcode": postcode, "latitude": lat, "longitude": lon,
            "list_price": "900000", "listed_on": "2026-01-01", "status": "active"}


def sale(ref, address_full, name="", email="", phone=""):
    return {"sale_ref": ref, "address_full": address_full, "sold_on": "2026-01-01", "sale_price": "900000", "buyer_name": name, "buyer_email": email, "buyer_phone": phone}


def write_feeds(folder: Path, *, crm_rows=(), listing_rows=(), sales_rows=()) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for source, rows in (("crm", crm_rows), ("listing", listing_rows), ("sales", sales_rows)):
        if not rows:
            continue
        with (folder / SOURCES[source].file).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return folder


CRM_HEADER = "contact_ref,full_name,email,phone,address,suburb,state,postcode,created_on"


def crm_line(ref, address="12 Smith St", name="", email="", phone=""):
    return f"{ref},{name},{email},{phone},{address},Fakeville,NSW,2000,2026-01-01"


def crm_text(*lines, eol="\n"):
    """A crm_export.csv body: the header and these lines, written exactly as given (blank lines, short rows, extra cells and all)."""
    return eol.join([CRM_HEADER, *lines]) + eol


def write_crm_text(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SOURCES["crm"].file).write_bytes(text.encode("utf-8"))
    return folder


def run(conn, agency, folder, cfg: MatchConfig = MatchConfig(), **kw):
    kw.setdefault("new_agency", True)          # a test starts its own agency; the tests of the refusal pass new_agency=False
    return run_agency(conn, agency, folder, cfg, **kw)


def decisions(admin, agency: str) -> dict[str, tuple]:
    """record id -> ('link', rule, property canon_key or gnaf pid) or ('quarantine', reason)."""
    out: dict[str, tuple] = {}
    for rid, rule, origin, pid, canon in admin.execute(
            """select l.record_id, l.rule, p.origin, p.gnaf_pid, p.canon_key from property_link l join property p using (property_id) where l.agency_id = %s""", (agency,)).fetchall():
        out[rid] = ("link", rule, pid if origin == "gnaf" else canon)
    for rid, reason in admin.execute("select record_id, reason_code from quarantine where agency_id = %s and status = 'open'", (agency,)).fetchall():
        out[rid] = ("quarantine", reason)
    return out
