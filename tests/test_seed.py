"""The generator: same seed same bytes, the layout is the documented one, everything is made up on purpose, and the answer key covers every defect class."""
from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from pathlib import Path

import pytest

import seed as gen
from parcelkey.normalise import STATES, normalise_phone

PSV_COLUMNS = {
    "ADDRESS_DETAIL": 35, "PRIMARY_SECONDARY": 7, "ADDRESS_DEFAULT_GEOCODE": 7, "STREET_LOCALITY": 11, "LOCALITY": 9, "STATE": 5,
}


def digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


def rows(path: Path, delimiter=","):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiter))


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed")
    manifest = gen.generate(out, seed=11, addresses=800, per_agency=700)
    return out, manifest


def test_the_same_seed_writes_the_same_bytes_and_another_seed_does_not(made, tmp_path):
    out, _ = made
    gen.generate(tmp_path / "again", seed=11, addresses=800, per_agency=700)
    assert digest(out) == digest(tmp_path / "again")
    gen.generate(tmp_path / "other", seed=12, addresses=800, per_agency=700)
    assert digest(out)["answer_key.csv"] != digest(tmp_path / "other")["answer_key.csv"]


def test_gnaf_files_use_the_documented_names_and_column_counts(made):
    out, manifest = made
    standard = sorted(p.name for p in (out / "gnaf" / "Standard").iterdir())
    assert len(standard) == 30 and "NSW_ADDRESS_DETAIL_psv.psv" in standard and "NSW_PRIMARY_SECONDARY_psv.psv" in standard and "WA_STATE_psv.psv" in standard
    assert sorted(p.name for p in (out / "gnaf" / "Authority Code").iterdir()) == ["Authority_Code_FLAT_TYPE_AUT_psv.psv", "Authority_Code_LEVEL_TYPE_AUT_psv.psv", "Authority_Code_STREET_TYPE_AUT_psv.psv"]
    for table, ncol in PSV_COLUMNS.items():
        for path in (p for p in (out / "gnaf" / "Standard").glob("*.psv") if re.fullmatch(rf"[A-Z]+_{table}_psv\.psv", p.name)):
            lines = path.read_text(encoding="utf-8").splitlines()
            assert lines[0].split("|") == gen.HEADERS[table] and len(gen.HEADERS[table]) == ncol
            assert all(len(line.split("|")) == ncol for line in lines[1:]), path.name
    assert manifest["gnaf_rows"]["ADDRESS_DETAIL"] == manifest["properties_in_gnaf"] == manifest["gnaf_rows"]["ADDRESS_DEFAULT_GEOCODE"]


def test_the_address_detail_header_is_the_documented_thirty_five_columns_in_order():
    assert gen.HEADERS["ADDRESS_DETAIL"][:6] == ["ADDRESS_DETAIL_PID", "DATE_CREATED", "DATE_LAST_MODIFIED", "DATE_RETIRED", "BUILDING_NAME", "LOT_NUMBER_PREFIX"]
    assert gen.HEADERS["ADDRESS_DETAIL"][-3:] == ["PROPERTY_PID", "GNAF_PROPERTY_PID", "PRIMARY_SECONDARY"]
    for col in ("FLAT_TYPE_CODE", "FLAT_NUMBER", "LEVEL_NUMBER", "NUMBER_FIRST", "NUMBER_LAST", "LOT_NUMBER", "STREET_LOCALITY_PID", "LOCALITY_PID", "POSTCODE", "BUILDING_NAME"):
        assert col in gen.HEADERS["ADDRESS_DETAIL"]


