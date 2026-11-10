"""The G-NAF layout loader and the flat reference table, on generated files only."""
from __future__ import annotations

import psycopg
import pytest

import seed as gen
from parcelkey import db, gnaf
from parcelkey.gnaf import LoadReport, table_for_file

pytestmark = pytest.mark.db


@pytest.mark.parametrize("name,table", [
    ("ACT_ADDRESS_DETAIL_psv.psv", "address_detail"), ("NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv", "address_default_geocode"), ("VIC_STREET_LOCALITY_psv.psv", "street_locality"),
    ("QLD_LOCALITY_psv.psv", "locality"), ("WA_STATE_psv.psv", "state"), ("OT_STATE_psv.psv", "state"),
    ("Authority_Code_FLAT_TYPE_AUT_psv.psv", "flat_type_aut"), ("Authority_Code_STREET_TYPE_AUT_psv.psv", "street_type_aut"), ("Authority_Code_LEVEL_TYPE_AUT_psv.psv", "level_type_aut"),
    ("ACT_ADDRESS_ALIAS_psv.psv", None), ("ACT_ADDRESS_SITE_psv.psv", None), ("Authority_Code_GEOCODE_TYPE_AUT_psv.psv", None), ("readme.psv", None), ("NSW_ADDRESS_DETAIL.psv", None),
])
def test_file_names_map_to_tables(name, table):
    assert table_for_file(name) == table


@pytest.fixture()
def empty_reference(clean):
    db.wipe(clean, reference=True)
    from conftest import _STATE
    _STATE["reference"] = ""
    with psycopg.connect(clean, options=f"-c search_path={db.SCHEMA},public") as c:          # a connection option survives a rollback, a SET inside a transaction does not
        yield c


def test_load_reads_every_modelled_file_and_reports_the_rest(empty_reference, world_dir):
    report = gnaf.load_gnaf(empty_reference, world_dir / "gnaf")
    assert report.files == 5 * 5 + 3 and report.rows_read == report.rows_inserted > 500
    for table in ("state", "locality", "street_locality", "address_detail", "address_default_geocode", "flat_type_aut", "street_type_aut", "level_type_aut"):
        assert empty_reference.execute(f"select count(*) from {table}").fetchone()[0] > 0, table
    assert empty_reference.execute("select count(*) from address_detail").fetchone()[0] == empty_reference.execute("select count(*) from address_default_geocode").fetchone()[0]
    assert report.skipped_files == [] and report.ignored_columns == {}


def test_a_second_load_adds_nothing(empty_reference, world_dir):
    first = gnaf.load_gnaf(empty_reference, world_dir / "gnaf")
    second = gnaf.load_gnaf(empty_reference, world_dir / "gnaf")
    assert first.rows_inserted > 0 and second.rows_read == first.rows_read and second.rows_inserted == 0


