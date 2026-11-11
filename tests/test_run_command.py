"""The run command and the pipeline around the feeds: each feed is ingested on its own (a refused one is reported and the rest still run), and an agency id that has never been loaded is refused
unless the run says it is new (so a typo cannot start a second agency or be reported on as an empty, fine one)."""
from __future__ import annotations

import pytest

import helpers as h
from conftest import AGENCY, OTHER
from parcelkey import db
from parcelkey.ingest import SOURCES
from parcelkey.pipeline import Refusal, run_agency
from test_quality_cli import REFERENCE, cli

pytestmark = pytest.mark.db

GOOD = {
    "crm": [h.crm("C1", "12 Smith St", name="Kate", email="kate@example.com")],
    "listing": [h.listing("L1", "14 Smith St")],
    "sales": [h.sale("S1", "12 Smith St, Fakeville NSW 2000", name="K", email="kate@example.com")],
}
ARG = {"crm": "crm_rows", "listing": "listing_rows", "sales": "sales_rows"}
SPOILS = {
    "a missing column": (b"contact_ref,address\nC1,12 Smith St\n", "needs column"),
    "text that is not UTF-8": ("contact_ref,full_name,email,phone,address,suburb,state,postcode\nC1,Caf\xe9,,,12 Smith St,X,NSW,2000\n".encode("latin-1"), "not UTF-8"),
    "an empty file": (b"", "needs column"),
}


def feeds(tmp_path, spoil=None, source=None):
    """A folder with all three feeds, one of them (source) replaced by the spoiled bytes."""
    folder = h.write_feeds(tmp_path / "feeds", **{ARG[s]: rows for s, rows in GOOD.items()})
    if spoil is not None:
        (folder / SOURCES[source].file).write_bytes(SPOILS[spoil][0])
    return folder


@pytest.mark.parametrize("source", ["crm", "listing", "sales"])
@pytest.mark.parametrize("spoil", sorted(SPOILS))
def test_class_one_refused_feed_never_stops_the_others(hand, conn, admin, tmp_path, source, spoil):
    """CLASS-TABLE: P5-8, any one of the three feeds spoiled in any of the ways a file is refused, in any position: the other two are stored, the later stages run, the refusal is reported with its reason."""
    hand(REFERENCE)
    folder = feeds(tmp_path, spoil, source)
    report = run_agency(conn, AGENCY, folder, new_agency=True)
    others = sorted(s for s in GOOD if s != source)
    assert sorted(i.source for i in report.ingested) == others and all(i.status == "loaded" for i in report.ingested)
    assert [(r.source, r.file) for r in report.refused] == [(source, SOURCES[source].file)] and SPOILS[spoil][1] in report.refused[0].reason and isinstance(report.refused[0], Refusal)
    assert admin.execute("select source from source_load order by source").fetchall() == [(s,) for s in others]
    assert report.normalised == 2 and sum(report.match.linked.values()) + sum(report.match.quarantined.values()) == 2
    assert admin.execute("select count(*) from source_record").fetchone()[0] == 2 and admin.execute("select count(*) from ingest_reject").fetchone()[0] == 0


