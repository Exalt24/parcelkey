# Measured results in full

Run on 2026-11-11: Windows 11, 16 logical cores, Python 3.13, PostgreSQL 16 with PostGIS 3.4 in Docker on the same machine, other containers running. The raw output is in [measured-run.txt](measured-run.txt). Both agencies are summed here. Resolved is a link to the property the answer key names. Set aside is quarantine where the key says that is right. Missed is quarantine where the key names a property. Wrong is a link to another property. Recall is resolved over the records the key says should link. Honest recall is resolved over every record in the key, so the records the key says to set aside count as misses. Read the last section, How accuracy was checked, and what it cannot show, before quoting any figure.

The world has 4,230 addresses in the G-NAF layout and 92 made up addresses that are not in it. The feeds use 72 of those 92 (`new_builds_in_feeds` in `manifest.json`), which is the number the answer key holds.

## Defect classes

Which defect classes were resolved, set aside or missed, both agencies together. Each record has one primary defect.

| Defect class | Records | Resolved | Set aside | Missed | Wrong |
|---|---|---|---|---|---|
| clean | 1,250 | 1,250 | 0 | 0 | 0 |
| street_abbrev (St, Str, Rd, Ave) | 561 | 561 | 0 | 0 | 0 |
| mount_saint | 47 | 47 | 0 | 0 | 0 |
| case_punct | 280 | 280 | 0 | 0 | 0 |
| unit_word (`Unit 5, 12 Smith St`) | 279 | 279 | 0 | 0 | 0 |
| unit_slash (`5/12 Smith St`) | 255 | 255 | 0 | 0 | 0 |
| unit_apt (`Apt 5 12 Smith Street`) | 171 | 171 | 0 | 0 | 0 |
| unit_u_slash (`U5/12`) | 169 | 169 | 0 | 0 | 0 |
| unit_flat (`Flat 5`) | 158 | 158 | 0 | 0 | 0 |
| level_suite (`Level 3 Suite 7`) | 58 | 58 | 0 | 0 | 0 |
| tower_no_unit_parent (the tower has its own address) | 13 | 13 | 0 | 0 | 0 |
| tower_no_unit_noparent | 98 | 0 | 97 | 0 | 1 |
| unit_unknown (a flat the file does not have) | 66 | 0 | 66 | 0 | 0 |
| lot_exact (a lot only address in the file) | 71 | 71 | 0 | 0 | 0 |
| lot_geocoded (lot, coordinates close) | 26 | 26 | 0 | 0 | 0 |
| lot_no_geo (lot, no coordinates) | 73 | 0 | 73 | 0 | 0 |
| lot_far (lot, coordinates 300 to 600 m off) | 12 | 0 | 12 | 0 | 0 |
| postcode_missing | 219 | 219 | 0 | 0 | 0 |
| postcode_wrong | 186 | 186 | 0 | 0 | 0 |
| postcode_transposed | 80 | 80 | 0 | 0 | 0 |
| number_transposed (street number digits swapped) | 77 | 0 | 76 | 0 | 1 |
| street_typo | 115 | 105 | 0 | 10 | 0 |
| not_in_gnaf (a new address, several records each) | 320 | 281 | 0 | 23 | 16 |
| combined (unit form, abbreviation, case, no postcode) | 320 | 320 | 0 | 0 | 0 |
| **Total** | **4,904** | **4,529** | **324** | **33** | **18** |

## Contacts

One primary defect per mention. A mention is resolved when it is in the same contact as the person's first mention. `dup_no_shared_id` is the same person with a new email and no phone: parcelkey is built not to merge it, so the 46 misses are that choice, counted. Every phone number the generator writes is a landline, and only an email or a mobile number merges contacts (a landline is a shared office or household line and never merges), so `phone_format` (the same person, a phone and no email) and `dup_new_email` (the same phone, a new email) are missed in full: 109 and 78 mentions. That is the policy counted, not a matching fault; before the fifth review a shared landline merged contacts, recall read 93.99%, and one office number could join eight different people.

| Contact class | Mentions | Resolved | Missed | Wrong | Set aside |
|---|---|---|---|---|---|
| contact_clean (first mention) | 2,117 | 2,117 | 0 | 0 | 0 |
| name_variant (Kate, K. Smith, Smith, Kate) | 181 | 169 | 12 | 0 | 0 |
| email_case (upper case, spaces) | 118 | 116 | 2 | 0 | 0 |
| phone_format (five written forms, a landline) | 109 | 0 | 109 | 0 | 0 |
| dup_new_email (same phone, new email) | 78 | 0 | 78 | 0 | 0 |
| dup_no_shared_id | 46 | 0 | 46 | 0 | 0 |
| same_name_different_person (must stay apart) | 42 | 42 | 0 | 0 | 0 |
| sales_buyer (a buyer who is in the CRM) | 647 | 553 | 94 | 0 | 0 |

