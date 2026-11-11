"""The whole pipeline on a small hand written reference: every outcome and every quarantine reason through the real database, PostGIS included."""
from __future__ import annotations

import pytest

import helpers as h
from conftest import AGENCY, OTHER, snapshot
from parcelkey import db
from parcelkey.master import property_id_for_derived, property_id_for_gnaf
from parcelkey.match import MatchConfig
from parcelkey.normalise import normalise_address

pytestmark = pytest.mark.db

A = h.addr


REFERENCE = [
    A("G12", "12"), A("G14", "14"), A("G16", "16", street="HARWICK"), A("G16B", "16", street="HARDWICK"),
    A("GT", "30", building="SKYLINE TOWER"), A("GT5", "30", unit="5", level="3"), A("GT6", "30", unit="6", level="3"), A("GT15", "30", unit="15", level="4"),
    A("GB4", "40", unit="4"), A("GB6", "40", unit="6"),
    A("GLOTONLY", "", street="BASE", type="ROAD", lot="7", lat=-33.800, lon=151.000),
    A("GLOTNUM", "23", street="BASE", type="ROAD", lot="9", lat=-33.8100, lon=151.0100),
    A("GOTHER", "12", locality="OTHERTON", postcode="2000"),
    A("GMT", "3", street="MOUNT PLEASANT", type="ROAD", locality="SAINT ALBANS", postcode="2001"),
]


@pytest.fixture()
def ref(hand):
    hand(REFERENCE)


def run_crm(conn, admin, tmp_path, rows, agency=AGENCY, cfg=MatchConfig()):
    folder = h.write_feeds(tmp_path / f"{agency}-{len(list(tmp_path.iterdir()))}", crm_rows=rows)
    h.run(conn, agency, folder, cfg)
    return h.decisions(admin, agency)


def test_every_spelling_of_one_flat_links_to_the_same_property(ref, conn, admin, tmp_path):
    forms = ["Unit 5, 30 Smith St", "5/30 Smith St", "Apt 5 30 Smith Street", "U5/30 smith st", "Flat 5, 30 SMITH STREET.", "Level 3 Suite 5, 30 Smith St"]
    d = run_crm(conn, admin, tmp_path, [h.crm(f"C{i}", f) for i, f in enumerate(forms)])
    assert {v[2] for v in d.values()} == {"GT5"} and {v[1] for v in d.values()} == {"EXACT_KEY"}


def test_tower_siblings_with_different_units_never_merge(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C5", "Unit 5, 30 Smith St"), h.crm("C6", "Unit 6, 30 Smith St"), h.crm("C15", "Unit 15, 30 Smith St"), h.crm("C51", "Unit 51, 30 Smith St")])
    assert (d["crm:C5"][2], d["crm:C6"][2], d["crm:C15"][2]) == ("GT5", "GT6", "GT15")
    assert d["crm:C51"] == ("quarantine", "UNIT_CONFLICT")


def test_a_house_street_number_with_no_unit_is_the_building_when_the_building_has_an_address_and_otherwise_set_aside(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "30 Smith St"), h.crm("C2", "40 Smith St")])
    assert d["crm:C1"][2] == "GT"
    assert d["crm:C2"] == ("quarantine", "UNIT_CONFLICT")


def test_a_unit_that_is_not_in_the_reference_is_a_unit_conflict(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "Unit 99, 30 Smith St"), h.crm("C2", "Unit 5, 12 Smith St")])
    assert d["crm:C1"] == ("quarantine", "UNIT_CONFLICT") and d["crm:C2"] == ("quarantine", "UNIT_CONFLICT")


def test_quarantine_detail_says_what_was_in_conflict(ref, conn, admin, tmp_path):
    run_crm(conn, admin, tmp_path, [h.crm("C1", "Unit 99, 40 Smith St")])
    assert admin.execute("select detail from quarantine").fetchone()[0] == {"record_unit": "99", "candidate_units": ["4", "6"]}


def test_a_near_miss_street_links_and_the_score_is_the_similarity(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "14 Smyth St"), h.crm("C2", "14 Smith Street")], cfg=MatchConfig(threshold=0.5))
    assert d["crm:C1"] == ("link", "TRIGRAM", "G14") and d["crm:C2"][1] == "EXACT_KEY"
    score = admin.execute("select score from property_link where record_id = 'crm:C1'").fetchone()[0]
    assert 0.5 <= score < 1