def test_every_feed_refused_stores_nothing_starts_no_agency_and_still_finishes(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    folder = feeds(tmp_path)
    for source in GOOD:
        (folder / SOURCES[source].file).write_bytes(SPOILS["a missing column"][0])
    report = run_agency(conn, AGENCY, folder, new_agency=True)
    assert [r.source for r in report.refused] == ["crm", "listing", "sales"] and report.ingested == [] and report.normalised == 0
    assert admin.execute("select (select count(*) from agency) + (select count(*) from source_load) + (select count(*) from source_record)").fetchone()[0] == 0


def test_a_run_with_no_refusal_reports_none(hand, conn, tmp_path):
    hand(REFERENCE)
    assert run_agency(conn, AGENCY, feeds(tmp_path), new_agency=True).refused == []


def test_the_command_line_runs_the_good_feeds_and_exits_2_after_everything_ran(hand, clean, admin, tmp_path):
    hand(REFERENCE)
    folder = feeds(tmp_path, "a missing column", "crm")
    p = cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency")
    assert p.returncode == 2
    assert "REFUSED: crm_export.csv: the crm feed needs column(s)" in p.stderr
    assert "ingest     listing" in p.stdout and "ingest     sales" in p.stdout and "ingest     crm" not in p.stdout and "timings" in p.stdout
    assert admin.execute("select source from source_load order by source").fetchall() == [("listing",), ("sales",)]
    again = cli("run", "--agency", AGENCY, "--feeds", str(folder))
    assert again.returncode == 2 and "skipped" in again.stdout                                    # the next run says the same and writes nothing new


def test_the_command_line_exits_0_when_no_feed_is_refused(hand, clean, tmp_path):
    hand(REFERENCE)
    assert cli("run", "--agency", AGENCY, "--feeds", str(feeds(tmp_path)), "--new-agency").returncode == 0


# ---- an agency id that has never been loaded ---------------------------------------------------------------------------------------------------

def test_an_unknown_agency_is_refused_by_the_pipeline_unless_it_is_new(hand, conn, admin, tmp_path):
    hand(REFERENCE)
    folder = feeds(tmp_path)
    for kwargs in ({}, {"new_agency": False}):
        with pytest.raises(db.UnknownAgency, match="no agency 'agnecy-a' has been loaded.*--new-agency"):
            run_agency(conn, "agnecy-a", folder, **kwargs)
    with pytest.raises(db.UnknownAgency):
        run_agency(conn, "agnecy-a", None)                                                      # the stages after ingest are refused as well
    assert admin.execute("select count(*) from agency").fetchone()[0] == 0
    run_agency(conn, AGENCY, folder, new_agency=True)
    assert run_agency(conn, AGENCY, folder).ingested[0].status == "skipped"                     # a started agency needs no flag
    with pytest.raises(db.UnknownAgency):                                                       # another agency's existence does not count
        run_agency(conn, OTHER, folder)


def test_class_every_command_that_names_an_agency_refuses_one_that_was_never_loaded(hand, clean, admin, world_dir, tmp_path):
    """CLASS-TABLE: P5-13, run, reconcile, freshness, quality, quarantine, explain and evaluate with a mistyped agency id exit 2, say so, and start nothing. `run --new-agency` is the one way to start an agency."""
    hand(REFERENCE)
    folder = feeds(tmp_path)
    cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency", check=True)
    typo = "agnecy-a"
    commands = {
        "run": ["run", "--agency", typo, "--feeds", str(folder)],
        "reconcile": ["reconcile", "--agency", typo],
        "reconcile with feeds": ["reconcile", "--agency", typo, "--feeds", str(folder)],
        "freshness": ["freshness", "--agency", typo],
        "quality": ["quality", "--agency", typo],
        "quarantine": ["quarantine", "--agency", typo],
        "explain": ["explain", "--agency", typo, "crm:C1"],
        "evaluate": ["evaluate", "--agency", typo, "--key", str(world_dir / "answer_key.csv"), "--truth", str(world_dir / "truth_properties.csv")],
    }
    for name, argv in commands.items():
        p = cli(*argv)
        assert p.returncode == 2, (name, p.stdout, p.stderr)
        assert f"REFUSED: no agency '{typo}' has been loaded: check the spelling" in p.stderr, name
        assert (", or pass --new-agency to start it with this run" in p.stderr) == (name == "run"), name
        assert admin.execute("select agency_id from agency order by 1").fetchall() == [(AGENCY,)], name
    started = cli("run", "--agency", "agency-new", "--feeds", str(folder), "--new-agency")
    assert started.returncode == 0 and admin.execute("select agency_id from agency order by 1").fetchall() == [(AGENCY,), ("agency-new",)]
    assert cli("reconcile", "--agency", "agency-new").returncode == 0


def test_a_known_agency_is_still_served_by_every_command_without_a_flag(hand, clean, world_dir, tmp_path):
    hand(REFERENCE)
    folder = feeds(tmp_path)
    cli("run", "--agency", AGENCY, "--feeds", str(folder), "--new-agency", check=True)
    for argv in (["reconcile", "--agency", AGENCY], ["quality", "--agency", AGENCY], ["quarantine", "--agency", AGENCY], ["explain", "--agency", AGENCY, "crm:C1"]):
        assert cli(*argv).returncode == 0, argv
    assert cli("freshness", "--agency", AGENCY, "--max-age", "crm=24", "--max-age", "listing=24", "--max-age", "sales=24").returncode in (0, 1)