def test_pids_are_unique_and_every_reference_resolves(made):
    out, _ = made
    details = [r for p in (out / "gnaf" / "Standard").glob("*_ADDRESS_DETAIL_psv.psv") for r in rows(p, "|")]
    pids = [r["ADDRESS_DETAIL_PID"] for r in details]
    assert len(pids) == len(set(pids)) and all(re.fullmatch(r"GA(NSW|VIC|QLD|WA|SA)9\d{8}", p) for p in pids) and all(len(p) <= 15 for p in pids)
    streets = {r["STREET_LOCALITY_PID"] for p in (out / "gnaf" / "Standard").glob("*_STREET_LOCALITY_psv.psv") for r in rows(p, "|")}
    localities = {r["LOCALITY_PID"] for p in (out / "gnaf" / "Standard").glob("*_LOCALITY_psv.psv") for r in rows(p, "|")}
    assert {r["STREET_LOCALITY_PID"] for r in details if r["STREET_LOCALITY_PID"]} <= streets and {r["LOCALITY_PID"] for r in details} <= localities
    geo = {r["ADDRESS_DETAIL_PID"] for p in (out / "gnaf" / "Standard").glob("*_ADDRESS_DEFAULT_GEOCODE_psv.psv") for r in rows(p, "|")}
    assert geo == set(pids)


def test_states_and_postcodes_have_a_valid_shape_and_range(made):
    out, _ = made
    ranges = {s: spec["pc"] for s, spec in gen.STATE_SPEC.items()}
    for state, (lo, hi) in ranges.items():
        for r in rows(out / "gnaf" / "Standard" / f"{state}_LOCALITY_psv.psv", "|"):
            assert re.fullmatch(r"\d{4}", r["PRIMARY_POSTCODE"]) and lo <= int(r["PRIMARY_POSTCODE"]) <= hi and state in STATES
        for r in rows(out / "gnaf" / "Standard" / f"{state}_ADDRESS_DETAIL_psv.psv", "|"):
            assert lo <= int(r["POSTCODE"]) <= hi


def test_coordinates_are_inside_australia_and_inside_their_state_box(made):
    out, _ = made
    for state, spec in gen.STATE_SPEC.items():
        lat0, lat1, lon0, lon1 = spec["box"]
        for r in rows(out / "gnaf" / "Standard" / f"{state}_ADDRESS_DEFAULT_GEOCODE_psv.psv", "|"):
            lat, lon = float(r["LATITUDE"]), float(r["LONGITUDE"])
            assert -44 < lat < -9 and 112 < lon < 154
            assert lat0 - 0.05 < lat < lat1 + 0.05 and lon0 - 0.05 < lon < lon1 + 0.05


def test_the_world_has_every_kind_of_address(made):
    out, _ = made
    details = [r for p in (out / "gnaf" / "Standard").glob("*_ADDRESS_DETAIL_psv.psv") for r in rows(p, "|")]
    assert any(r["FLAT_NUMBER"] and r["LEVEL_NUMBER"] and r["BUILDING_NAME"] for r in details)          # a tower flat
    assert any(r["BUILDING_NAME"] and not r["FLAT_NUMBER"] for r in details)                          # a tower with its own address
    assert any(r["FLAT_NUMBER"] and not r["LEVEL_NUMBER"] for r in details)                           # a block of units
    assert any(r["LOT_NUMBER"] and not r["NUMBER_FIRST"] for r in details)                            # a lot only address
    assert any(r["LOT_NUMBER"] and r["NUMBER_FIRST"] for r in details)                                # a numbered address that still carries its lot
    assert {r["FLAT_TYPE_CODE"] for r in details if r["FLAT_TYPE_CODE"]} == {"UNIT", "APT", "FLAT", "SE", "VLLA"}          # Geoscape's own codes, not their names
    names = [r["STREET_NAME"] for p in (out / "gnaf" / "Standard").glob("*_STREET_LOCALITY_psv.psv") for r in rows(p, "|")]
    assert any(n.startswith("MOUNT ") for n in names) and any(n.startswith("SAINT ") for n in names)
    types = {r["STREET_TYPE_CODE"] for p in (out / "gnaf" / "Standard").glob("*_STREET_LOCALITY_psv.psv") for r in rows(p, "|")}
    assert {"STREET", "ROAD", "AVENUE"} <= types