def test_a_street_typo_between_two_look_alike_streets_is_a_tie_not_a_guess(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "16 Harwik St"), h.crm("C2", "16 Harwick St"), h.crm("C3", "16 Hardwick St")], cfg=MatchConfig(threshold=0.4, tie_margin=0.5))
    assert d["crm:C1"] == ("quarantine", "AMBIGUOUS_TIE")
    assert d["crm:C2"][2] == "G16" and d["crm:C3"][2] == "G16B"


def test_a_street_that_is_only_a_little_alike_is_below_the_threshold(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "14 Smyth St")], cfg=MatchConfig(threshold=0.99))
    assert d["crm:C1"] == ("quarantine", "BELOW_THRESHOLD")


def test_a_wrong_or_missing_postcode_still_links_by_suburb_and_is_noted(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "12 Smith St", postcode="2999"), h.crm("C2", "12 Smith St", postcode=""), h.crm("C3", "12 Smith St", postcode="2000")])
    assert {v[2] for v in d.values()} == {"G12"}
    rules = {r[0]: r[1] for r in admin.execute("select record_id, rules from property_link").fetchall()}
    assert "POSTCODE_MISMATCH" in rules["crm:C1"] and "POSTCODE_MISSING" in rules["crm:C2"] and "POSTCODE_MISMATCH" not in rules["crm:C3"]


def test_the_same_street_and_number_in_two_suburbs_goes_to_the_suburb_written(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "12 Smith St", suburb="Fakeville"), h.crm("C2", "12 Smith St", suburb="Otherton")])
    assert (d["crm:C1"][2], d["crm:C2"][2]) == ("G12", "GOTHER")


def test_mount_and_saint_in_street_and_suburb(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "3 Mt Pleasant Rd", suburb="St Albans", postcode="2001")])
    assert d["crm:C1"][2] == "GMT"


def test_a_transposed_street_number_is_set_aside(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "21 Smith St")])
    assert d["crm:C1"] == ("quarantine", "NUMBER_TRANSPOSED")


def test_a_street_number_that_is_not_there_and_not_a_swap_is_a_new_derived_property(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "18 Smith St"), h.crm("C2", "18 SMITH STREET", postcode=""), h.crm("C3", "18 smith st.", postcode="2999")])
    keys = {v[2] for v in d.values()}
    expected = normalise_address("18 Smith St", "Fakeville", "NSW", "2000").canon_key
    assert keys == {expected} and {v[1] for v in d.values()} == {"NEW_DERIVED"}
    pid = admin.execute("select distinct property_id from property_link").fetchall()
    assert pid == [(property_id_for_derived(expected),)]
    assert admin.execute("select origin, gnaf_pid, display_address from property where property_id = %s", (pid[0][0],)).fetchone() == ("derived", None, "18 SMITH STREET, FAKEVILLE NSW 2000")


def test_property_ids_are_derived_not_numbered(ref, conn, admin, tmp_path):
    run_crm(conn, admin, tmp_path, [h.crm("C1", "12 Smith St")])
    assert admin.execute("select property_id, origin, gnaf_pid from property").fetchall() == [(property_id_for_gnaf("G12"), "gnaf", "G12")]
    assert property_id_for_gnaf("G12") == property_id_for_gnaf("G12") != property_id_for_gnaf("G14")
    assert property_id_for_gnaf("G12").startswith("pk_g_") and property_id_for_derived("x").startswith("pk_d_")


def test_two_agencies_get_the_same_property_id_for_the_same_address(ref, conn, admin, tmp_path):
    run_crm(conn, admin, tmp_path, [h.crm("C1", "12 Smith St"), h.crm("C2", "18 Smith St")], agency=AGENCY)
    run_crm(conn, admin, tmp_path, [h.crm("X9", "Unit 5, 12 Smith Street"), h.crm("X8", "12 smith st"), h.crm("X7", "18 SMITH ST")], agency=OTHER)
    got = {(r[0], r[1]): r[2] for r in admin.execute("select agency_id, record_id, property_id from property_link").fetchall()}
    assert got[(AGENCY, "crm:C1")] == got[(OTHER, "crm:X8")] and got[(AGENCY, "crm:C2")] == got[(OTHER, "crm:X7")]
    assert admin.execute("select count(*) from property").fetchone()[0] == 2