3,338 mentions became 2,493 contacts. Pairwise precision 100.00% (1,023 of 1,023 merged pairs), recall 68.34% (1,023 of 1,497 true pairs).

## The similarity threshold

The same data, both agencies, at five values (`python tools/measure.py --seed-dir out/seed --sweep 0.4,0.5,0.6,0.7,0.8`):

| Threshold | Links | Wrong | Precision | Recall | Honest recall | Missed |
|---|---|---|---|---|---|---|
| 0.40 | 4,564 | 25 | 99.45% | 99.15% | 92.56% | 16 |
| 0.50 | 4,564 | 25 | 99.45% | 99.15% | 92.56% | 16 |
| **0.60 (default)** | 4,547 | 18 | 99.60% | 98.93% | 92.35% | 33 |
| 0.70 | 4,493 | 18 | 99.60% | 97.75% | 91.25% | 87 |
| 0.80 | 4,426 | 0 | 100.00% | 96.68% | 90.25% | 152 |

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

`python scripts/seed.py --out out/seed40k --seed 7 --addresses 40000`, then `python tools/measure.py --seed-dir out/seed40k`. 40,295 addresses, 85,781 G-NAF rows loaded in 2.41 s, flat reference built in 2.49 s, two agencies of 23,370 records.

| | Agency A | Agency B |
|---|---|---|
| Precision | 99.94% (21,720 of 21,734) | 99.89% (21,724 of 21,748) |
| Recall | 99.32% | 99.15% |
| Ingest | 13.9 s | 14.3 s |
| Normalise | 16.3 s | 16.3 s |
| Match | 94.2 s | 108.3 s |
| Contacts | 109.3 s | 68.1 s |
| Reconcile | passes | passes |

Both agencies together: 43,482 links made, 43,444 right, precision 99.91%. Recall is 99.23% (43,444 of 43,779 that should link) and honest recall is 92.95% (43,444 of 46,740, with the 2,961 policy records counted as misses).

Against the 4,904 record run (7.1 s and 7.1 s to match one agency), ten times the records took 13 to 15 times as long to match. The trigram branch reads an index over the whole address table. Filtering to the streets of one postcode first, or partitioning the table by state, would keep the growth flat. That is not built.

## How accuracy was checked, and what it cannot show

Every precision and recall figure here is one comparison: each record the pipeline decided is checked against an answer key that `scripts/seed.py` writes. Read these limits before quoting a figure.

- **What the answer key is.** The generator builds the made up world first and renders each messy feed line from a property it already holds. The key is the property each line was rendered from (`answer_key.csv`, one row per record). Nothing was labelled by hand.
- **The generator and the matcher share a vocabulary.** The generator writes St, Rd and Ave, `Unit 5`, `5/12`, `U5/12` and Mt, and the normaliser reads exactly those. A line the generator can write is a line the normaliser was built to read, so the clean, abbreviation and unit classes score close to 100% by construction.
- **For an address that is not in G-NAF, the key is the matcher's own key.** The expected result for the 72 new addresses the feeds use (92 were made) is `Addr.canon_key`, imported from `parcelkey/normalise.py` into the generator. A record there can only be wrong by being linked somewhere else, never because the key function is wrong.
- **Five classes carry the matcher's own policy as the expected result.** lot_no_geo, lot_far, number_transposed, tower_no_unit_noparent and unit_unknown expect `quarantine` because the matcher's rules set them aside. There are 326 such records of 4,904. Recall leaves them out of its denominator. Honest recall counts them as misses: 4,529 of 4,904 is 92.35%, against 98.93% of the 4,578 records that should link. Neither figure says whether setting them aside is right.
- **The look-alike streets and the threshold were fitted on this seed.** The first run scored 100.00% precision at every threshold because the generated streets were far apart. The look-alike streets were added after that run showed it, and the threshold 0.60 was chosen from a sweep on seed 7, the seed the results are reported on. No second seed was held out.
- **No hand labelled real sample exists.** No real G-NAF release and no real feed was read, so none of these numbers is an accuracy on real addresses. They show that the code does what its rules say on the defects the generator can write, and how the threshold trades links against misses on this data.