def test_every_feed_row_has_one_answer_key_row_and_every_class_appears(made):
    out, manifest = made
    key = rows(out / "answer_key.csv")
    ids = Counter((r["agency_id"], r["record_id"]) for r in key)
    assert max(ids.values()) == 1 and len(key) == manifest["answer_key_rows"]
    expected = set()
    for agency in gen.AGENCIES:
        for source, name in gen.FEED_FILES.items():
            ref_col = {"crm": "contact_ref", "listing": "listing_ref", "sales": "sale_ref"}[source]
            expected |= {(agency, f"{source}:{r[ref_col]}") for r in rows(out / agency / name)}
    assert expected == set(ids)
    seen = {r["class"] for r in key}
    missing = set(gen.CLASSES) - seen
    assert not missing, f"classes never generated: {sorted(missing)}"
    assert {r["contact_class"] for r in key if r["contact_class"]} == set(gen.CONTACT_CLASSES)


def test_expected_outcomes_follow_the_class(made):
    out, _ = made
    for r in rows(out / "answer_key.csv"):
        assert r["expected"] == ("quarantine" if r["class"] in gen.QUARANTINE_CLASSES else "link"), r
        if r["expected"] == "link":
            assert bool(r["truth_pid"]) != bool(r["truth_canon_key"]), r                  # a link has a G-NAF pid or a derived key, never both and never neither


def test_the_feeds_carry_the_defects_the_post_describes(made):
    out, _ = made
    crm = rows(out / "agency-a" / "crm_export.csv")
    text = " | ".join(r["address"] for r in crm)
    assert re.search(r"\bUnit \d+, ", text) and re.search(r"\b\d+/\d+ ", text) and re.search(r"\bApt \d+ \d+", text) and re.search(r"\bU\d+/\d+", text) and re.search(r"\bFlat \d+, ", text)
    assert re.search(r"Level \d+ Suite \d+", text) and re.search(r"\bLot \d+\b", " | ".join(r["address"] for r in rows(out / "agency-a" / "listing_feed.csv")))
    everything = " | ".join(r["address"] + " " + r["suburb"] for a in gen.AGENCIES for r in rows(out / a / "crm_export.csv") + rows(out / a / "listing_feed.csv"))
    assert re.search(r"\b(St|Str|Rd|Ave|Dr|Ct)\b", text) and re.search(r"\bMt [A-Z]", everything) and any(r["postcode"] == "" for r in crm)
    assert any(r["address"].isupper() for r in crm) and any(r["address"].islower() for r in crm)
    sales = rows(out / "agency-a" / "sales_history.csv")
    assert any(re.fullmatch(r".+, .+ [A-Za-z]+ ?\d{0,4}", r["address_full"]) for r in sales)
    listing = rows(out / "agency-a" / "listing_feed.csv")
    assert any(r["latitude"] for r in listing) and any(not r["latitude"] for r in listing)


def test_everything_is_made_up_emails_on_reserved_domains_phones_in_the_fiction_range(made):
    out, _ = made
    for agency in gen.AGENCIES:
        for r in rows(out / agency / "crm_export.csv"):
            if r["email"]:
                assert r["email"].strip().lower().endswith(("@example.com", "@example.net", "@example.org")), r["email"]
            if r["phone"] and r["phone"] != "tbc":
                e164, _ = normalise_phone(r["phone"])
                assert re.fullmatch(r"\+61[2378]5550\d{4}", e164), r["phone"]
        for r in rows(out / agency / "sales_history.csv"):
            if r["buyer_email"]:
                assert r["buyer_email"].strip().lower().endswith(("@example.com", "@example.net", "@example.org"))
            if r["buyer_phone"]:
                assert re.fullmatch(r"\+61[2378]5550\d{4}", normalise_phone(r["buyer_phone"])[0])


def test_contact_duplicates_have_the_shapes_the_post_describes(made):
    out, _ = made
    crm = rows(out / "agency-a" / "crm_export.csv")
    key = {r["record_id"]: r for r in rows(out / "answer_key.csv") if r["agency_id"] == "agency-a"}
    by_person: dict[str, list[dict]] = {}
    for r in crm:
        by_person.setdefault(key[f"crm:{r['contact_ref']}"]["person_id"], []).append(r)
    dups = [v for v in by_person.values() if len(v) > 1]
    assert dups
    assert any(len({x["email"].strip().lower() for x in v if x["email"]}) == 1 and len({x["email"] for x in v if x["email"]}) > 1 for v in dups)          # same email, different case
    assert any(len({x["full_name"] for x in v}) > 1 and len({x["email"] for x in v if x["email"]}) == 1 for v in dups)                                 # a name variant
    forms = {re.sub(r"\d", "9", r["phone"]) for r in crm if r["phone"] and r["phone"] != "tbc"}
    assert len(forms) >= 4, forms