# ---- lots ---------------------------------------------------------------------------------------------------------------------------------

def run_listings(conn, admin, tmp_path, rows, cfg=MatchConfig()):
    folder = h.write_feeds(tmp_path / f"l{len(list(tmp_path.iterdir()))}", listing_rows=rows)
    h.run(conn, AGENCY, folder, cfg)
    return h.decisions(admin, AGENCY)


def test_a_lot_only_address_links_to_a_lot_only_reference_address(ref, conn, admin, tmp_path):
    d = run_listings(conn, admin, tmp_path, [h.listing("L1", "Lot 7 Base Rd")])
    assert d["listing:L1"] == ("link", "EXACT_LOT", "GLOTONLY")


def test_a_lot_never_merges_with_a_numbered_address_without_coordinates(ref, conn, admin, tmp_path):
    d = run_listings(conn, admin, tmp_path, [h.listing("L1", "Lot 9 Base Rd")])
    assert d["listing:L1"] == ("quarantine", "LOT_NO_GEOCODE")


def test_a_lot_merges_with_a_numbered_address_when_the_geocode_is_within_the_distance(ref, conn, admin, tmp_path):
    near = run_listings(conn, admin, tmp_path, [h.listing("L1", "Lot 9 Base Rd", lat="-33.81005", lon="151.01005")])          # about 7 m from the reference geocode
    assert near["listing:L1"][:2] == ("link", "LOT_GEOCODE") and near["listing:L1"][2] == "GLOTNUM"
    d = run_listings(conn, admin, tmp_path, [h.listing("L2", "Lot 9 Base Rd", lat="-33.8110", lon="151.0100")])                  # about 111 m away
    assert d["listing:L2"] == ("quarantine", "LOT_TOO_FAR")
    assert admin.execute("select detail from quarantine where record_id = 'listing:L2'").fetchone()[0]["limit_m"] == 50.0
    d = run_listings(conn, admin, tmp_path, [h.listing("L3", "Lot 9 Base Rd", lat="-33.8110", lon="151.0100")], cfg=MatchConfig(lot_distance_m=200.0))
    assert d["listing:L3"][:2] == ("link", "LOT_GEOCODE")


def test_the_distance_is_measured_on_the_earth_not_in_degrees(ref, conn, admin, tmp_path):
    """0.0004 degrees of longitude at this latitude is about 37 m; the same in latitude is about 44 m. Both are inside 50 m, and 0.0006 degrees of latitude (67 m) is not."""
    d = run_listings(conn, admin, tmp_path, [h.listing("L1", "Lot 9 Base Rd", lat="-33.8100", lon="151.0104"), h.listing("L2", "Lot 9 Base Rd", lat="-33.8106", lon="151.0100")])
    assert d["listing:L1"][1] == "LOT_GEOCODE" and d["listing:L2"] == ("quarantine", "LOT_TOO_FAR")


def test_listing_campaigns_are_linked_to_the_master_property(ref, conn, admin, tmp_path):
    run_listings(conn, admin, tmp_path, [h.listing("L1", "12 Smith St", campaign="CMP-001"), h.listing("L2", "12 Smith Street", campaign="CMP-002"), h.listing("L3", "14 Smith St", campaign="CMP-001")])
    with db.agency_tx(conn, AGENCY) as cur:
        rows = cur.execute("select campaign_id, property_id from campaign_property order by campaign_id, record_id").fetchall()
    assert [(c, p) for c, p in rows] == [("CMP-001", property_id_for_gnaf("G12")), ("CMP-001", property_id_for_gnaf("G14")), ("CMP-002", property_id_for_gnaf("G12"))]


# ---- unparseable and unusual ---------------------------------------------------------------------------------------------------------------------

def test_a_record_with_no_street_number_is_set_aside_as_unparseable(ref, conn, admin, tmp_path):
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "Flat 5"), h.crm("C2", "Smith St"), h.crm("C3", "12 Smith St")])
    assert d["crm:C1"] == ("quarantine", "UNPARSEABLE") and d["crm:C2"] == ("quarantine", "UNPARSEABLE") and d["crm:C3"][0] == "link"


