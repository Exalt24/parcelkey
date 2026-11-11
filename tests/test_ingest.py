"""Ingest: byte for byte storage, rejects with reasons, the refusal of a feed with a missing column, and a rerun that writes nothing."""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

import helpers as h
from conftest import AGENCY, snapshot

from parcelkey import db
from parcelkey.db import agency_tx
from parcelkey.ingest import SOURCES, FeedRefused, ingest_file, read_feed, row_sha
from parcelkey.records import normalise_pending

pytestmark = pytest.mark.db


def ingest(conn, path, source, **kw):
    with agency_tx(conn, AGENCY) as cur:
        return ingest_file(cur, AGENCY, source, path, **kw)


def test_rows_are_stored_as_received(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "  12  smith ST ", name=" Kate  Smith ", email="K@X.COM"), h.crm("C2", "5/9 Rose Rd")])
    r = ingest(conn, folder / "crm_export.csv", "crm")
    assert (r.status, r.row_count, r.stored, r.rejected, r.unchanged) == ("loaded", 2, 2, 0, 0)
    raw = admin.execute("select raw from source_record where record_id = 'crm:C1'").fetchone()[0]
    assert raw["address"] == "  12  smith ST " and raw["full_name"] == " Kate  Smith " and raw["email"] == "K@X.COM"
    assert admin.execute("select norm_status from source_record").fetchall() == [("pending",), ("pending",)]
    assert admin.execute("select record_id, source, source_ref, line_no from source_record order by 1").fetchall() == [("crm:C1", "crm", "C1", 2), ("crm:C2", "crm", "C2", 3)]


