"""Claims the README makes that a test holds in place, so a later edit cannot quietly drop a limit from the place the claim is made (round 5 of the review).

A limit that sits only at the bottom of the README is easy to miss; the first two stay in the headline, next to the figures and the claims they limit, and every other row is one
disclosure with a harmful input behind it (the repro files of the review name each)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def section(title: str) -> str:
    """The text of one `## ` section of the README, without its heading."""
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert m, f"README has no section {title!r}"
    return m.group(1)


HEADLINE = README.split("## What it is and why")[0]
LIMITS = section("Limits")

# id -> (where it must be said, the words that must be there). Each is one disclosure of the fifth review.
DISCLOSURES = {
    "P5-5 a flat G-NAF does not list": (HEADLINE, ["flat that G-NAF does not list", "UNIT_CONFLICT", "no master id until a later release lists it", "never lets a different unit match"]),
    "P5-7 a derived property is shared": (HEADLINE, ["agency's own rows", "derived id", "display address and the point of the first record that made it", "shows every agency"]),
    "P5-9 a misspelt suburb": (LIMITS, ["no suburb typos", "a second derived id"]),
    "P5-14 a lot only record and a flat": (LIMITS, ["lot only record", "flat", "LOT_GEOCODE"]),
    "P5-15 other mailbox forms": (LIMITS, ["Post Office Box", "RMB", "UNPARSEABLE", "read as that street address"]),
    "P5-16 freshness wants all three feeds": (LIMITS, ["freshness", "all three feeds", "MISSING"]),
    "P5-6 a landline never merges": (LIMITS, ["landline", "never merges"]),
    "P5-3 a re-sent feed": (LIMITS, ["CHANGED_AFTER_LOAD", "FILE_NOT_APPLIED"]),
    "P5-2 a G-NAF reload": (LIMITS, ["reload", "retired", "left as they are"]),
    "P5-10 the first form names the id": (LIMITS, ["suffix", "first form seen"]),
}


@pytest.mark.parametrize("name", sorted(DISCLOSURES))
def test_class_each_disclosure_of_the_fifth_review_stays_where_the_claim_is_made(name):
    """CLASS-TABLE: every limit the review found that is stated rather than fixed is in the README at the place the table says."""
    where, words = DISCLOSURES[name]
    for word in words:
        assert word in where, f"{name}: the README no longer says {word!r} where it must"


def test_the_headline_limits_sit_after_the_figures_they_limit_and_before_the_first_section():
    assert 0 < HEADLINE.index("precision") < HEADLINE.index("flat that G-NAF does not list") < len(HEADLINE)
    assert HEADLINE.index("agency's own rows") < len(HEADLINE) and "## What it is and why" not in HEADLINE


def test_run_it_says_which_database_each_command_uses_and_starts_the_agency():
    run_it = section("Run it")
    for word in ("`parcelkey` is the demo database", "`parcelkey_test`", "`parcelkey_measure`", "refuse to run against `parcelkey`", "--new-agency"):
        assert word in run_it, word
    assert "python -m parcelkey run --agency agency-a --feeds out/seed/agency-a --new-agency" in run_it
    assert "python -m parcelkey run --agency agency-b --feeds out/seed/agency-b --new-agency" in run_it
    tests = section("Tests")
    assert "parcelkey_test" in tests and "never the demo one" in tests


def test_the_readme_does_not_still_say_a_phone_merges_contacts():
    stale = ["E.164 phone inside one agency", "email or phone, evidence kept", "A phone shared by two real people merges them", "merges duplicate contacts only on a shared email or phone"]
    for text in stale:
        assert text not in README, text
    assert "email or mobile" in README and "landline never merges" in README


def test_the_commands_the_readme_runs_exist_and_accept_help(capsys):
    from parcelkey import cli
    commands = sorted(set(re.findall(r"python -m parcelkey ([a-z-]+)", README)))
    assert {"init-db", "load-gnaf", "run", "reconcile", "evaluate"} <= set(commands)
    for command in commands:
        with pytest.raises(SystemExit) as stop:
            cli.main([command, "--help"])
        assert stop.value.code == 0, command
    assert "--new-agency" in capsys.readouterr().out