def test_sales_rows_carry_their_whole_address_in_one_field(ref, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "s", sales_rows=[h.sale("S1", "5/30 Smith St, Fakeville NSW 2000"), h.sale("S2", "12 smith street, FAKEVILLE nsw"), h.sale("S3", "Apt 6 30 Smith Street, Fakeville NSW 2000")])
    h.run(conn, AGENCY, folder)
    d = h.decisions(admin, AGENCY)
    assert (d["sales:S1"][2], d["sales:S2"][2], d["sales:S3"][2]) == ("GT5", "G12", "GT6")


# ---- reruns and later changes -------------------------------------------------------------------------------------------------------------------

def test_a_second_run_writes_nothing_new(ref, conn, admin, tmp_path):
    rows = [h.crm("C1", "12 Smith St", name="Kate Smith", email="k@example.com"), h.crm("C2", "Unit 99, 30 Smith St"), h.crm("C3", "18 Smith St"), h.crm("C4", "Flat 5"), h.crm("C5", "21 Smith St")]
    folder = h.write_feeds(tmp_path / "f", crm_rows=rows)
    h.run(conn, AGENCY, folder)
    before = snapshot(admin)
    second = h.run(conn, AGENCY, folder)
    assert snapshot(admin) == before
    assert second.ingested[0].status == "skipped" and second.normalised == 0 and sum(second.match.linked.values()) == 0 and second.contacts.records == 0


def test_a_decided_link_is_never_rewritten_when_the_reference_changes_later(ref, conn, admin, hand, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "18 Smith St")])
    h.run(conn, AGENCY, folder)
    before = h.decisions(admin, AGENCY)["crm:C1"]
    decided = admin.execute("select property_id, decided_at from property_link").fetchone()
    hand(REFERENCE + [A("G18", "18")])                                    # the address arrives in G-NAF afterwards
    h.run(conn, AGENCY, folder)
    assert h.decisions(admin, AGENCY)["crm:C1"] == before
    assert admin.execute("select property_id, decided_at from property_link").fetchone() == decided


def test_a_quarantined_record_is_freed_when_a_later_reference_can_link_it(ref, conn, admin, hand, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "Unit 99, 30 Smith St")])
    h.run(conn, AGENCY, folder)
    assert h.decisions(admin, AGENCY)["crm:C1"] == ("quarantine", "UNIT_CONFLICT")
    created = admin.execute("select created_at from quarantine").fetchone()[0]
    h.run(conn, AGENCY, folder)
    assert admin.execute("select created_at from quarantine").fetchone()[0] == created             # nothing new while nothing changed
    hand(REFERENCE + [A("GT99", "30", unit="99", level="9")])
    h.run(conn, AGENCY, folder)
    assert h.decisions(admin, AGENCY)["crm:C1"][:3] == ("link", "EXACT_KEY", "GT99")
    assert admin.execute("select status, resolved_at is not null from quarantine").fetchone() == ("resolved", True)


def test_a_quarantine_row_changes_only_when_the_reason_changes(ref, conn, admin, hand, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "Unit 99, 30 Smith St")])
    h.run(conn, AGENCY, folder)
    hand(REFERENCE + [A("GT99B", "30", unit="99", level="9"), A("GT99C", "30", unit="99", level="9")])
    h.run(conn, AGENCY, folder)
    assert h.decisions(admin, AGENCY)["crm:C1"] == ("quarantine", "AMBIGUOUS_TIE")