def test_same_bytes_again_writes_nothing(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    ingest(conn, folder / "crm_export.csv", "crm")
    before = snapshot(admin)
    r = ingest(conn, folder / "crm_export.csv", "crm")
    assert r.status == "skipped" and snapshot(admin) == before


def test_rejects_carry_a_reason_and_the_raw_row(conn, admin, tmp_path):
    rows = [h.crm("C1", "12 Smith St"), h.crm("", "5 Rose Rd"), h.crm("C3", "   "), h.crm("C1", "99 Other St")]
    folder = h.write_feeds(tmp_path / "f", crm_rows=rows)
    r = ingest(conn, folder / "crm_export.csv", "crm")
    assert (r.row_count, r.stored, r.rejected) == (4, 1, 3)
    got = admin.execute("select line_no, reason_code, raw ->> 'address' from ingest_reject order by line_no").fetchall()
    assert got == [(3, "MISSING_REF", "5 Rose Rd"), (4, "BLANK_ADDRESS", "   "), (5, "DUPLICATE_REF", "99 Other St")]
    assert admin.execute("select raw ->> 'address' from source_record").fetchone()[0] == "12 Smith St"            # the first row of a repeated reference is the one kept


def test_a_later_file_with_the_same_rows_counts_them_unchanged_and_adds_the_new_ones(conn, admin, tmp_path):
    a = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "5 Rose Rd")])
    b = h.write_feeds(tmp_path / "b", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C2", "5 Rose Rd"), h.crm("C3", "7 Elm Ct")])
    ingest(conn, a / "crm_export.csv", "crm")
    r = ingest(conn, b / "crm_export.csv", "crm")
    assert (r.row_count, r.stored, r.rejected, r.unchanged) == (3, 1, 0, 2)
    assert admin.execute("select count(*) from source_record").fetchone()[0] == 3


def test_a_reference_that_changed_after_it_was_loaded_is_rejected_not_replaced(conn, admin, tmp_path):
    a = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")])
    b = h.write_feeds(tmp_path / "b", crm_rows=[h.crm("C1", "99 Changed Rd")])
    ingest(conn, a / "crm_export.csv", "crm")
    r = ingest(conn, b / "crm_export.csv", "crm")
    assert (r.stored, r.rejected, r.unchanged) == (0, 1, 0)
    assert admin.execute("select reason_code from ingest_reject").fetchone()[0] == "CHANGED_AFTER_LOAD"
    assert admin.execute("select raw ->> 'address' from source_record").fetchone()[0] == "12 Smith St"


@pytest.mark.parametrize("source,missing", [("crm", "email"), ("listing", "latitude"), ("sales", "buyer_phone"), ("crm", "contact_ref")])
def test_a_feed_missing_a_mapped_column_is_refused_whole_and_names_the_column(conn, admin, tmp_path, source, missing):
    rows = {"crm": [h.crm("C1", "12 Smith St")], "listing": [h.listing("L1", "12 Smith St")], "sales": [h.sale("S1", "12 Smith St, Fakeville NSW 2000")]}[source]
    rows = [{k: v for k, v in rows[0].items() if k != missing}]
    folder = h.write_feeds(tmp_path / "f", **{{"crm": "crm_rows", "listing": "listing_rows", "sales": "sales_rows"}[source]: rows})
    with pytest.raises(FeedRefused, match=missing):
        ingest(conn, folder / SOURCES[source].file, source)
    assert admin.execute("select (select count(*) from source_record) + (select count(*) from source_load) + (select count(*) from ingest_reject)").fetchone()[0] == 0


def test_a_file_that_is_not_utf8_is_refused(conn, tmp_path):
    p = tmp_path / "crm_export.csv"
    p.write_bytes("contact_ref,full_name,email,phone,address,suburb,state,postcode\nC1,Caf\xe9,,,12 Smith St,X,NSW,2000\n".encode("latin-1"))
    with pytest.raises(FeedRefused, match="not UTF-8"):
        ingest(conn, p, "crm")


def test_a_byte_order_mark_is_ignored(conn, admin, tmp_path):
    p = tmp_path / "crm_export.csv"
    p.write_bytes(b"\xef\xbb\xbf" + b"contact_ref,full_name,email,phone,address,suburb,state,postcode\nC1,,,,12 Smith St,Fakeville,NSW,2000\n")
    assert ingest(conn, p, "crm").stored == 1


def test_the_load_time_is_recorded_and_can_be_set(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    ingest(conn, folder / "crm_export.csv", "crm", now=when)
    assert admin.execute("select loaded_at from source_load").fetchone()[0] == when
    assert admin.execute("select loaded_at from source_record").fetchone()[0] == when


def test_the_app_role_cannot_change_the_raw_row_or_its_hash(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    ingest(conn, folder / "crm_export.csv", "crm")
    for column in ("raw", "raw_sha256", "source_ref", "record_id"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with agency_tx(conn, AGENCY) as cur:
                cur.execute(f"update source_record set {column} = {column} where agency_id = %s", (AGENCY,))
    with agency_tx(conn, AGENCY) as cur:                      # control: a normalised column can be updated
        cur.execute("update source_record set norm_status = 'ok' where agency_id = %s", (AGENCY,))
        assert cur.rowcount == 1


def test_normalise_fills_the_columns_and_keeps_the_rules(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "Unit 5, 12 smith st.", name="Kate  Smith", email=" Kate@Example.COM ", phone="(02) 5550 1234"),
                                                      h.crm("C2", "Flat 5")])
    ingest(conn, folder / "crm_export.csv", "crm")
    with agency_tx(conn, AGENCY) as cur:
        assert normalise_pending(cur, AGENCY) == 2
    row = admin.execute("""select norm_status, unit, number_first, street_key, locality_key, state, postcode, canon_key, contact_name, contact_email, contact_phone, norm_rules, contact_rules
                           from source_record where record_id = 'crm:C1'""").fetchone()
    assert row[:11] == ("ok", "5", "12", "SMITH STREET", "FAKEVILLE", "NSW", "2000", "|5||12|SMITH STREET|FAKEVILLE|NSW", "Kate Smith", "kate@example.com", "+61255501234")
    assert {"CASE_FOLDED", "PUNCT_STRIPPED", "UNIT_MARKER_WORD", "STREET_TYPE_EXPANDED"} <= set(row[11]) and {"EMAIL_TRIMMED", "EMAIL_LOWERCASED", "PHONE_E164"} <= set(row[12])
    assert admin.execute("select norm_status from source_record where record_id = 'crm:C2'").fetchone()[0] == "unparseable"
    with agency_tx(conn, AGENCY) as cur:
        assert normalise_pending(cur, AGENCY) == 0


def test_listing_coordinates_become_a_geography_and_bad_ones_are_dropped_with_a_rule(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", listing_rows=[h.listing("L1", "12 Smith St", lat="-33.8", lon="151.0"), h.listing("L2", "12 Smith St", lat="33.8", lon="151.0"),
                                                          h.listing("L3", "12 Smith St", lat="abc", lon="151.0"), h.listing("L4", "12 Smith St")])
    ingest(conn, folder / "listing_feed.csv", "listing")
    with agency_tx(conn, AGENCY) as cur:
        normalise_pending(cur, AGENCY)
    got = {r[0]: (r[1], r[2]) for r in admin.execute("select source_ref, geog is not null, 'COORDS_OUT_OF_RANGE' = any(norm_rules) from source_record").fetchall()}
    assert got == {"L1": (True, False), "L2": (False, True), "L3": (False, True), "L4": (False, False)}


def test_read_feed_gives_sha_and_numbered_rows(tmp_path):
    folder = h.write_feeds(tmp_path / "f", sales_rows=[h.sale("S1", "12 Smith St, Fakeville NSW 2000"), h.sale("S2", "5 Rose Rd, Fakeville NSW 2000")])
    sha, rows = read_feed(folder / "sales_history.csv", "sales")
    assert len(sha) == 64 and [r.line_no for r in rows] == [2, 3] and rows[0].raw["sale_ref"] == "S1" and rows[0].extra == ()
    assert row_sha({"a": "1", "b": "2"}) == row_sha({"b": "2", "a": "1"}) != row_sha({"a": "1", "b": "3"})


def test_a_lot_only_address_is_usable_and_a_half_given_coordinate_pair_is_dropped(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", listing_rows=[h.listing("L1", "Lot 7 Smith Rd"), h.listing("L2", "12 Smith St", lat="-33.8", lon=""), h.listing("L3", "12 Smith St", lat="", lon="151.0"),
                                                          h.listing("L4", "12 Smith St")])
    ingest(conn, folder / "listing_feed.csv", "listing")
    with agency_tx(conn, AGENCY) as cur:
        normalise_pending(cur, AGENCY)
    got = {r[0]: r[1:] for r in admin.execute("select source_ref, norm_status, geog is not null, 'COORDS_OUT_OF_RANGE' = any(norm_rules) from source_record").fetchall()}
    assert got == {"L1": ("ok", False, False), "L2": ("ok", False, True), "L3": ("ok", False, True), "L4": ("ok", False, False)}


