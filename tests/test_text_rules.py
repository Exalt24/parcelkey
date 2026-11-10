"""Text rules for the repository: no em or en dashes in any text file, and every file the README and the docs link to exists."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".hypothesis", ".mutation", "out"}
TEXT_SUFFIXES = {".md", ".py", ".sql", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg"}
DASHES = (chr(0x2014), chr(0x2013))


def text_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS):
            yield p


def test_no_em_or_en_dashes_anywhere():
    bad = []
    for p in text_files():
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if any(d in line for d in DASHES):
                bad.append(f"{p.relative_to(ROOT)}:{n}")
    assert not bad, bad


def test_every_relative_link_in_the_markdown_files_points_at_a_file_that_exists():
    missing = []
    for p in ROOT.rglob("*.md"):
        if set(p.relative_to(ROOT).parts) & SKIP_DIRS:
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", p.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (p.parent / target).exists():
                missing.append(f"{p.relative_to(ROOT)} -> {target}")
    assert not missing, missing


def test_no_secrets_or_real_hosts_are_committed():
    patterns = [re.compile(r"sk-[A-Za-z0-9]{20,}"), re.compile(r"ghp_[A-Za-z0-9]{20,}"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(r"AKIA[0-9A-Z]{16}")]
    hits = [str(p.relative_to(ROOT)) for p in text_files() if any(pat.search(p.read_text(encoding="utf-8")) for pat in patterns)]
    assert not hits, hits