def test_stages_report_their_timings(ref, conn, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    r = h.run(conn, AGENCY, folder)
    assert set(r.seconds) == {"ingest", "normalise", "match", "contacts"} and all(v >= 0 for v in r.seconds.values())
    assert r.match.records == 1 and r.match.linked == {"EXACT_KEY": 1} and r.match.new_properties == 1


def test_run_without_a_feed_folder_runs_only_the_later_stages(ref, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    from parcelkey.ingest import ingest_file
    with db.agency_tx(conn, AGENCY) as cur:
        ingest_file(cur, AGENCY, "crm", folder / "crm_export.csv", new_agency=True)
    r = h.run(conn, AGENCY, None)
    assert r.ingested == [] and r.normalised == 1 and r.match.linked == {"EXACT_KEY": 1}


def test_a_derived_property_keeps_the_geocode_of_a_listing_when_it_has_one(ref, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", listing_rows=[h.listing("L1", "18 Smith St", lat="-33.8123", lon="151.0456"), h.listing("L2", "20 Smith St")])
    h.run(conn, AGENCY, folder)
    got = {r[0]: (r[1], r[2]) for r in admin.execute("select display_address, ST_Y(geog::geometry), ST_X(geog::geometry) from property where origin = 'derived'").fetchall()}
    assert got["18 SMITH STREET, FAKEVILLE NSW 2000"] == (pytest.approx(-33.8123), pytest.approx(151.0456))
    assert got["20 SMITH STREET, FAKEVILLE NSW 2000"] == (None, None)


def test_a_property_is_counted_as_added_once_however_many_records_point_at_it(ref, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "12 Smith Street"), h.crm("C5", "12 smith st"), h.crm("C3", "18 Smith St"), h.crm("C4", "18 smith st"), h.crm("C6", "18 SMITH STREET")])
    r = h.run(conn, AGENCY, folder)
    assert r.match.new_properties == 2 and admin.execute("select count(*) from property").fetchone()[0] == 2
    assert h.run(conn, AGENCY, folder).match.new_properties == 0


def test_the_match_counts_name_each_reason_and_count_each_record(ref, conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "Unit 99, 30 Smith St"), h.crm("C2", "Unit 98, 30 Smith St"), h.crm("C3", "21 Smith St"), h.crm("C4", "12 Smith St")])
    r = h.run(conn, AGENCY, folder)
    assert r.match.quarantined == {"UNIT_CONFLICT": 2, "NUMBER_TRANSPOSED": 1} and r.match.linked == {"EXACT_KEY": 1} and r.match.records == 4


def test_candidates_honour_the_floor_and_each_address_comes_back_once(ref, conn, admin, tmp_path):
    from parcelkey import block
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "16 Harwik St")])
    from parcelkey.ingest import ingest_file
    from parcelkey.records import normalise_pending
    with db.agency_tx(conn, AGENCY) as cur:
        ingest_file(cur, AGENCY, "crm", folder / "crm_export.csv", new_agency=True)
        normalise_pending(cur, AGENCY)
    with db.agency_tx(conn, AGENCY) as cur:
        wide = block.candidates(cur, AGENCY, ["crm:C1", "crm:NOPE"], 0.3)
    with db.agency_tx(conn, AGENCY) as cur:
        narrow = block.candidates(cur, AGENCY, ["crm:C1"], 0.95)
    assert {"G16", "G16B"} <= {c.pid for c in wide["crm:C1"]} and len({c.pid for c in wide["crm:C1"]}) == len(wide["crm:C1"])
    assert wide["crm:NOPE"] == [] and narrow["crm:C1"] == []
    assert all(0.3 <= c.sim <= 1 for c in wide["crm:C1"])


def test_a_candidate_is_immutable():
    from dataclasses import FrozenInstanceError
    from parcelkey.block import Candidate
    from parcelkey.normalise import Addr
    with pytest.raises(FrozenInstanceError):
        Candidate("P", Addr(), 1.0, True, None).pid = "x"


# ---- second review: aliases, street suffixes, typed units, mailboxes, the display line (F1, F4, F5, F6, F10) ---------------------------------------------

def test_a_record_spelled_as_an_alias_links_to_the_principal_and_is_never_a_tie_with_it(hand, conn, admin, tmp_path):
    hand([h.addr("P1", "10"), h.addr("A1", "10", street="JONES", type="ROAD", alias_of="P1"), h.addr("A2", "10", alias_of="P1"), h.addr("P2", "20", street="JONES", type="ROAD")])
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "10 Smith St"), h.crm("C2", "10 Jones Rd"), h.crm("C3", "20 Jones Rd")])
    assert d["crm:C1"] == ("link", "EXACT_KEY", "P1") and d["crm:C2"] == ("link", "EXACT_KEY", "P1") and d["crm:C3"] == ("link", "EXACT_KEY", "P2")
    assert sorted(r[0] for r in admin.execute("select gnaf_pid from property").fetchall()) == ["P1", "P2"]


