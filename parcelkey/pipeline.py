"""Run the stages for one agency: ingest, normalise, match, contacts. Each stage is its own transaction, so a failure in one leaves the earlier stages stored."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psycopg

from .contacts import DEFAULT_MAX_CLUSTER, ContactCounts, resolve_pending
from .db import agency_tx
from .ingest import SOURCES, IngestResult, ingest_file
from .match import MatchConfig, MatchCounts, match_pending
from .records import normalise_pending


@dataclass
class PipelineReport:
    agency: str
    ingested: list[IngestResult] = field(default_factory=list)
    normalised: int = 0
    match: MatchCounts = field(default_factory=MatchCounts)
    contacts: ContactCounts = field(default_factory=ContactCounts)
    seconds: dict[str, float] = field(default_factory=dict)


def run_agency(conn: psycopg.Connection, agency: str, feeds_dir: Path | None, cfg: MatchConfig = MatchConfig(), *, now: datetime | None = None,
               max_cluster: int = DEFAULT_MAX_CLUSTER) -> PipelineReport:
    """Run the pipeline for one agency. `feeds_dir` holds the feed files (a missing file is skipped); with None only the stages after ingest run."""
    report = PipelineReport(agency)
    t0 = time.perf_counter()
    if feeds_dir is not None:
        for source, spec in SOURCES.items():
            path = Path(feeds_dir) / spec.file
            if path.is_file():
                with agency_tx(conn, agency) as cur:
                    report.ingested.append(ingest_file(cur, agency, source, path, now=now))
    t1 = time.perf_counter()
    with agency_tx(conn, agency) as cur:
        report.normalised = normalise_pending(cur, agency)
    t2 = time.perf_counter()
    with agency_tx(conn, agency) as cur:
        report.match = match_pending(cur, agency, cfg)
    t3 = time.perf_counter()
    with agency_tx(conn, agency) as cur:
        report.contacts = resolve_pending(cur, agency, max_cluster)
    t4 = time.perf_counter()
    report.seconds = {"ingest": t1 - t0, "normalise": t2 - t1, "match": t3 - t2, "contacts": t4 - t3}
    return report
