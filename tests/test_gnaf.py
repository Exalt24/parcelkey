"""The G-NAF layout loader and the flat reference table, on generated files only."""
from __future__ import annotations

import psycopg
import pytest

import helpers as h
import seed as gen
from parcelkey import db, gnaf
from parcelkey.gnaf import LoadReport, table_for_file

pytestmark = pytest.mark.db


@pytest.mark.parametrize("name,table", [
    ("ACT_ADDRESS_DETAIL_psv.psv", "address_detail"), ("NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv", "address_default_geocode"), ("VIC_STREET_LOCALITY_psv.psv", "street_locality"),
    ("QLD_LOCALITY_psv.psv", "locality"), ("WA_STATE_psv.psv", "state"), ("OT_STATE_psv.psv", "state"),
    ("Authority_Code_FLAT_TYPE_AUT_psv.psv", "flat_type_aut"), ("Authority_Code_STREET_TYPE_AUT_psv.psv", "street_type_aut"), ("Authority_Code_LEVEL_TYPE_AUT_psv.psv", "level_type_aut"),
    ("ACT_ADDRESS_ALIAS_psv.psv", "address_alias"), ("NSW_PRIMARY_SECONDARY_psv.psv", "primary_secondary"), ("ACT_ADDRESS_SITE_psv.psv", None), ("Authority_Code_GEOCODE_TYPE_AUT_psv.psv", None), ("readme.psv", None), ("NSW_ADDRESS_DETAIL.psv", None),
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
    assert report.files == 5 * 6 + 3 and report.rows_read == report.rows_inserted > 500
    for table in ("state", "locality", "street_locality", "address_detail", "address_default_geocode", "flat_type_aut", "street_type_aut", "level_type_aut"):
        assert empty_reference.execute(f"select count(*) from {table}").fetchone()[0] > 0, table
    assert empty_reference.execute("select count(*) from address_detail").fetchone()[0] == empty_reference.execute("select count(*) from address_default_geocode").fetchone()[0]
    assert report.skipped_files == [] and report.ignored_columns == {}


def test_a_second_load_adds_nothing(empty_reference, world_dir):
    first = gnaf.load_gnaf(empty_reference, world_dir / "gnaf")
    second = gnaf.load_gnaf(empty_reference, world_dir / "gnaf")
    assert first.rows_inserted > 0 and second.rows_read == first.rows_read and (second.rows_inserted, second.rows_changed, second.rows_retired) == (0, 0, 0)


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
    assert built == {"addresses": before, "aliases": 0, "skipped_no_street": 0, "skipped_alias_without_principal": 0}
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


def test_a_last_line_with_no_final_newline_is_loaded(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_STATE_psv.psv").write_bytes(b"STATE_PID|DATE_CREATED|STATE_NAME|STATE_ABBREVIATION\n1|2016-02-01|NEW SOUTH WALES|NSW\n2|2016-02-01|VICTORIA|VIC")
    report = gnaf.load_gnaf(empty_reference, folder)
    assert report.rows_read == 2 and empty_reference.execute("select state_abbreviation from state order by 1").fetchall() == [("NSW",), ("VIC",)]


# ---- aliases, primary and secondary links, the display line (F1, F3, F10) --------------------------------------------------------------------------

def rebuild():
    with psycopg.connect(db.default_admin_dsn()) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        return gnaf.build_reference(c)


def test_class_aliases_are_spellings_of_their_principal_never_properties(hand, admin):
    """CLASS-TABLE: F1. An address marked A is one more spelling of its principal. A principal (P, or no value) is a property. An alias with no live link to a principal is left out and counted."""
    import itertools
    flags = ("A", "P", None)
    links = ("good", "principal missing", "link retired", "principal has no street")
    cases = list(itertools.product(flags, links))
    addresses = []
    for i, (flag, link) in enumerate(cases):
        addresses.append(h.addr(f"PR{i}", str(200 + i)))
        addresses.append(h.addr(f"C{i}", str(100 + i), alias_of=f"PR{i}"))
    hand(addresses)
    for i, (flag, link) in enumerate(cases):
        admin.execute("update address_detail set alias_principal = %s where address_detail_pid = %s", (flag, f"C{i}"))
        if link == "principal missing":
            admin.execute("update address_alias set principal_pid = 'GONE' where alias_pid = %s", (f"C{i}",))
        elif link == "link retired":
            admin.execute("update address_alias set date_retired = '2020-01-01' where alias_pid = %s", (f"C{i}",))
        elif link == "principal has no street":
            admin.execute("update address_detail set street_locality_pid = null where address_detail_pid = %s", (f"PR{i}",))
    built = rebuild()
    got = {r[0]: r[1] for r in admin.execute("select address_detail_pid, principal_pid from gnaf_address").fetchall()}
    rows = []
    aliases = orphans = no_street = 0
    for i, (flag, link) in enumerate(cases):
        alias_kept = flag == "A" and link == "good"
        rows.append((("case", flag, link), f"C{i}" in got, flag != "A" or alias_kept))
        rows.append((("principal", flag, link), f"PR{i}" in got, link != "principal has no street"))
        if f"C{i}" in got:
            rows.append((("principal pid", flag, link), got[f"C{i}"], f"PR{i}" if flag == "A" else None))
        aliases += alias_kept
        orphans += flag == "A" and link != "good"
        no_street += link == "principal has no street"
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, bad[:5]
    principals = len([p for p in got if got[p] is None])
    assert (built["aliases"], built["skipped_alias_without_principal"], built["skipped_no_street"], built["addresses"]) == (aliases, orphans, no_street, principals)
    assert principals + aliases == len(got) and aliases == 1


def test_an_alias_with_two_live_links_takes_the_first_link_and_a_reference_without_aliases_has_none(hand, admin):
    hand([h.addr("P1", "10"), h.addr("P2", "20"), h.addr("A1", "11", alias_of="P1")])
    admin.execute("insert into address_alias values ('A0LINK', '2016-02-01', null, 'P2', 'A1', 'SYN', null)")
    rebuild()
    assert admin.execute("select principal_pid from gnaf_address where address_detail_pid = 'A1'").fetchone()[0] == "P2"          # 'A0LINK' sorts before 'A1AL'
    hand([h.addr("P1", "10")])
    built = rebuild()
    assert built["aliases"] == 0 and admin.execute("select count(*) from gnaf_address where principal_pid is not null").fetchone()[0] == 0


def test_the_loader_reads_address_alias_and_primary_secondary_files(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_ADDRESS_ALIAS_psv.psv").write_text("ADDRESS_ALIAS_PID|DATE_CREATED|DATE_RETIRED|PRINCIPAL_PID|ALIAS_PID|ALIAS_TYPE_CODE|ALIAS_COMMENT\nAL1|2016-02-01||P1|A1|SYN|Corner address\n", encoding="utf-8")
    (folder / "NSW_PRIMARY_SECONDARY_psv.psv").write_text("PRIMARY_SECONDARY_PID|DATE_CREATED|DATE_RETIRED|PRIMARY_PID|SECONDARY_PID|PS_JOIN_TYPE_CODE|PS_JOIN_COMMENT\nPS1|2016-02-01||P1|S1|1|\n", encoding="utf-8")
    report = gnaf.load_gnaf(empty_reference, folder)
    assert report.files == 2 and report.rows_inserted == 2 and report.skipped_files == []
    assert empty_reference.execute("select principal_pid, alias_pid, alias_type_code, alias_comment from address_alias").fetchall() == [("P1", "A1", "SYN", "Corner address")]
    assert empty_reference.execute("select primary_pid, secondary_pid, ps_join_type_code from primary_secondary").fetchall() == [("P1", "S1", 1)]


def test_the_flat_reference_carries_the_g_naf_display_line(world, admin):
    assert admin.execute("select count(*) from gnaf_address where display_address = ''").fetchone()[0] == 0
    rows = admin.execute("select display_address, level, number_first, street_key, locality_key, state from gnaf_address where level <> '' limit 20").fetchall()
    assert rows
    for display, level, number, street_key, locality, state in rows:
        assert f"LEVEL {level} " in display and f" {number} {street_key}, " in display and f"{locality} {state}" in display


# ---- a newer release loaded on top of an older one (P5-2) ---------------------------------------------------------------------------------------------

# table -> (a column that is not the key, the value a new release gives it); one row of each table is edited in the class test below
TABLE_EDIT = {
    "state": ("state_name", "CHANGED STATE"), "locality": ("locality_name", "CHANGED LOCALITY"), "street_locality": ("street_name", "CHANGED STREET"),
    "address_detail": ("building_name", "NEW TOWER"), "address_default_geocode": ("latitude", "-33.1234567"), "primary_secondary": ("ps_join_comment", "edited"),
    "flat_type_aut": ("name", "CHANGED FLAT"), "street_type_aut": ("name", "CHANGED STREET TYPE"), "level_type_aut": ("name", "CHANGED LEVEL"), "address_alias": ("alias_comment", "edited"),
}
ALIAS_FILE = "ADDRESS_ALIAS_PID|DATE_CREATED|DATE_RETIRED|PRINCIPAL_PID|ALIAS_PID|ALIAS_TYPE_CODE|ALIAS_COMMENT\nAL1|2016-02-01||P1|A1|SYN|first\nAL2|2016-02-01||P2|A2|SYN|second\n"


@pytest.fixture()
def small_gnaf(tmp_path):
    from pathlib import Path
    out = tmp_path / "world"
    gen.generate(out, seed=3, addresses=100, per_agency=20)
    (out / "gnaf" / "NSW_ADDRESS_ALIAS_psv.psv").write_text(ALIAS_FILE, encoding="utf-8")
    return Path(out) / "gnaf"


def edit_first_row(gnaf_dir, table, column, value):
    """Change `column` of the first data row of the first file of `table` under gnaf_dir. Returns the key of that row."""
    path = next(p for p in sorted(gnaf_dir.rglob("*.psv")) if table_for_file(p.name) == table)
    lines = path.read_text(encoding="utf-8").splitlines()
    cols = [c.lower() for c in lines[0].split("|")]
    row = lines[1].split("|")
    row[cols.index(column)] = value
    lines[1] = "|".join(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return row[0]


def test_class_a_reload_updates_a_changed_column_in_every_modelled_table_and_counts_it(empty_reference, small_gnaf):
    """CLASS-TABLE (P5-2): the loader used to keep the first value of a row for ever (ON CONFLICT DO NOTHING). One edited row in each of the ten tables is one changed row, written."""
    first = gnaf.load_gnaf(empty_reference, small_gnaf)
    assert first.rows_changed == 0 and first.rows_retired == 0 and first.rows_inserted == first.rows_read
    for table, (column, value) in TABLE_EDIT.items():
        key = edit_first_row(small_gnaf, table, column, value)
        again = gnaf.load_gnaf(empty_reference, small_gnaf)
        assert (again.rows_inserted, again.rows_changed, again.rows_retired) == (0, 1, 0), table
        pk = gnaf._primary_key(empty_reference.cursor(), table)[0]
        got = empty_reference.execute(f"select {column}::text from {table} where {pk} = %s", (key,)).fetchone()[0]
        assert got == value or float(got) == float(value), table
        assert gnaf.load_gnaf(empty_reference, small_gnaf).rows_changed == 0, table            # the same release again changes nothing


def test_a_reload_that_gives_a_row_a_retired_date_counts_it_retired_and_the_rebuild_drops_it(empty_reference, small_gnaf):
    gnaf.load_gnaf(empty_reference, small_gnaf)
    gnaf.build_reference(empty_reference)
    pid = edit_first_row(small_gnaf, "address_detail", "date_retired", "2026-06-30")
    report = gnaf.load_gnaf(empty_reference, small_gnaf)
    assert (report.rows_inserted, report.rows_changed, report.rows_retired) == (0, 1, 1)
    assert str(empty_reference.execute("select date_retired from address_detail where address_detail_pid = %s", (pid,)).fetchone()[0]) == "2026-06-30"
    gnaf.build_reference(empty_reference)
    assert empty_reference.execute("select count(*) from gnaf_address where address_detail_pid = %s", (pid,)).fetchone()[0] == 0
    again = gnaf.load_gnaf(empty_reference, small_gnaf)
    assert (again.rows_changed, again.rows_retired) == (0, 0)                                  # already retired is not retired again
    edit_first_row(small_gnaf, "address_detail", "date_retired", "2026-07-31")
    assert (lambda r: (r.rows_changed, r.rows_retired))(gnaf.load_gnaf(empty_reference, small_gnaf)) == (1, 0)          # a new retired date on a retired row is a change, not a new retirement


def test_a_retired_date_that_comes_back_removed_makes_the_address_live_again(empty_reference, small_gnaf):
    gnaf.load_gnaf(empty_reference, small_gnaf)
    pid = edit_first_row(small_gnaf, "address_detail", "date_retired", "2026-06-30")
    gnaf.load_gnaf(empty_reference, small_gnaf)
    edit_first_row(small_gnaf, "address_detail", "date_retired", "")
    assert gnaf.load_gnaf(empty_reference, small_gnaf).rows_changed == 1
    assert empty_reference.execute("select date_retired from address_detail where address_detail_pid = %s", (pid,)).fetchone()[0] is None


def test_a_row_the_new_release_leaves_out_is_left_as_it_was(empty_reference, small_gnaf):
    gnaf.load_gnaf(empty_reference, small_gnaf)
    path = next(p for p in sorted(small_gnaf.rglob("*.psv")) if table_for_file(p.name) == "address_detail")
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    before = empty_reference.execute("select count(*) from address_detail").fetchone()[0]
    report = gnaf.load_gnaf(empty_reference, small_gnaf)
    assert before > 1 and empty_reference.execute("select count(*) from address_detail").fetchone()[0] == before and report.rows_inserted == 0


def test_a_key_repeated_in_one_file_is_not_an_error_and_the_last_row_wins(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_STATE_psv.psv").write_text("STATE_PID|DATE_CREATED|STATE_NAME|STATE_ABBREVIATION\n1|2016-02-01|FIRST|NSW\n1|2016-02-01|LAST|NSW\n", encoding="utf-8")
    report = gnaf.load_gnaf(empty_reference, folder)
    assert empty_reference.execute("select state_name from state").fetchall() == [("LAST",)] and (report.rows_read, report.rows_inserted, report.rows_changed) == (2, 1, 0)


def test_a_file_without_its_key_column_or_with_only_key_columns_is_refused_naming_the_file(empty_reference, tmp_path):
    folder = tmp_path / "gnaf"
    folder.mkdir()
    (folder / "NSW_STATE_psv.psv").write_text("DATE_CREATED|STATE_NAME|STATE_ABBREVIATION\n2016-02-01|X|NSW\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"NSW_STATE_psv.psv: the primary key column\(s\) state_pid are not in the file"):
        gnaf.load_gnaf(empty_reference, folder)
    (folder / "NSW_STATE_psv.psv").write_text("STATE_PID\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"NSW_STATE_psv.psv: the file has only key columns"):
        gnaf.load_gnaf(empty_reference, folder)


def test_primary_keys_are_read_from_the_catalogue(empty_reference):
    cur = empty_reference.cursor()
    assert gnaf._primary_key(cur, "state") == ["state_pid"] and gnaf._primary_key(cur, "flat_type_aut") == ["code"] and gnaf._primary_key(cur, "address_detail") == ["address_detail_pid"]


# ---- the master list's copy of a G-NAF address follows the release --------------------------------------------------------------------------------

def test_refresh_properties_follows_a_corrected_address_and_leaves_a_retired_one(hand, conn, admin, admin_dsn, tmp_path):
    hand([h.addr("G12", "12"), h.addr("G14", "14")])
    h.run(conn, "agency-a", h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "14 Smith St")]))
    ids = dict(admin.execute("select gnaf_pid, property_id from property").fetchall())
    assert set(ids) == {"G12", "G14"}
    with psycopg.connect(admin_dsn) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        assert gnaf.refresh_properties(c) == {"refreshed": 0, "retired": 0}                        # nothing changed: nothing written
        c.execute("update address_detail set number_first = '120', postcode = '2999' where address_detail_pid = 'G12'")
        c.execute("update address_default_geocode set latitude = -33.5 where address_detail_pid = 'G12'")
        c.execute("update address_detail set date_retired = '2026-06-30' where address_detail_pid = 'G14'")
        gnaf.build_reference(c)
        assert gnaf.refresh_properties(c) == {"refreshed": 1, "retired": 1}
        assert gnaf.refresh_properties(c) == {"refreshed": 0, "retired": 1}                        # the second call finds nothing more to refresh
    row = admin.execute("select property_id, display_address, postcode, state, canon_key, ST_Y(geog::geometry) from property where gnaf_pid = 'G12'").fetchone()
    assert row[0] == ids["G12"] and "120" in row[1] and row[2] == "2999" and row[3] == "NSW" and "|120|" in row[4] and round(row[5], 1) == -33.5
    still = admin.execute("select property_id, display_address from property where gnaf_pid = 'G14'").fetchone()
    assert still[0] == ids["G14"] and "14" in still[1]                                            # the retired address's property is left as it is


def test_refresh_properties_leaves_derived_properties_alone(hand, conn, admin, admin_dsn, tmp_path):
    hand([h.addr("G12", "12")])
    h.run(conn, "agency-a", h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "99 Newbuild St")]))
    before = admin.execute("select property_id, display_address, canon_key from property where origin = 'derived'").fetchall()
    with psycopg.connect(admin_dsn) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        assert gnaf.refresh_properties(c) == {"refreshed": 0, "retired": 0}
    assert before and admin.execute("select property_id, display_address, canon_key from property where origin = 'derived'").fetchall() == before


def test_load_gnaf_through_the_command_line_reports_changed_and_retired_rows_and_refreshed_properties(clean, world_dir, tmp_path):
    import shutil
    from conftest import _STATE
    from test_quality_cli import cli
    _STATE["reference"] = ""
    db.wipe(clean, reference=True)
    folder = tmp_path / "gnaf"
    shutil.copytree(world_dir / "gnaf", folder)
    first = cli("load-gnaf", str(folder), check=True).stdout
    assert "0 changed rows (0 of them newly retired)" in first and "master properties: 0 refreshed from the new reference, 0 point at a G-NAF address the reference no longer has" in first
    edit_first_row(folder, "address_detail", "date_retired", "2026-06-30")
    edit_first_row(folder, "locality", "locality_name", "NEW NAME")
    second = cli("load-gnaf", str(folder), check=True).stdout
    assert "0 new rows, 2 changed rows (1 of them newly retired)" in second
    quiet = cli("load-gnaf", str(folder), "--no-build", check=True).stdout
    assert "reference table built" not in quiet and "master properties" not in quiet
