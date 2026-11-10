# Normalisation rules

`parcelkey/normalise.py`. Every function is pure. Each returns what it found and the ids of the rules that fired, and the ids are stored on the record (`source_record.norm_rules`, `contact_rules`) so a surprising result can be explained from one row. `python -m parcelkey explain --agency A crm:C00042` prints them.

The address parts are: unit, level, building, lot, street number (first and last), street name, street type, locality, state, postcode. Street types are written the G-NAF way, as the full word (`STREET`, not `ST`). The same functions read G-NAF rows and messy text, so both sides of a match use one vocabulary.

## Address rules

| Rule id | What it does | Example |
|---|---|---|
| CASE_FOLDED | Upper case. Fires only when something was not upper case already. | `12 smith st` |
| PUNCT_STRIPPED | Characters other than letters, digits, `/`, `,`, `-`, `#` and space become spaces. Apostrophes are dropped. | `U5/12 Smith St.` |
| WHITESPACE_COLLAPSED | Runs of spaces are one space. | `12   Smith St` |
| UNIT_MARKER_WORD | `Unit`, `U`, `Apt`, `Apartment`, `Flat`, `Suite`, `Ste`, `Shop`, `Villa`, `Office`, `Townhouse`, `Room` or `#` followed by an id. | `Unit 5, 12 Smith St`, `Apt 5 12 Smith Street` |
| UNIT_MARKER_GLUED | The marker and the id written together. | `U5/12`, `#5` |
| UNIT_SLASH_FORM | `unit/number`. | `5/12 Smith St`, `5A/12-14 Smith St` |
| LEVEL_MARKER | `Level`, `Lvl`, `L`, `Floor`, `Flr` followed by an id or `G` or `Ground`. | `Level 3 Suite 7, 12 Smith St` |
| LOT_MARKER | `Lot` followed by an id. | `Lot 7 Smith Rd` |
| BUILDING_NAME_PREFIX | Words before the first unit, level or number, with no digit in them. Read with a comma after them or without one. | `Skyline Tower, Unit 5, 12 Smith St` |
| STREET_TYPE_EXPANDED | `St`, `Str`, `Rd`, `Ave`, `Dr`, `Ct`, `Pl`, `Cres`, `Ln`, `Tce`, `Pde`, `Cl`, `Blvd`, `Hwy`, `Cct`, `Esp`, `Gr`, `Sq` and the other spellings in `STREET_TYPE_SPELLINGS` become the full word. | `Smith St` |
| MOUNT_EXPANDED | `Mt` becomes `MOUNT` in a street or suburb name. | `Mt Pleasant Rd` |
| SAINT_EXPANDED | `St` becomes `SAINT` when it starts a street name, and everywhere in a suburb name. At the end of a street it is the type `STREET`. | `St Kilda Rd`, `St Albans` |
| STATE_NAME_TO_CODE | `New South Wales` becomes `NSW`, and the other seven. | `Victoria` |
| SUBURB_FROM_LINE | The suburb, state and postcode were read from the end of a one line address. | `5/12 Smith St, Fakeville NSW 2000` |
| SUBURB_SPLIT_HEURISTIC | No comma told where the street ends, so the first street type word with a name before it and something after it ends the street. | `12 Smith St Fakeville NSW 2000` |
| POSTCODE_MISSING, POSTCODE_BAD_SHAPE | The postcode is empty, or is not four digits. It is left empty, never guessed. | `2000x`, `200` |
| STATE_MISSING, SUBURB_MISSING | Nothing to read it from. Left empty. | |
| COORDS_OUT_OF_RANGE | A listing latitude or longitude is not a number or is outside Australia. The coordinates are dropped. | `33.8` (a missing minus sign) |

Never done: the letter O is not read as the digit 0, a postcode is not inferred from a suburb, a state is not inferred from a postcode, a street name is not spell corrected here (that is the match stage's job, with a threshold and a tie rule).

## What a canonical key is

`Addr.canon_key` is `level|unit|lot|number|street name and type|locality|state`. The postcode is left out on purpose: a feed that drops or mistypes it must not make a second derived property for one address. The building name is left out too, because it is written differently everywhere.

## Phones

`normalise_phone` returns E.164 (`+61412345678`) or an empty string.

| Rule id | Fires when |
|---|---|
| PHONE_COUNTRY_CODE_STRIPPED | `+61`, `0061` or a leading `61` on a number of 11 or more digits was removed. |
| PHONE_TRUNK_ZERO_STRIPPED | The leading `0` was removed (also the `(0)` in `+61 (0)2 5550 1234`). |
| PHONE_E164 | The rest is a mobile (`4` and eight digits) or a landline (`2`, `3`, `7` or `8` and eight digits). |
| PHONE_INVALID | Anything else: no area code, a 13 or 1300 or 1800 number, a foreign number, text such as `tbc`. Not guessed. |

## Emails

`normalise_email` trims, removes `mailto:` and angle brackets, and lower-cases. The local part is never edited beyond case: dots are not removed from a Gmail style address and plus tags are not cut, because that would be guessing. Rules: EMAIL_TRIMMED, EMAIL_MAILTO_STRIPPED, EMAIL_LOWERCASED, EMAIL_INVALID.

## Names

`normalise_name` only collapses spaces. A name is shown and never matched on.

## How it is tested

`tests/test_normalise.py`: a table of every unit form from the brief, one test per street type spelling, every state name and code, Mount and Saint in street and suburb, lots, building names, the tail of a one line address, a table of phone forms with the rules that must fire and a table of numbers that must not be guessed. Hypothesis properties: normalising the canonical line gives the same address, unit and number are read back from any spelling, normalising any text never raises, phone and email normalisation are idempotent. The mutation check requires a failing test for each change to the module.