def test_a_near_miss_of_an_alias_spelling_links_to_the_principal_by_the_better_spelling(hand, conn, admin, tmp_path):
    hand([h.addr("P1", "10", street="SMITHSON"), h.addr("A1", "10", street="HARWICK", alias_of="P1")])
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "10 Harwik St")], cfg=MatchConfig(threshold=0.4))
    assert d["crm:C1"] == ("link", "TRIGRAM", "P1")
    assert admin.execute("select count(*) from property").fetchone()[0] == 1


def test_class_a_street_suffix_is_part_of_the_street(hand, conn, admin, tmp_path):
    """CLASS-TABLE: F5. Smith Street North and Smith Street South are two streets: a record names its own, a record that names no suffix is a tie when both streets have the number and a link
    when only one has it, a suffix the reference writes differently is a new property and never a near miss, and a reference with no suffixes at all is the same street as any suffix."""
    import itertools
    rows = []
    references = {"both": [h.addr("GN", "12", suffix="N"), h.addr("GS", "12", suffix="S")], "north only": [h.addr("GN", "12", suffix="N")], "none": [h.addr("G12", "12")]}
    records = {"North": "12 Smith St North", "N": "12 Smith St N", "South": "12 Smith Street South", "East": "12 Smith St East", "bare": "12 Smith St"}
    want = {
        ("both", "North"): ("link", "EXACT_KEY", "GN"), ("both", "N"): ("link", "EXACT_KEY", "GN"), ("both", "South"): ("link", "EXACT_KEY", "GS"),
        ("both", "East"): "NEW", ("both", "bare"): ("quarantine", "AMBIGUOUS_TIE"),
        ("north only", "North"): ("link", "EXACT_KEY", "GN"), ("north only", "N"): ("link", "EXACT_KEY", "GN"), ("north only", "South"): "NEW",
        ("north only", "East"): "NEW", ("north only", "bare"): ("link", "EXACT_KEY", "GN"),
        ("none", "North"): ("link", "EXACT_KEY", "G12"), ("none", "N"): ("link", "EXACT_KEY", "G12"), ("none", "South"): ("link", "EXACT_KEY", "G12"),
        ("none", "East"): ("link", "EXACT_KEY", "G12"), ("none", "bare"): ("link", "EXACT_KEY", "G12"),
    }
    for (ref_name, ref), (rec_name, text) in itertools.product(references.items(), records.items()):
        hand(ref)
        db.wipe(db.default_admin_dsn())
        folder = tmp_path / f"{ref_name}-{rec_name}".replace(" ", "_")
        folder.mkdir()
        d = run_crm(conn, admin, folder, [h.crm("C1", text)])["crm:C1"]
        rows.append(((ref_name, rec_name), "NEW" if d[:2] == ("link", "NEW_DERIVED") else d, want[(ref_name, rec_name)]))
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, bad[:5]


def test_a_carspace_and_a_unit_with_the_same_number_are_two_properties(hand, conn, admin, tmp_path):
    hand([h.addr("GU", "40", unit="3", flat_type="UNIT"), h.addr("GC", "40", unit="3", flat_type="CARS"), h.addr("GA", "40", unit="4", flat_type="APT"),
          h.addr("GB", "40", unit="1", level="1", level_type="B"), h.addr("GL", "40", unit="1", level="1", level_type="L")])
    d = run_crm(conn, admin, tmp_path, [h.crm("C1", "Carspace 3, 40 Smith St"), h.crm("C2", "Unit 3, 40 Smith St"), h.crm("C3", "Apartment 4, 40 Smith St"), h.crm("C4", "Flat 3/40 Smith St"),
                                        h.crm("C5", "Basement 1, Unit 1, 40 Smith St"), h.crm("C6", "Level 1 Unit 1, 40 Smith St"), h.crm("C7", "Unit 1, 40 Smith St")])
    assert (d["crm:C1"][2], d["crm:C2"][2], d["crm:C3"][2], d["crm:C4"][2]) == ("GC", "GU", "GA", "GU")
    assert (d["crm:C5"][2], d["crm:C6"][2]) == ("GB", "GL") and d["crm:C7"] == ("quarantine", "AMBIGUOUS_TIE")          # a record with no level cannot choose between two unit 1s


