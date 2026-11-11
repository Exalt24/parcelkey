"""Freshness monitor: how long ago did each source last change, against the longest age the agency accepts?

The age is measured from `source_load.loaded_at`, the time a load last stored a row. A file that arrives and stores nothing does not refresh the age: the same bytes again, a file whose
rows are all unchanged, and a file whose rows were all rejected (a corrected row is rejected as CHANGED_AFTER_LOAD, the stored row is not replaced) leave the source as old as it was.
A source that keeps sending such files is stale in the way that matters. A source that never stored a row is MISSING.

The rows a source delivered after its last load that stored anything, and rejected, are counted here too. When there are some the source is REJECTED even if it is young: the latest
delivery applied nothing. STALE, MISSING and REJECTED make the command exit 1 (STALE wins when the age is past the maximum).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

DEFAULT_MAX_AGE = {"crm": timedelta(days=7), "listing": timedelta(days=1), "sales": timedelta(days=31)}


@dataclass(frozen=True)
class FreshnessRow:
    source: str
    last_loaded_at: datetime | None
    age: timedelta | None
    max_age: timedelta
    status: str                    # FRESH | STALE | MISSING | REJECTED
    idle_loads: int = 0            # files that arrived after the last load that stored a row, and stored nothing
    rejected_after: int = 0        # rows of those files that were rejected

    def line(self) -> str:
        when = "never" if self.last_loaded_at is None else self.last_loaded_at.strftime("%Y-%m-%d %H:%M:%SZ")
        age = "-" if self.age is None else f"{self.age.total_seconds() / 3600:.1f} h"
        text = f"{self.status:8} {self.source:8} last loaded {when}  age {age}  max {self.max_age.total_seconds() / 3600:.0f} h"
        if self.idle_loads:
            text += f"  since then {self.idle_loads} file(s) stored nothing, {self.rejected_after} row(s) rejected"
        return text


def parse_max_age(items: list[str]) -> dict[str, timedelta]:
    """`crm=168` means 168 hours. Sources not named keep their default."""
    out = dict(DEFAULT_MAX_AGE)
    for item in items:
        source, sep, hours = item.partition("=")
        if not sep or not source.strip():
            raise ValueError(f"bad --max-age {item!r}: write source=hours, for example crm=168")
        try:
            h = float(hours)
        except ValueError as exc:
            raise ValueError(f"bad --max-age {item!r}: hours must be a number") from exc
        if h <= 0:
            raise ValueError(f"bad --max-age {item!r}: hours must be above zero")
        out[source.strip()] = timedelta(hours=h)
    return out


def check(cur: psycopg.Cursor, agency: str, max_age: dict[str, timedelta], now: datetime | None = None) -> list[FreshnessRow]:
    """One row per configured source. Runs inside agency_tx."""
    now = now or datetime.now(timezone.utc)
    last: dict[str, datetime] = {}
    idle: dict[str, list[int]] = {}               # source -> [files, rejected rows] after the last load that stored a row
    for source, loaded_at, rejected in cur.execute("SELECT source, loaded_at, rejected FROM source_load WHERE agency_id = %s ORDER BY source, load_id", (agency,)).fetchall():
        if loaded_at is not None:
            last[source] = loaded_at
            idle[source] = [0, 0]
        else:
            counts = idle.setdefault(source, [0, 0])
            counts[0] += 1
            counts[1] += rejected
    rows = []
    for source in sorted(max_age):
        loaded = last.get(source)
        files, rejected = idle.get(source, [0, 0])
        if loaded is None:
            rows.append(FreshnessRow(source, None, None, max_age[source], "MISSING", files, rejected))
            continue
        age = now - loaded
        status = "STALE" if age > max_age[source] else ("REJECTED" if rejected else "FRESH")
        rows.append(FreshnessRow(source, loaded, age, max_age[source], status, files, rejected))
    return rows


def exit_code(rows: list[FreshnessRow]) -> int:
    return 1 if any(r.status != "FRESH" for r in rows) else 0
