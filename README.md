# parcelkey

![Five messy spellings of one flat become one property id](docs/hero.svg)

Resolve messy property addresses into one master property id, with every decision on record.

[![CI](https://github.com/Exalt24/parcelkey/actions/workflows/ci.yml/badge.svg)](https://github.com/Exalt24/parcelkey/actions/workflows/ci.yml)

**On 4,904 made-up records parcelkey made 4,547 links and 4,529 were right: precision 99.60%, and recall 98.93% of the 4,578 records that should link, with no merge of two different units. Counting the 326 records its own policy sets aside as misses, honest recall is 92.35%.** The suite has 952 tests. The recorded mutation check changed one guard at a time in 1,798 places (1,758 from the syntax tree of 13 modules, 40 named guards) and a test failed every time, on the final code after the fifth review's fixes; the run was reduced (15 Hypothesis examples per test instead of 60, and only the tests that cover the mutated module). Measured 2026-11-11, numbers and commands below.

Two limits belong next to those figures. A flat that G-NAF does not list is never matched to a neighbouring unit: it stays in quarantine as UNIT_CONFLICT and has no master id until a later release lists it, because the matcher never lets a different unit match; 160 of the 4,904 records (3.3 percent) are set aside this way. And agencies are kept apart for each agency's own rows only: a derived id shows every agency its display address and the point of the first record that made it.

parcelkey takes address and contact records from three feeds (a CRM export, a listing feed, a sales history), matches each address to a row in a table in the public G-NAF layout on PostgreSQL with PostGIS, and gives every property one id that is stable for one G-NAF release file. It records the rule and the score behind every link, sets aside what it cannot decide with a reason, merges duplicate contacts only on a shared email or mobile number, and keeps each agency's own rows apart with forced row level security. The caller names the agency and one database role serves all of them. The master property list is shared: a property made for an address that is not in G-NAF (a derived id) shows every agency the display address and the point of the first record that made it. All data in this repository is made up.

## What it is and why

A property data team gets the same house from many places. One feed writes `Unit 5, 12 Smith St`, another `5/12 Smith Street`, a third `Apt 5 12 SMITH ST.`, and a fourth leaves the postcode out. In a tower, two records that differ only by the unit are two homes, and merging them is the worst mistake available. A lot with no street number may or may not be the same place as a numbered address on the same road. A contact can turn up three times with a nickname, a capital letter in the email and a phone number written three ways.

parcelkey normalises each record and writes down which rules fired. It narrows the search with blocking. It matches on the exact key first, then on a typo tolerant street name with a threshold and a tie rule. Two hard rules cannot be switched off: a different unit never matches, and a lot only address never merges with a numbered one unless a geocode is within a distance. A record it cannot decide goes to quarantine with a reason code. A reconcile command proves that every source row is linked or set aside, that nothing is orphaned and that no property has two ids.

The data is generated. `scripts/seed.py` writes about 4,200 addresses in the G-NAF table layout across five states, two agencies' feeds that refer to them with realistic defects, and a hidden answer key that only `parcelkey evaluate` reads. What was checked against Geoscape's published documents, and what was not, is in [docs/data-sources.md](docs/data-sources.md). What the answer key is, and what the accuracy figures cannot show, is in [docs/results.md](docs/results.md).

## How it works

```mermaid
flowchart LR
  F[CRM, listing and sales feeds] --> I[ingest, raw row kept as received]
  I --> N[normalise, rules recorded per row]
  N --> B[block on postcode, locality and street key]
  G[(G-NAF layout tables, flattened to gnaf_address)] --> B
  B --> M[match: exact key, trigram near miss, lot geocode]
  M -->|linked| L[(property_link: rule, score, decided_at)]
  M -->|set aside with a reason| Q[(quarantine)]
  M --> P[(property: master id)]
  N --> C[contacts: email or mobile, evidence kept]
  L --> R[reconcile, freshness, quality, evaluate]
  Q --> R
  C --> R
```

| Stage | What it does | Where |
|---|---|---|
| Load G-NAF | Reads pipe delimited files named as Geoscape's guide shows (`NSW_ADDRESS_DETAIL_psv.psv`), columns by header name, and flattens them into `gnaf_address`. An alias address becomes one more spelling of its principal, never a property. A reload upserts by key: new, changed and newly retired rows are counted, and the master list's copy of each address is refreshed. | `parcelkey/gnaf.py` |
| Ingest | Stores each feed row as received, read by the one reader `reconcile` also uses. A feed missing a column is refused whole and reported while the other feeds still run. Bad rows, among them a row with more cells than the header, go to `ingest_reject` with a reason. The same bytes again write nothing, and a file that stores nothing does not move the feed's age. An agency id that was never loaded is refused unless `run` gets `--new-agency`. | `parcelkey/ingest.py` |
| Normalise | Unit forms (prefixes, zeros, floor words), all 275 G-NAF street types and street suffixes, Mount and Saint, case and punctuation, state names, lots, building names, phones to E.164, emails lower cased. A PO Box, GPO Box, Locked Bag or Private Bag is marked as a mailbox. A carspace is kept apart from a unit and a basement from a level. The rule ids are stored on the row. | `parcelkey/normalise.py`, [docs/normalisation.md](docs/normalisation.md) |
| Block | Candidates in SQL on postcode and street key, locality and street key, and a trigram index for near misses. | `parcelkey/block.py` |
| Match | Exact key, then pg_trgm similarity with a threshold and a tie margin, lots by `ST_Distance` on `geography`. A different unit never matches. | `parcelkey/match.py`, [docs/matching.md](docs/matching.md) |
| Master id | `pk_g_` plus a hash of the G-NAF pid, else `pk_d_` plus a hash of the canonical key. Derived, never numbered, never reassigned, stable for one release file. A new address written with and without a street suffix is one derived property. | `parcelkey/master.py` |
| Quarantine | Ties, below threshold, unit conflict, lot with no geocode or too far, transposed number, unreadable address, mailbox, no suburb, each with a reason code and the candidates. A later run can free a record. | `quarantine` |
| Contacts | Merge only on a shared normalised email or E.164 mobile number inside one agency, evidence kept. A landline never merges. Never on a name. A crowd on one number is set aside, and decided again when the cap is raised. | `parcelkey/contacts.py` |
| Permissions | `agency_id` in every key and forced row level security, with a non owner role. | `parcelkey/migrations/003_rls.sql` |
| Reconcile | Re-reads the feed files with the loader's reader. Rows equal stored plus rejected plus unchanged, every stored reference holds what the file says, every record linked or quarantined, no orphan, one id per property. A derived property that a later release now covers is listed as SUPERSEDED and never rewritten. Exit 1 on any finding. | `parcelkey/reconcile.py` |
| Freshness, quality, evaluate | Last load that stored a row against a maximum age (exit 1 when stale, missing, or when the latest file was all rejected). What the feeds looked like. Precision, recall and honest recall against the answer key. | `freshness.py`, `quality.py`, `evaluate.py` |

## Measured results

All from my runs on 2026-11-11 (Windows 11, 16 logical cores, PostgreSQL 16 with PostGIS 3.4 in Docker on the same machine, other containers running, and another heavy job was using the machine during the final run, so its timings are inflated and say nothing about speed; the counts and percentages do not depend on timing). Full tables: [docs/results.md](docs/results.md). Raw output: [docs/measured-run.txt](docs/measured-run.txt).

| What | Result | How |
|---|---|---|
| Generated world | 4,230 addresses in the G-NAF layout (5 states, 37 localities, 226 streets) plus 92 new addresses made that are not in the file, 72 of which the feeds use; 4,904 source rows in two agencies (each 981 CRM, 662 listing, 809 sales) | `python scripts/seed.py --out out/seed --seed 7` |
| G-NAF load | 9,135 rows in 3.00 s, flat reference of 4,230 addresses in 0.58 s | `python -m parcelkey load-gnaf out/seed/gnaf` |
| Links | 4,547 made, 4,529 right: **precision 99.60%**. Of 4,578 records that should link, 4,529 did: **recall 98.93%**. Counting the 326 records the matcher's own policy sets aside as misses: **honest recall 92.35%** (4,529 of 4,904) | `python tools/measure.py --seed-dir out/seed` |
| Set aside | 357 in quarantine: 324 where the answer key says that is right, 33 that could have linked | same |
| Wrong links | 18, of which 16 are new addresses (not_in_gnaf) merged into a street one letter different | same |
| Different units merged | 0 | same |
| Contacts | 3,338 mentions became 2,493 contacts: pair precision 100.00%, pair recall 68.34%. Only an email or a mobile number merges, and the generator writes landline phones only, so no generated phone merges anyone; before the fifth review a shared landline merged contacts and recall read 93.99%. The generator never makes two people share an email or phone, so the 100.00% holds by construction and shows no real world rate | same |
| One agency, 2,452 records | ingest 21.9 s, normalise 7.0 s, match 56.7 s, contacts 24.6 s | same |
| Ten times the data | precision 99.91%, recall 99.23%, honest recall 92.95%; one agency of 23,370 records matched in 386 s (the other in 391 s) | [docs/results.md](docs/results.md) |

Which defect classes were resolved, set aside or missed is in [docs/results.md](docs/results.md).

## Run it

You need Python 3.11 or newer and Docker. PostgreSQL 16 with PostGIS runs in a container on port 5436, published on 127.0.0.1 only, with three databases that never share data: `parcelkey` is the demo database that every `python -m parcelkey` command below fills, `parcelkey_test` is the one `python -m pytest` empties and refills, and `parcelkey_measure` is the one `tools/measure.py` empties and refills. The tests and the measure script refuse to run against `parcelkey`, so running them does not wipe what you just loaded. There are no accounts or keys. The first `python tools/pg.py up` pulls the `postgis/postgis:16-3.4` image, which takes a few minutes on a slow link, and `pip install` fetches three packages.

```
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux and macOS: source .venv/bin/activate
pip install -r requirements.txt
python tools/pg.py up && python -m parcelkey init-db
python scripts/seed.py --out out/seed --seed 7
python -m parcelkey load-gnaf out/seed/gnaf
python -m parcelkey run --agency agency-a --feeds out/seed/agency-a --new-agency
python -m parcelkey reconcile --agency agency-a --feeds out/seed/agency-a
python -m parcelkey evaluate --agency agency-a --key out/seed/answer_key.csv --truth out/seed/truth_properties.csv
```

Also `quality`, `freshness --agency agency-a --max-age crm=168`, `quarantine --agency agency-a --reason UNIT_CONFLICT`, and `explain --agency agency-a crm:C00042`, which prints one record's raw row, its normalised parts, the rules that fired and what was decided. `python tools/measure.py --seed-dir out/seed` runs both agencies in `parcelkey_measure`, not in the demo database; `--sweep 0.5,0.6,0.7` runs once per similarity threshold. A second agency is started the same way, for example `python -m parcelkey run --agency agency-b --feeds out/seed/agency-b --new-agency`; an agency id that was never loaded is refused without `--new-agency`, so a typo cannot start one. `python tools/pg.py down` removes the container.

## Tests

```
python tools/pg.py up        # a missing database fails the tests, it never skips them
python -m pytest             # 952 tests, in the database parcelkey_test, never the demo one
python tests/mutation_check.py --module normalise     # one module at a time; --guards runs the named guards; --summary totals them
```

What the suites cover, the planted bugs and how the mutation check works are in [docs/tests.md](docs/tests.md). The mutation results are in [docs/mutation-results.txt](docs/mutation-results.txt). The CI workflow runs the whole suite against a PostGIS service container on Ubuntu, and its first run on GitHub was green. The figures in this README come from runs on one Windows 11 machine with Python 3.13.

## What was hard

The first measurement was too good: 100.00% precision at every threshold, because the generated street names were far apart. Look-alike streets showed the real failure, a new address one letter from an existing street merging into it. The eight things that were hard are in [docs/what-was-hard.md](docs/what-was-hard.md).

## Limits

- The data is made up and in the public G-NAF layout, so it is not G-NAF. The loader has never read a real Geoscape release, and nothing near its 15 million addresses has been tried.
- English style addresses only. Other mailbox forms (Post Office Box written out, Private Mail Bag, CMB) are set aside as UNPARSEABLE, while `RMB 5 Smith Rd` is read as that street address. There are no suburb typos, so a misspelt suburb written with no postcode mints a second derived id.
- A lot only record can link to a flat that carries the same lot by LOT_GEOCODE, because the lot belongs to the building.
- A landline never merges contacts; only a shared email or mobile number does.
- A record changed after loading is rejected as CHANGED_AFTER_LOAD and `reconcile --feeds` reports it as FILE_NOT_APPLIED. `freshness` expects all three feeds and reports a missing one as MISSING.
- A G-NAF reload is an upsert: a pid the new file leaves out stays, and properties made for a retired pid are left as they are.
- A new address written with and without a street suffix links to one derived property, and the first form seen names its id.
- Nothing is deployed: one machine, one database, no TLS, no scheduler, no web app.

Every limit, with the ones above in full, is in [docs/limits.md](docs/limits.md).

## Stack

Python 3.11 or newer (developed on 3.13), PostgreSQL 16 with PostGIS 3.4 and pg_trgm, psycopg 3, pytest, Hypothesis, plain SQL migrations and Docker.

## License

MIT, see [LICENSE](LICENSE).