def test_columns_are_matched_by_name_not_by_position(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_STATE_psv.psv").write_text("STATE_ABBREVIATION|STATE_NAME|DATE_CREATED|STATE_PID\nNSW|NEW SOUTH WALES|2016-02-01|1\n", encoding="utf-8")
    gnaf.load_gnaf(empty_reference, folder)
    assert empty_reference.execute("select state_pid, state_name, state_abbreviation, date_created::text from state").fetchall() == [("1", "NEW SOUTH WALES", "NSW", "2016-02-01")]


def test_an_unknown_column_is_skipped_and_reported_and_an_unmodelled_table_is_skipped(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_STATE_psv.psv").write_text("STATE_PID|DATE_CREATED|DATE_RETIRED|STATE_NAME|STATE_ABBREVIATION|GEOMETRY\n1|2016-02-01||NEW SOUTH WALES|NSW|POINT(1 2)\n", encoding="utf-8")
    (folder / "NSW_ADDRESS_SITE_psv.psv").write_text("ADDRESS_SITE_PID|DATE_CREATED\nX|2016-02-01\n", encoding="utf-8")
    report = gnaf.load_gnaf(empty_reference, folder)
    assert report.ignored_columns == {"NSW_STATE_psv.psv": ["geometry"]} and report.skipped_files == ["NSW_ADDRESS_SITE_psv.psv"]
    assert empty_reference.execute("select count(*) from state").fetchone()[0] == 1


def test_a_value_that_does_not_cast_stops_the_load_naming_the_file_and_stores_nothing(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_STATE_psv.psv").write_text("STATE_PID|DATE_CREATED|STATE_NAME|STATE_ABBREVIATION\n1|31/12/2016|NEW SOUTH WALES|NSW\n", encoding="utf-8")
    with pytest.raises(ValueError, match="NSW_STATE_psv.psv"):
        gnaf.load_gnaf(empty_reference, folder)
    empty_reference.rollback()
    assert empty_reference.execute("select count(*) from state").fetchone()[0] == 0


def test_quotes_and_odd_characters_survive(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_LOCALITY_psv.psv").write_text('LOCALITY_PID|DATE_CREATED|LOCALITY_NAME|LOCALITY_CLASS_CODE|STATE_PID\nL1|2016-02-01|O"CONNELL\'S REST|G|1\n', encoding="utf-8")
    gnaf.load_gnaf(empty_reference, folder)
    assert empty_reference.execute("select locality_name from locality").fetchone()[0] == "O\"CONNELL'S REST"


def test_build_reference_makes_one_normalised_row_per_address(world, admin):
    n_detail = admin.execute("select count(*) from address_detail").fetchone()[0]
    assert admin.execute("select count(*) from gnaf_address").fetchone()[0] == n_detail
    assert admin.execute("select count(*) from gnaf_address where geog is null").fetchone()[0] == 0
    assert admin.execute("select count(*) from gnaf_address where street_key = '' or locality_key = ''").fetchone()[0] == 0
    row = admin.execute("select unit, level, number_first, street_type from gnaf_address where unit <> '' limit 1").fetchone()
    assert row[0] and row[2] and row[3] in {t for t, _ in gen.AUTHORITY["STREET_TYPE_AUT"]}


def test_build_reference_geography_is_in_australia(world, admin):
    lo, hi = admin.execute("select min(ST_Y(geog::geometry)), max(ST_Y(geog::geometry)) from gnaf_address").fetchone()
    assert -44 < lo < hi < -9
    lo, hi = admin.execute("select min(ST_X(geog::geometry)), max(ST_X(geog::geometry)) from gnaf_address").fetchone()
    assert 112 < lo < hi < 154


def test_build_reference_is_a_rebuild_not_an_append(world, admin):
    before = admin.execute("select count(*) from gnaf_address").fetchone()[0]
    with psycopg.connect(db.default_admin_dsn()) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        built = gnaf.build_reference(c)
    assert built == {"addresses": before, "skipped_no_street": 0}
    assert admin.execute("select count(*) from gnaf_address").fetchone()[0] == before


def test_retired_addresses_and_addresses_without_a_street_are_left_out(world, admin):
    from conftest import _STATE
    _STATE["reference"] = ""                      # this test edits the shared reference, so the next test that needs it reloads it
    pid, other = [r[0] for r in admin.execute("select address_detail_pid from address_detail order by 1 limit 2").fetchall()]
    admin.execute("update address_detail set date_retired = '2020-01-01' where address_detail_pid = %s", (pid,))
    admin.execute("update address_detail set street_locality_pid = null where address_detail_pid = %s", (other,))
    with psycopg.connect(db.default_admin_dsn()) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        built = gnaf.build_reference(c)
    assert built["skipped_no_street"] == 1
    assert admin.execute("select count(*) from gnaf_address where address_detail_pid in (%s, %s)", (pid, other)).fetchone()[0] == 0


def test_load_report_defaults():
    r = LoadReport()
    assert (r.files, r.rows_read, r.rows_inserted, r.skipped_files, r.ignored_columns) == (0, 0, 0, [], {})