def test_class_a_mailbox_is_set_aside_with_its_own_reason_and_a_street_that_starts_with_box_is_matched(hand, conn, admin, tmp_path):
    """CLASS-TABLE: F6. A PO Box, GPO Box, Locked Bag or Private Bag is quarantined as POSTAL_ADDRESS, never matched to a street and never made a property; Box Hill Road is a street."""
    import itertools
    hand([h.addr("GBX", "14", street="BOX HILL", type="ROAD", locality="BOX HILL", state="VIC", postcode="3128"), h.addr("GBS", "12", street="BOX", type="STREET"), h.addr("GFAKE", "5", street="FAKEVILLE", type="STREET")])
    boxes = ["PO Box 5", "P.O. Box 5", "GPO Box 1234", "Locked Bag 5", "Private Bag 7"]
    rows = []
    records = []
    for i, (box, layout) in enumerate(itertools.product(boxes, ("own line", "one line"))):
        records.append(h.crm(f"M{i}", box) if layout == "own line" else h.crm(f"M{i}", f"{box} Fakeville NSW 2000", suburb="", state="", postcode=""))
    records += [h.crm("S1", "14 Box Hill Rd", suburb="Box Hill", state="VIC", postcode="3128"), h.crm("S2", "12 Box Street")]
    d = run_crm(conn, admin, tmp_path, records)
    for i, (box, layout) in enumerate(itertools.product(boxes, ("own line", "one line"))):
        rows.append(((box, layout), d[f"crm:M{i}"], ("quarantine", "POSTAL_ADDRESS")))
    rows += [(("street", "S1"), d["crm:S1"], ("link", "EXACT_KEY", "GBX")), (("street", "S2"), d["crm:S2"], ("link", "EXACT_KEY", "GBS"))]
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, bad[:5]
    assert admin.execute("select count(*) from property where origin = 'derived'").fetchone()[0] == 0
    assert admin.execute("select detail from quarantine where record_id = 'crm:M0'").fetchone()[0] == {"why": "a PO Box, GPO Box, Locked Bag or Private Bag is a mailbox, not a property"}
    assert admin.execute("select norm_status from source_record where record_id = 'crm:M0'").fetchone()[0] == "unparseable"


def test_class_the_master_list_keeps_the_g_naf_spelling_for_a_g_naf_match_and_the_normalised_one_for_a_derived_row(hand, conn, admin, tmp_path):
    """CLASS-TABLE: F10. ST KILDA stays ST KILDA for a G-NAF property; a derived property is written from the normalised parts."""
    import itertools
    streets = [("ST KILDA", "ROAD", "SAINT KILDA ROAD"), ("MT ELIZA", "WAY", "MOUNT ELIZA WAY"), ("SMITH", "STREET", "SMITH STREET"), ("O'CONNELL", "STREET", "OCONNELL STREET")]
    hand([h.addr(f"G{i}", "12", street=name, type=typ, locality="ST ALBANS", state="VIC", postcode="3021") for i, (name, typ, _) in enumerate(streets)])
    rows = []
    texts = [f"12 {name.title().replace(chr(39), '')} {typ.title()}" for name, typ, _ in streets]
    d = run_crm(conn, admin, tmp_path, [h.crm(f"C{i}", t, suburb="St Albans", state="VIC", postcode="3021") for i, t in enumerate(texts)]
                + [h.crm(f"D{i}", t.replace("12", "14"), suburb="St Albans", state="VIC", postcode="3021") for i, t in enumerate(texts)])
    for i, (name, typ, normalised) in enumerate(streets):
        gnaf_display = admin.execute("select display_address from property where gnaf_pid = %s", (f"G{i}",)).fetchone()
        rows.append((("G-NAF", name), gnaf_display and gnaf_display[0], f"12 {name} {typ}, ST ALBANS VIC 3021"))
        derived = admin.execute("select display_address from property where property_id = %s", (property_id_for_derived(normalise_address(f"14 {normalised.title()}", "St Albans", "VIC", "3021").canon_key),)).fetchone()
        rows.append((("derived", name), derived and derived[0], f"14 {normalised}, SAINT ALBANS VIC 3021"))
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, bad[:5]
    assert all(v[0] == "link" for v in d.values())


