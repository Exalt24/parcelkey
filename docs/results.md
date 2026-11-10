# Measured results in full

Run on 2026-11-10: Windows 11, 16 logical cores, Python 3.13, PostgreSQL 16 with PostGIS 3.4 in Docker on the same machine, other containers running. The raw output is in [measured-run.txt](measured-run.txt). Both agencies are summed here. Resolved is a link to the property the answer key names. Set aside is quarantine where the key says that is right. Missed is quarantine where the key names a property. Wrong is a link to another property.

## Contacts

One primary defect per mention. A mention is resolved when it is in the same contact as the person's first mention. `dup_no_shared_id` is the same person with a new email and no phone: parcelkey is built not to merge it, so the 46 misses are that choice, counted.

| Contact class | Mentions | Resolved | Missed | Wrong | Set aside |
|---|---|---|---|---|---|
| contact_clean (first mention) | 2,117 | 2,117 | 0 | 0 | 0 |
| name_variant (Kate, K. Smith, Smith, Kate) | 181 | 180 | 1 | 0 | 0 |
| email_case (upper case, spaces) | 118 | 116 | 2 | 0 | 0 |
| phone_format (five written forms) | 109 | 103 | 6 | 0 | 0 |
| dup_new_email (same phone, new email) | 78 | 77 | 1 | 0 | 0 |
| dup_no_shared_id | 46 | 0 | 46 | 0 | 0 |
| same_name_different_person (must stay apart) | 42 | 42 | 0 | 0 | 0 |
| sales_buyer (a buyer who is in the CRM) | 647 | 635 | 12 | 0 | 0 |

3,338 mentions became 2,226 contacts. Pairwise precision 100.00% (1,407 of 1,407 merged pairs), recall 93.99% (1,407 of 1,497 true pairs).

## The similarity threshold

The same data, both agencies, at five values (`python tools/measure.py --seed-dir out/seed --sweep 0.4,0.5,0.6,0.7,0.8`):

| Threshold | Links | Wrong | Precision | Recall | Missed |
|---|---|---|---|---|---|
| 0.40 | 4,564 | 25 | 99.45% | 99.15% | 16 |
| 0.50 | 4,564 | 25 | 99.45% | 99.15% | 16 |
| **0.60 (default)** | 4,547 | 18 | 99.60% | 98.93% | 33 |
| 0.70 | 4,493 | 18 | 99.60% | 97.75% | 87 |
| 0.80 | 4,426 | 0 | 100.00% | 96.68% | 152 |

0.60 is the default because a wrong merge of two properties costs more than a record in quarantine, and between 0.60 and 0.70 recall falls with no gain in precision. At 0.80 there are no wrong links, and 103 correct links are lost.

## Quarantine by reason

| Reason | Agency A | Agency B |
|---|---|---|
| UNIT_CONFLICT | 69 | 91 |
| LOT_NO_GEOCODE | 34 | 39 |
| NUMBER_TRANSPOSED | 31 | 50 |
| BELOW_THRESHOLD | 12 | 19 |
| LOT_TOO_FAR | 9 | 3 |

## Ten times the data

`python scripts/seed.py --out out/seed40k --seed 7 --addresses 40000`, then `python tools/measure.py --seed-dir out/seed40k`. 40,295 addresses, 83,044 G-NAF rows loaded in 1.40 s, flat reference built in 1.60 s, two agencies of 23,370 records.

| | Agency A | Agency B |
|---|---|---|
| Precision | 99.94% (21,720 of 21,734) | 99.89% (21,724 of 21,748) |
| Recall | 99.32% | 99.15% |
| Ingest | 12.8 s | 12.0 s |
| Normalise | 35.9 s | 12.9 s |
| Match | 112.3 s | 75.6 s |
| Contacts | 83.0 s | 52.6 s |
| Reconcile | passes | passes |

Against the 4,904 record run (about 4.8 s to match one agency), ten times the records took 15 to 23 times as long to match. The trigram branch reads an index over the whole address table. Filtering to the streets of one postcode first, or partitioning the table by state, would keep the growth flat. That is not built.
