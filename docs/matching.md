# Matching, the master id, quarantine and contacts

## Blocking

`parcelkey/block.py`. A record is only compared with G-NAF addresses in its block, so the cost grows with the size of a street and not with the size of the country:

| Block | Condition | Index |
|---|---|---|
| A | same postcode and same street key | btree on (postcode, street_key) |
| B | same locality and state, and same street key | btree on (locality_key, state, street_key) |
| C | same postcode or same locality, and a street key pg_trgm finds similar (the typo case) | GIN trigram index on street_key |

## Decision order

`parcelkey/match.py`, function `decide`. It is a pure function of one record and its candidates, so the order records are processed in cannot change a result.

1. **EXACT_KEY.** Same street, number, unit and locality. One candidate: link, score 1. Two: quarantine AMBIGUOUS_TIE. A postcode that disagrees is noted (POSTCODE_MISMATCH) and does not stop the link. A street is the same street when its name and type are equal and its suffix is equal or left out by one side (below).
2. **TRIGRAM.** The street name is a near miss with pg_trgm similarity at or above `--threshold` (default 0.60), the number and unit are equal and one candidate is clearly best. Candidates within `tie_margin` (0.03) of the best are a tie: quarantine AMBIGUOUS_TIE. Below the threshold: quarantine BELOW_THRESHOLD.
3. **Lots.** A record with a lot and no street number: an address that is only a lot links by EXACT_LOT. A numbered address that carries the same lot links only when the record has coordinates within `--lot-distance` metres (default 50), measured with PostGIS `ST_Distance` on `geography` (LOT_GEOCODE, score falls from 1 to 0 with distance). No coordinates: LOT_NO_GEOCODE. Too far: LOT_TOO_FAR.
4. **NEW_DERIVED.** Nothing in the reference is near, so a property is made with an id derived from the canonical key.

Two hard rules sit above all of these and no setting turns them off:

- A different unit or flat number never matches, even when the street and number are equal and even for a near miss street. A record with no unit never matches a flat, and a record with a unit never matches an address with none. A level written on both sides and different also conflicts. A building with its own address (no unit) is matched by a record with no unit.
- A lot only record never merges with a numbered address without a geocode within distance.
- A different street suffix never matches, near miss or not: Smith Street North and Smith Street South are two streets. A record that writes no suffix is the same street as either, so it links to the one that has the number and is a tie when both have it. The suffix is kept in the street type (`STREET NORTH`); G-NAF's STREET_SUFFIX_AUT supplies the names.

## Quarantine reasons

| Reason | Set aside because |
|---|---|
| UNPARSEABLE | no street, or neither a street number nor a lot |
| POSTAL_ADDRESS | a PO Box, GPO Box, Locked Bag or Private Bag: a mailbox is not a property, so it is never matched to a street and never made a derived property |
| AMBIGUOUS_TIE | two or more candidates are equally good |
| BELOW_THRESHOLD | the best street name is too different to link |
| UNIT_CONFLICT | the street and number match, and every candidate has a different unit (or the record has one and they have none) |
| LOCALITY_CONFLICT | the street, number and postcode match an address in another suburb |
| NUMBER_TRANSPOSED | no address has this number, and one has the number with two neighbouring digits swapped (12 and 21) |
| LOT_NO_GEOCODE, LOT_TOO_FAR | a lot only record meets a numbered address that carries the lot, with no coordinates or too far |

Each quarantine row keeps `detail` (the candidates and their scores, or the units in conflict). A quarantined record is decided again on the next run, so a later G-NAF load can free it; then its row becomes `resolved`.

## Aliases

G-NAF marks an address as an alias (ALIAS_PRINCIPAL = A) and ADDRESS_ALIAS names its principal address. `build_reference` keeps an alias as one more spelling of its principal in `gnaf_address` (`principal_pid` is set on it), and blocking offers it as a candidate under the principal's pid. A record that matches an alias spelling links to the principal, so an alias is never a property of its own. When a record is near both the principal and an alias, the better scoring spelling is the one candidate for that property, so a property is never a tie with itself. An alias whose link to a principal is missing, retired or points at an address that was left out is left out and counted. A row with no ALIAS_PRINCIPAL value counts as a principal.

## The master property id

`parcelkey/master.py`. `property_id` is derived, never numbered:

- matched to G-NAF: `pk_g_` + the first 16 hex digits of sha256 of `gnaf|<ADDRESS_DETAIL_PID>`
- not in G-NAF: `pk_d_` + the first 16 hex digits of sha256 of `derived|<canonical key>`. The key has no postcode, and a blank state is taken from the postcode's range, so a feed that leaves the state column empty reaches the same id as one that fills it. With neither a state nor a postcode the state stays blank in the key, and that address has its own id.

**The rule.** For one G-NAF release file, the same address gets the same id on every run, on every machine and in every agency. A link, once written, is never rewritten, and a derived property stays derived even if the address reaches G-NAF in a later release. `reconcile` checks that every id equals its derivation.

**Its limit.** After a release adds an address, the same place can hold a derived id (made before) and a G-NAF id (made by records matched after). `reconcile` does not rewrite either: its SUPERSEDED check lists every derived property an agency uses whose canonical key now equals a G-NAF address, with the G-NAF pid, and exits 1. Merging them is left to a person. The G-NAF id hashes ADDRESS_DETAIL_PID; ADDRESS_FEATURE, the table that tracks a pid across releases, is not modelled, so a pid that a later release re-issues for the same address gives a new id. The id is stable for one release file and nothing wider.

## The link table

`property_link(agency_id, record_id, property_id, rule, rules, score, decided_at)`. `rule` is the match rule, `rules` is every normalisation rule that fired on the record plus the match notes. `python -m parcelkey explain` shows it all for one record.

## Contacts

`parcelkey/contacts.py`. Two mentions are one contact only when they share a normalised email or an E.164 phone, inside one agency. A name is never evidence. Each decision keeps its evidence (`matched_on`, the identifier, the earlier records that carried it). A mention that would grow a cluster past `--max-cluster` records (default 8) is set aside in `contact_quarantine` as CLUSTER_TOO_LARGE, so a shared reception number cannot merge a crowd. A contact id comes from the record that created the contact and is never reassigned. A mention that joins two contacts merges them: the smaller id survives and `contact_merge` records why.

Views `campaign_property` (listing campaign to master property) and `contact_property` (contact to master property through the record) answer the linking questions. Both run as the caller, so row level security applies.

## Permissions

Every table with agency data has `agency_id` in its key and forced row level security: a row is visible only when `agency_id` equals `app.agency_id` of the current transaction, set by `agency_tx`. No setting, no rows. The caller picks the agency: `agency_tx(conn, agency)` takes the id as an argument, and one database role, `parcelkey_app`, serves every agency, so the policy keeps a transaction to the agency it named and does not stop a caller that names another one. Nothing here authenticates a caller. The pipeline runs as `parcelkey_app` (no superuser, no BYPASSRLS), which cannot update the raw row of a record, cannot write the G-NAF tables and can add but not change a master property. The G-NAF tables, `gnaf_address` and `property` carry no agency: they hold addresses and no agency or customer data, and the master id is shared across agencies on purpose.