# ---- the fifth review (final blind recall of corpus-round-4): a record with no suburb, and a level against the building's own address ----------------------------------------------------

def test_class_a_record_with_no_suburb_that_links_to_nothing_is_no_locality_and_makes_no_derived_property(ref, conn, admin, tmp_path):
    """CLASS-TABLE: F3 (round 5). `99 Nowhere St` with no suburb is set aside as NO_LOCALITY whatever state and postcode it carries, and no derived property is made for it; the same record with a suburb is a
    new property, a record with no suburb but a postcode that finds its street still links, and reconcile accepts the new reason."""
    from parcelkey.reconcile import reconcile

    rows = [h.crm("C1", "99 Nowhere St", suburb="", state="", postcode=""), h.crm("C2", "99 Nowhere St", suburb="", state="NSW", postcode="2000"), h.crm("C3", "99 Nowhere St", suburb="", state="", postcode="2750"),
            h.crm("C4", "Lot 99 Nowhere Rd", suburb="", state="", postcode=""), h.crm("C5", "99 Nowhere St"), h.crm("C6", "14 Smith St", suburb="", state="", postcode="2000"),
            h.crm("C7", "99 Nowhere St, Fakeville NSW 2000", suburb="", state="", postcode="")]
    d = run_crm(conn, admin, tmp_path, rows)
    for rid in ("C1", "C2", "C3", "C4"):
        assert d[f"crm:{rid}"] == ("quarantine", "NO_LOCALITY"), rid
    assert d["crm:C5"][:2] == ("link", "NEW_DERIVED") and d["crm:C7"][:2] == ("link", "NEW_DERIVED") and d["crm:C5"][2] == d["crm:C7"][2]
    assert d["crm:C6"] == ("link", "EXACT_KEY", "G14")
    assert admin.execute("select count(*) from property where origin = 'derived'").fetchone()[0] == 1
    detail = admin.execute("select detail from quarantine where record_id = 'crm:C1'").fetchone()[0]
    assert detail["why"].startswith("no suburb")
    with db.agency_tx(conn, AGENCY) as cur:
        assert reconcile(cur, AGENCY).ok


def test_class_a_floor_is_not_the_building_when_the_reference_holds_both(hand, conn, admin, tmp_path):
    """CLASS-TABLE: F7 (round 5). G-NAF holds 100 Collins St, its level 3 and its level 5: `Level 3, 100 Collins St` links to level 3, `100 Collins St` to the building. Where G-NAF holds only floors (number 200)
    the record with no level is set aside and links to no floor."""
    hand([h.addr("GB", "100", street="COLLINS", locality="MELBOURNE", state="VIC", postcode="3000"), h.addr("GL3", "100", street="COLLINS", locality="MELBOURNE", state="VIC", postcode="3000", level="3"),
          h.addr("GL5", "100", street="COLLINS", locality="MELBOURNE", state="VIC", postcode="3000", level="5"),
          h.addr("GF3", "200", street="COLLINS", locality="MELBOURNE", state="VIC", postcode="3000", level="3"), h.addr("GF5", "200", street="COLLINS", locality="MELBOURNE", state="VIC", postcode="3000", level="5")])
    texts = {"Level 3, 100 Collins St": "GL3", "L5/100 Collins St": "GL5", "Level 3/100 Collins St": "GL3", "100 Collins St": "GB", "Level 9, 100 Collins St": "GB", "Level 3, 200 Collins St": "GF3",
             "Level 5/200 Collins St": "GF5", "200 Collins St": ("quarantine", "UNIT_CONFLICT")}
    d = run_crm(conn, admin, tmp_path, [h.crm(f"C{i}", t, suburb="Melbourne", state="VIC", postcode="3000") for i, t in enumerate(texts)])
    got = {t: d[f"crm:C{i}"][2] if d[f"crm:C{i}"][0] == "link" else d[f"crm:C{i}"] for i, t in enumerate(texts)}
    assert got == texts