def test_the_load_row_keeps_the_counts_and_the_agency_is_registered(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("", "5 Rose Rd"), h.crm("C3", "7 Elm Ct")])
    r = ingest(conn, folder / "crm_export.csv", "crm")
    assert admin.execute("select row_count, stored, rejected, unchanged, file_name, source from source_load").fetchall() == [(3, 2, 1, 0, "crm_export.csv", "crm")]
    assert admin.execute("select agency_id from agency").fetchall() == [(AGENCY,)] and (r.row_count, r.stored, r.rejected, r.unchanged) == (3, 2, 1, 0)


def test_a_skipped_file_reports_zero_counts(conn, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "12 Smith St")])
    ingest(conn, folder / "crm_export.csv", "crm")
    r = ingest(conn, folder / "crm_export.csv", "crm")
    assert (r.status, r.row_count, r.stored, r.rejected, r.unchanged) == ("skipped", 0, 0, 0, 0)


def test_a_repeated_unchanged_row_in_a_later_file_is_unchanged_once_and_a_duplicate_after(conn, admin, tmp_path):
    a = h.write_feeds(tmp_path / "a", crm_rows=[h.crm("C1", "12 Smith St")])
    b = h.write_feeds(tmp_path / "b", crm_rows=[h.crm("C1", "12 Smith St"), h.crm("C1", "12 Smith St"), h.crm("C2", "5 Rose Rd")])
    ingest(conn, a / "crm_export.csv", "crm")
    r = ingest(conn, b / "crm_export.csv", "crm")
    assert (r.row_count, r.stored, r.rejected, r.unchanged) == (3, 1, 1, 1)
    assert admin.execute("select reason_code from ingest_reject").fetchall() == [("DUPLICATE_REF",)]


