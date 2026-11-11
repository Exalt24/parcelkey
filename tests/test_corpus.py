"""Replay of the review corpus: one JSON line per address, with what a reader of the lab expects the normaliser to do with it.

`expect pass` means the address is handled as an ordinary property address (a street and a number, or a lot, and no mailbox). `expect fail` means it is refused or set aside with a stated
reason: UNPARSEABLE (no street, or neither a number nor a lot) or POSTAL_ADDRESS (a PO Box, GPO Box, Locked Bag or Private Bag).

The corpus lives with the research that produced it, at CORPUS_PATH. This repository keeps a copy so a clone can replay it; one test shows the two are the same when both exist.
A case is a claim: DISPUTES holds the three the code does not follow, each with the reason and what the code does instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
import seed as gen
from conftest import _STATE
from helpers import crm, decisions, listing, sale, write_feeds
from parcelkey import db, gnaf
from parcelkey.freshness import parse_max_age
from parcelkey.master import property_id_for_derived
from parcelkey.normalise import normalise_address, normalise_email, normalise_phone
from parcelkey.pipeline import run_agency
from parcelkey.records import normalise_record

CORPUS_PATH = Path("C:/Projects/Professional/Operations/docs/research/2026-11-10-parcelkey-property-resolution-proof/evidence/output/corpus_parcelkey_1.jsonl")
REPO_COPY = Path(__file__).resolve().parent / "corpus" / "addresses.jsonl"
REASONS = {"UNPARSEABLE", "POSTAL_ADDRESS"}

# (line, suburb, state, postcode) -> (what the code does, why the case is not followed)
DISPUTES = {
    ("Box Hill Road 12", "Fakeville", "VIC", "3128"): ("fail", "the number comes after the street, a form the lab has never read (aa03327 refuses it too); the real claim, that a street starting with Box is not a mailbox, is held by 14 Box Hill Rd"),
    ("12 Smith St", "Fakeville", "XX", "2000"): ("pass", "a state that is not one is left empty with STATE_MISSING and the record still matches on suburb and postcode; refusing it would turn away a row that links today"),
    ("12 Smith St", "Fakeville", "NSW", "20000"): ("pass", "a postcode of the wrong shape is dropped with POSTCODE_BAD_SHAPE and the record still matches on suburb; test_postcode_shape pins it"),
}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def outcome(inp: dict) -> tuple[str, str]:
    """('pass', '') for an ordinary property address, ('fail', reason) for one that is refused or set aside."""
    a = normalise_address(inp["line"], inp["suburb"], inp["state"], inp["postcode"])
    if a.street_name and (a.number_first or a.lot) and "POSTAL_ADDRESS" not in a.rules:
        return "pass", ""
    return "fail", "POSTAL_ADDRESS" if "POSTAL_ADDRESS" in a.rules else "UNPARSEABLE"


CASES = load(REPO_COPY)


def key(case: dict) -> tuple:
    i = case["input"]
    return i["line"], i["suburb"], i["state"], i["postcode"]


@pytest.mark.parametrize("case", CASES, ids=[f"{c['expect']}: {c['input']['line']}" for c in CASES])
def test_replay_the_corpus(case):
    got, reason = outcome(case["input"])
    if key(case) in DISPUTES:
        assert got == DISPUTES[key(case)][0] and got != case["expect"], DISPUTES[key(case)][1]          # the code does what the dispute says, which is the opposite of the case
        return
    assert got == case["expect"], (case["why"], got, reason)
    if got == "fail":
        assert reason in REASONS


def test_the_corpus_is_well_formed_and_big_enough():
    assert len(CASES) >= 60
    assert sum(c["expect"] == "pass" for c in CASES) >= 30 and sum(c["expect"] == "fail" for c in CASES) >= 30
    undisputed = [c for c in CASES if key(c) not in DISPUTES]
    assert sum(c["expect"] == "pass" for c in undisputed) >= 30 and sum(c["expect"] == "fail" for c in undisputed) >= 30
    for c in CASES:
        assert set(c) - {"promise"} == {"input", "expect", "why", "source"} and set(c["input"]) == {"line", "suburb", "state", "postcode"}
        assert "promise" not in c or (c["source"] == "invented" and len(c["promise"]) >= 10)       # an invented case says what the product promises for it
        assert c["expect"] in ("pass", "fail") and c["source"] in ("real", "invented") and len(c["why"]) >= 10
    assert len({key(c) for c in CASES}) == len(CASES)
    assert set(DISPUTES) <= {key(c) for c in CASES}


def test_the_research_folder_copy_and_the_repository_copy_are_the_same_file():
    if not CORPUS_PATH.is_file():
        pytest.skip(f"the research folder is not on this machine ({CORPUS_PATH})")
    assert load(CORPUS_PATH) == CASES
    for case in load(CORPUS_PATH):
        got, _ = outcome(case["input"])
        assert got == case["expect"] or key(case) in DISPUTES


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
# The second review corpus (a blind adversary's 46 cases, `tests/corpus/blind_round_2.jsonl`). Each case is replayed the way the product runs it, not as a function call alone:
# an address case goes through the real pipeline (feed file, ingest, normalise, block, match) against the generated G-NAF world of seed 7, the reference every case was written
# for. `expect pass` means the record links to the property named in `truth_pid` (or, for the other functions, gives a usable value); `expect fail` means it is set aside or
# refused. A case the code does not follow is named in BLIND_DISPUTES with what the code does instead and why; no case is edited, flipped or removed.
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------

BLIND_CORPUS_PATH = Path("C:/Projects/Professional/Operations/docs/research/2026-11-10-parcelkey-property-resolution-proof/evidence/output/corpus_parcelkey_blind_2.jsonl")
BLIND_REPO_COPY = Path(__file__).resolve().parent / "corpus" / "blind_round_2.jsonl"
BLIND_AGENCY = "agency-a"
EXTRA_4A = "GANSW_EXTRA_4A"

# corpus line number -> (what the code does: "pass" or "fail", why the case is not followed)
BLIND_DISPUTES = {
    26: ("fail", "KNOWN_LIMIT F11: a one letter typo in the street of a lot only address with no number makes a new derived property and no wrong link; a typo is matched only with a house number, and the README says so"),
}

BLIND_CASES = load(BLIND_REPO_COPY)


def _feed_row(source: str, ref: str, raw: dict) -> dict:
    if source == "crm":
        return crm(ref, raw["address"], raw["suburb"], raw["state"], raw["postcode"])
    if source == "listing":
        return listing(ref, raw["address"], raw["suburb"], raw["state"], raw["postcode"], raw.get("latitude", ""), raw.get("longitude", ""))
    return sale(ref, raw["address_full"])


def _label(i: int, c: dict) -> str:
    inp = c["input"]
    what = (inp.get("raw") or {}).get("address") or (inp.get("raw") or {}).get("address_full") or str(inp.get("arg") or inp.get("a"))
    return f"blind {i}: {c['expect']}: {what[:50]}"


@pytest.fixture(scope="module")
def blind_decisions(admin_dsn, tmp_path_factory):
    """case line number -> ('link', rule, gnaf pid or canonical key) or ('quarantine', reason), from the real pipeline on the seed 7 world plus the 4A row case 19 and 20 name."""
    world = tmp_path_factory.mktemp("blind_world")
    gen.generate(world, seed=7, addresses=4000)
    db.wipe(admin_dsn, reference=True)
    with psycopg.connect(admin_dsn) as c:
        c.execute(f"set search_path = {db.SCHEMA}, public")
        gnaf.load_gnaf(c, world / "gnaf")
        gnaf.build_reference(c)
        # case 19 and 20 ask for a G-NAF address 4A Saint Ingowick Street that the seed does not hold: a copy of the house at number 4 with the suffix letter
        c.execute("""INSERT INTO gnaf_address (address_detail_pid, unit, level, building, lot, number_first, number_last, street_name, street_type, street_key, locality_key, state, postcode,
                                               canon_key, principal_pid, display_address, geog)
                     SELECT %s, unit, level, building, lot, '4A', number_last, street_name, street_type, street_key, locality_key, state, postcode,
                            replace(canon_key, '|4|', '|4A|'), NULL, replace(display_address, ' 4 ', ' 4A '), geog FROM gnaf_address WHERE address_detail_pid = 'GANSW900000108'""", (EXTRA_4A,))
    _STATE["reference"] = "blind"          # the shared fixtures reload their own reference when they see this
    rows: dict[str, list[dict]] = {"crm": [], "listing": [], "sales": []}
    for n, case in enumerate(BLIND_CASES, 1):
        inp = case["input"]
        if inp["fn"] == "normalise_record+decide":
            rows[inp["source"]].append(_feed_row(inp["source"], f"B{n:02d}", inp["raw"]))
    feeds = write_feeds(world / "feeds", crm_rows=rows["crm"], listing_rows=rows["listing"], sales_rows=rows["sales"])
    with db.connect() as conn:
        run_agency(conn, BLIND_AGENCY, feeds, new_agency=True)
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f"set search_path = {db.SCHEMA}, public")
        found = decisions(admin, BLIND_AGENCY)
    return {n: found[f"{case['input']['source']}:B{n:02d}"] for n, case in enumerate(BLIND_CASES, 1) if case["input"]["fn"] == "normalise_record+decide"}


def blind_outcome(n: int, case: dict, found: dict) -> tuple[str, str]:
    """('pass' | 'fail', what happened) for one case of the second corpus."""
    inp = case["input"]
    fn = inp["fn"]
    if fn == "normalise_record+decide":
        d = found[n]
        if d[0] == "link" and d[2] == inp.get("truth_pid"):
            return "pass", f"linked to {d[2]} by {d[1]}"
        return "fail", f"{d[0]} {d[1]}" + ("" if d[0] == "quarantine" else f" {d[2]}")
    if fn.startswith("normalise_record(...)"):
        a, b = (normalise_record(inp[k]["source"], inp[k]["raw"])["addr"] for k in ("a", "b"))
        ids = property_id_for_derived(a.canon_key), property_id_for_derived(b.canon_key)
        return ("pass" if ids[0] == ids[1] else "fail"), f"{a.canon_key} and {b.canon_key}"
    if fn == "normalise.normalise_phone":
        value = normalise_phone(inp["arg"])[0]
        return ("pass" if value else "fail"), value
    if fn == "normalise.normalise_email":
        value = normalise_email(inp["arg"])[0]
        return ("pass" if value else "fail"), value
    if fn == "freshness.parse_max_age":
        try:
            parse_max_age(inp["arg"])
        except ValueError as exc:
            return "fail", str(exc)
        return "pass", "accepted"
    raise AssertionError(f"unknown function {fn}")


@pytest.mark.parametrize("n", range(1, len(BLIND_CASES) + 1), ids=[_label(i, c) for i, c in enumerate(BLIND_CASES, 1)])
def test_replay_the_second_corpus(n, blind_decisions):
    case = BLIND_CASES[n - 1]
    got, what = blind_outcome(n, case, blind_decisions)
    if n in BLIND_DISPUTES:
        assert got == BLIND_DISPUTES[n][0] and got != case["expect"], (BLIND_DISPUTES[n][1], what)
        return
    assert got == case["expect"], (case["why"], what)


def test_the_second_corpus_is_well_formed():
    assert len(BLIND_CASES) == 46
    assert sum(c["expect"] == "pass" for c in BLIND_CASES) >= 30 and sum(c["expect"] == "fail" for c in BLIND_CASES) >= 8
    for c in BLIND_CASES:
        assert {"input", "expect", "why", "source"} <= set(c) <= {"input", "expect", "why", "source", "observed", "promise"}
        assert c["expect"] in ("pass", "fail") and c["source"] in ("real", "invented") and len(c["why"]) >= 10
    assert set(BLIND_DISPUTES) <= set(range(1, len(BLIND_CASES) + 1))


def test_the_second_corpus_research_copy_and_repository_copy_are_the_same_file():
    if not BLIND_CORPUS_PATH.is_file():
        pytest.skip(f"the research folder is not on this machine ({BLIND_CORPUS_PATH})")
    assert load(BLIND_CORPUS_PATH) == BLIND_CASES
