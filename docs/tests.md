# What the tests cover

The tests that need PostgreSQL fail when it is missing and never skip. `python tools/pg.py up` starts a `postgis/postgis:16-3.4` container on port 5436 and writes `.env` with random passwords; `python tools/pg.py down` removes it.

| File | Covers |
|---|---|
| `tests/test_normalise.py` | every unit form, street type spelling, state name, Mount and Saint, lot, building name, phone form and email form, with the rules that must fire; Hypothesis properties |
| `tests/test_match_unit.py` | `decide` with no database: the rules in order, the unit table, the lot distance at, under and over the limit, ties, thresholds, transposed numbers; order of candidates never matters; different units never pair |
| `tests/test_pipeline.py` | the whole pipeline on a hand written reference with real PostGIS: every outcome and every quarantine reason, campaigns, a second run that writes nothing, a decided link that is never rewritten, a quarantined record freed by a later reference |
| `tests/test_contacts.py` | merge on email and phone with the evidence, never on a name, never across agencies, the cluster cap, bridging two contacts, stable ids, order independence |
| `tests/test_reconcile.py` | each reconcile check is shown to pass and then to fail on planted damage (a deleted link, an orphan, a wrong id, a stale file, a merged contact) |
| `tests/test_tenant.py` | agency B cannot read agency A's rows in any table, no setting means no rows, writes and updates across agencies fail, the owner role is held to the policy (and a control shows it is not without FORCE), grants on reference data, the master list and the raw row |
| `tests/test_ingest.py`, `tests/test_gnaf.py` | rejects with reasons, a feed with a missing column refused whole, byte for byte storage, the G-NAF loader on generated files |
| `tests/test_freshness.py`, `tests/test_quality_cli.py` | fresh, stale and missing, the exit codes, the data quality report, every command as a real process |
| `tests/test_evaluate.py` | the measurement on a small world whose answers are written by hand |
| `tests/test_seed.py` | same seed same bytes, the documented layout, ranges, every defect class present, everything made up |
| `tests/test_property.py` | Hypothesis through the database: a rerun writes nothing (down to `xmin`), the order of rows changes no decision, different units never merge, every row is accounted for |
| `tests/test_planted_bugs.py` | six deliberately planted bugs (the unit rule, the distance rule, the threshold, lower casing, the trunk zero, row level security switched off) and a greedy matcher; each must make an existing test fail |
| `tests/test_text_rules.py` | no em or en dashes in any text file, every relative link resolves, no secret shaped strings |

## Mutation check

`tests/mutation_check.py` changes one guard at a time in a copy of the code and requires a failing test for each. Two kinds of mutant:

- derived from the syntax tree of 13 modules: comparisons flipped, `and` and `or` swapped, a `not` removed, a condition negated, a guard that ends in `return` or `raise` replaced by `pass`, a call dropped, a returned value turned into `None`, integers moved by one, rule codes emptied;
- 39 named guards, including the ones in SQL: the row level security policy made always true, read side open, write side open, matching other agencies, not forced, not enabled, missing on one table; the column grants; the unit rule, the level rule, the distance rule, the threshold, the tie margin, lower casing, the trunk zero, the rerun guards, the reconcile orphan check, the contact cap, merging on a name.

Results are in [mutation-results.txt](mutation-results.txt). The recorded run set `PARCELKEY_EXAMPLES=15`, so each Hypothesis test drew 15 examples instead of the default 60, and it ran only the tests that cover the mutated module; no mutant survived that first stage, so the whole-suite rerun of survivors was never needed. A survivor means a line no test pins: a test is added, the mutant is never deleted. A line that cannot matter carries `# no-mutate: <why>`.
