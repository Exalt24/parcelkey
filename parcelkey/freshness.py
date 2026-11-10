"""Freshness monitor: how long ago did each source last change, against the longest age the agency accepts?

The age is measured from `source_load.loaded_at`, the time a file with new content was stored. A feed that is delivered again with identical bytes does not refresh its
age: a source that keeps sending the same file is stale in the way that matters. A source that never arrived is MISSING. STALE and MISSING make the command exit 1.
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
    status: str                    # FRESH | STALE | MISSING

    def line(self) -> str:
        when = "never" if self.last_loaded_at is None else self.last_loaded_at.strftime("%Y-%m-%d %H:%M:%SZ")
        age = "-" if self.age is None else f"{self.age.total_seconds() / 3600:.1f} h"
        return f"{self.status:8} {self.source:8} last loaded {when}  age {age}  max {self.max_age.total_seconds() / 3600:.0f} h"


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
    last = {r[0]: r[1] for r in cur.execute("SELECT source, max(loaded_at) FROM source_load WHERE agency_id = %s GROUP BY source", (agency,)).fetchall()}
    rows = []
    for source in sorted(max_age):
        loaded = last.get(source)
        if loaded is None:
            rows.append(FreshnessRow(source, None, None, max_age[source], "MISSING"))
            continue
        age = now - loaded
        rows.append(FreshnessRow(source, loaded, age, max_age[source], "STALE" if age > max_age[source] else "FRESH"))
    return rows


def exit_code(rows: list[FreshnessRow]) -> int:
    return 1 if any(r.status != "FRESH" for r in rows) else 0