def test_the_row_hash_is_the_sha256_of_sorted_json_that_keeps_unicode():
    import hashlib
    from dataclasses import FrozenInstanceError
    assert row_sha({"b": "é", "a": "1"}) == hashlib.sha256('{"a": "1", "b": "é"}'.encode("utf-8")).hexdigest()
    with pytest.raises(FrozenInstanceError):
        SOURCES["crm"].file = "x"


def test_a_street_with_no_number_and_a_number_with_no_street_are_both_unparseable(conn, admin, tmp_path):
    folder = h.write_feeds(tmp_path / "f", crm_rows=[h.crm("C1", "Smith St"), h.crm("C2", "12"), h.crm("C3", "Lot 7 Smith Rd"), h.crm("C4", "12 Smith St")])
    ingest(conn, folder / "crm_export.csv", "crm")
    with agency_tx(conn, AGENCY) as cur:
        normalise_pending(cur, AGENCY)
    assert dict(admin.execute("select source_ref, norm_status from source_record").fetchall()) == {"C1": "unparseable", "C2": "unparseable", "C3": "ok", "C4": "ok"}


# ---- the one reader of a feed: EXTRA_CELLS, blank lines, short rows ---------------------------------------------------------------------------------

crm_text = h.crm_text


def test_a_row_with_more_cells_than_the_header_is_rejected_and_kept_in_the_reject_table(conn, admin, tmp_path):
    """P5-4: the overflow used to be dropped with no reject row."""
    folder = h.write_crm_text(tmp_path / "f", crm_text(h.crm_line("C1"), h.crm_line("C2", "5 Rose Rd") + ",STRAY,CELLS", h.crm_line("C3", "7 Elm Ct")))
    r = ingest(conn, folder / "crm_export.csv", "crm")
    assert (r.row_count, r.stored, r.rejected) == (3, 2, 1)
    assert admin.execute("select source_ref from source_record order by line_no").fetchall() == [("C1",), ("C3",)]
    code, reason, raw = admin.execute("select reason_code, reason, raw from ingest_reject").fetchone()
    assert code == "EXTRA_CELLS" and "2 cell(s) beyond the 9 columns" in reason
    assert raw["contact_ref"] == "C2" and raw["address"] == "5 Rose Rd" and raw["(extra cells)"] == ["STRAY", "CELLS"]
    assert admin.execute("select row_count, stored, rejected from source_load").fetchone() == (3, 2, 1)


def test_an_extra_cell_row_does_not_use_up_its_reference(conn, admin, tmp_path):
    """A row whose cells are misaligned must not make a later good row with the same reference a DUPLICATE_REF."""
    folder = h.write_crm_text(tmp_path / "f", crm_text(h.crm_line("C1") + ",X", h.crm_line("C1", "99 Other St")))
    ingest(conn, folder / "crm_export.csv", "crm")
    assert admin.execute("select reason_code from ingest_reject").fetchall() == [("EXTRA_CELLS",)]
    assert admin.execute("select raw ->> 'address' from source_record").fetchone()[0] == "99 Other St"


