"""Replay of the review corpus: one JSON line per address, with what a reader of the lab expects the normaliser to do with it.

`expect pass` means the address is handled as an ordinary property address (a street and a number, or a lot, and no mailbox). `expect fail` means it is refused or set aside with a stated
reason: UNPARSEABLE (no street, or neither a number nor a lot) or POSTAL_ADDRESS (a PO Box, GPO Box, Locked Bag or Private Bag).

The corpus lives with the research that produced it, at CORPUS_PATH. This repository keeps a copy so a clone can replay it; one test shows the two are the same when both exist.
A case is a claim: DISPUTES holds the three the code does not follow, each with the reason and what the code does instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from parcelkey.normalise import normalise_address

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
        assert set(c) == {"input", "expect", "why", "source"} and set(c["input"]) == {"line", "suburb", "state", "postcode"}
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