def test_truth_properties_lists_every_property_and_every_new_build(made):
    out, manifest = made
    truth = rows(out / "truth_properties.csv")
    assert len(truth) == manifest["properties_in_gnaf"] + manifest["new_builds_not_in_gnaf"]
    assert sum(1 for r in truth if r["kind"] == "new_build") == manifest["new_builds_not_in_gnaf"] and all(r["canon_key"] for r in truth if r["kind"] == "new_build")


def test_the_manifest_is_written(made):
    out, manifest = made
    import json
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == manifest


def test_the_command_line_writes_the_world(tmp_path, capsys):
    assert gen.main(["--out", str(tmp_path / "w"), "--seed", "3", "--addresses", "200", "--per-agency", "100"]) == 0
    assert '"seed": 3' in capsys.readouterr().out and (tmp_path / "w" / "answer_key.csv").is_file()


def test_class_flats_are_secondary_addresses_and_the_primary_is_the_building_s_own_address(made):
    """CLASS-TABLE: F3. Every address with flat or level information is S, every other is P, and a tower flat points at the tower's own address in PRIMARY_SECONDARY."""
    import itertools
    out, _ = made
    standard = out / "gnaf" / "Standard"
    details = {r["ADDRESS_DETAIL_PID"]: r for p in standard.glob("*_ADDRESS_DETAIL_psv.psv") for r in rows(p, "|")}
    links = [r for p in standard.glob("*_PRIMARY_SECONDARY_psv.psv") for r in rows(p, "|")]
    counts = Counter(("flat" if r["FLAT_NUMBER"] or r["LEVEL_NUMBER"] else "plain", r["PRIMARY_SECONDARY"]) for r in details.values())
    table = []
    for kind, flag in itertools.product(("flat", "plain"), ("P", "S")):
        table.append(((kind, flag), counts[(kind, flag)] > 0, (kind, flag) in {("flat", "S"), ("plain", "P")}))
    for link in links:
        primary, secondary = details[link["PRIMARY_PID"]], details[link["SECONDARY_PID"]]
        table.append((("link", link["PRIMARY_SECONDARY_PID"]),
                      (primary["PRIMARY_SECONDARY"], bool(primary["FLAT_NUMBER"] or primary["LEVEL_NUMBER"]), secondary["PRIMARY_SECONDARY"], bool(secondary["FLAT_NUMBER"] or secondary["LEVEL_NUMBER"]),
                       link["PS_JOIN_TYPE_CODE"], link["DATE_RETIRED"], link["PS_JOIN_COMMENT"],
                       tuple(primary[c] for c in ("NUMBER_FIRST", "STREET_LOCALITY_PID", "LOCALITY_PID", "BUILDING_NAME")), tuple(secondary[c] for c in ("NUMBER_FIRST", "STREET_LOCALITY_PID", "LOCALITY_PID", "BUILDING_NAME"))),
                      ("P", False, "S", True, "1", "", "", tuple(primary[c] for c in ("NUMBER_FIRST", "STREET_LOCALITY_PID", "LOCALITY_PID", "BUILDING_NAME")), tuple(primary[c] for c in ("NUMBER_FIRST", "STREET_LOCALITY_PID", "LOCALITY_PID", "BUILDING_NAME")))))
    bad = [r for r in table if r[1] != r[2]]
    assert not bad, bad[:3]
    assert links and len({r["SECONDARY_PID"] for r in links}) == len(links) == len({r["PRIMARY_SECONDARY_PID"] for r in links})
    linked = {r["SECONDARY_PID"] for r in links}
    assert any(r["PRIMARY_SECONDARY"] == "S" and pid not in linked for pid, r in details.items())          # a block of units has no address of its own, so a flat can be secondary with no primary
    assert any(d["BUILDING_NAME"] and not d["FLAT_NUMBER"] for d in details.values())