def test_class_the_shapes_of_a_feed_file_the_reader_must_settle_one_way(conn, admin, tmp_path):
    """CLASS-TABLE: each shape of file gives (text, stored refs, reject reasons in line order, row count). Blank lines are not rows, a short row is stored with empty cells, a repeated reference keeps the
    first, blank overflow cells are not data, a quoted cell may span lines, CRLF reads the same."""
    ok1, ok2 = h.crm_line("C1"), h.crm_line("C2", "5 Rose Rd")
    table = {
        "plain": (crm_text(ok1, ok2), ["C1", "C2"], [], 2),
        "blank line between rows": (crm_text(ok1, "", ok2), ["C1", "C2"], [], 2),
        "blank lines at the end": (crm_text(ok1, ok2) + "\n\n", ["C1", "C2"], [], 2),
        "short row": (crm_text(ok1, "C2,Bob,,,5 Rose Rd"), ["C1", "C2"], [], 2),
        "duplicate reference": (crm_text(ok1, h.crm_line("C1", "99 Other St")), ["C1"], ["DUPLICATE_REF"], 2),
        "extra cell with data": (crm_text(ok1, ok2 + ",X"), ["C1"], ["EXTRA_CELLS"], 2),
        "extra cells that are blank": (crm_text(ok1 + ",", ok2 + ",,"), ["C1", "C2"], [], 2),
        "a quoted cell spanning two lines": (crm_text(ok1, h.crm_line("C2", '"5 Rose\nRd"')), ["C1", "C2"], [], 2),
        "windows line ends": (crm_text(ok1, ok2, eol="\r\n"), ["C1", "C2"], [], 2),
        "a whitespace only reference": (crm_text(ok1, h.crm_line("  ", "5 Rose Rd")), ["C1"], ["MISSING_REF"], 2),
    }
    for name, (text, stored, rejects, count) in table.items():
        db.wipe(db.default_admin_dsn())
        folder = h.write_crm_text(tmp_path / name.replace(" ", "_"), text)
        r = ingest(conn, folder / "crm_export.csv", "crm")
        got = [x[0] for x in admin.execute("select source_ref from source_record order by line_no").fetchall()]
        got_rejects = [x[0] for x in admin.execute("select reason_code from ingest_reject order by line_no").fetchall()]
        assert (got, got_rejects, r.row_count) == (stored, rejects, count), name
        assert (r.stored, r.rejected) == (len(stored), len(rejects)), name


def test_line_numbers_are_the_lines_of_the_file_even_with_blank_lines(tmp_path):
    folder = h.write_crm_text(tmp_path / "f", crm_text(h.crm_line("C1"), "", h.crm_line("C2")))
    _, rows = read_feed(folder / "crm_export.csv", "crm")
    assert [(r.line_no, r.raw["contact_ref"]) for r in rows] == [(2, "C1"), (4, "C2")]


def test_a_short_row_is_filled_with_empty_cells_and_an_empty_file_is_refused(tmp_path):
    folder = h.write_crm_text(tmp_path / "f", crm_text("C1,Bob"))
    _, rows = read_feed(folder / "crm_export.csv", "crm")
    assert rows[0].raw == {**{c: "" for c in h.CRM_HEADER.split(",")}, "contact_ref": "C1", "full_name": "Bob"}
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "crm_export.csv").write_bytes(b"")
    with pytest.raises(FeedRefused, match="needs column"):
        read_feed(tmp_path / "empty" / "crm_export.csv", "crm")


def test_the_feed_reader_is_the_only_place_the_package_reads_a_feed_file():
    """CLASS: `csv` is imported by the feed reader and by the answer key loader, nowhere else (reconcile once had its own reader and disagreed with the loader)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "parcelkey"
    users = sorted(p.name for p in root.glob("*.py") if "import csv" in p.read_text(encoding="utf-8"))
    assert users == ["evaluate.py", "ingest.py"]
