# Data sources and what was verified

parcelkey never ships, loads or tests against real G-NAF. Every address, street, suburb, person, email and phone number in this repository is made up by `scripts/seed.py`, which writes files in the public G-NAF table layout. A loader for the real pipe delimited files is provided (`parcelkey/gnaf.py`), and it was only run on generated files in the same layout. It has not read a real Geoscape release.

No data from CoreLogic, Domain, REA, Pricefinder or any other licensed property source is used or imitated.

## What was checked against primary sources (loaded 2026-11-10)

| Fact | Source |
|---|---|
| Table and column names, types and which columns are mandatory for ADDRESS_DETAIL (35 columns, from ADDRESS_DETAIL_PID to PRIMARY_SECONDARY), STREET_LOCALITY, LOCALITY, STATE, ADDRESS_DEFAULT_GEOCODE, ADDRESS_ALIAS, ADDRESS_SITE, ADDRESS_SITE_GEOCODE, FLAT_TYPE_AUT, STREET_TYPE_AUT, LEVEL_TYPE_AUT | G-NAF Data Product Description, Appendix C, https://docs.geoscape.com.au/projects/gnaf_desc/en/stable/appendix_c.html |
| FLAT_NUMBER is up to five digits, LEVEL_NUMBER up to three, NUMBER_FIRST and NUMBER_LAST up to six, LOT_NUMBER up to five characters, BUILDING_NAME up to 200 characters, POSTCODE is optional | same page |
| ADDRESS_DETAIL has no latitude or longitude; they are on ADDRESS_DEFAULT_GEOCODE (LATITUDE number(10,8), LONGITUDE number(11,8)) | same page |
| Open G-NAF is released on data.gov.au as pipe separated value files with the extension `.psv` | Data Product Description, section 9, https://docs.geoscape.com.au/projects/gnaf_desc/en/stable/data_product_delivery.html |
| A release has an `Authority Code` folder (15 lookup tables, such as street types) and a `Standard` folder with 16 tables per state or territory; files are named `ACT_ADDRESS_DETAIL_psv.psv` and `Authority_Code_FLAT_TYPE_AUT_psv.psv`; the delimiter is `\|`; the import dialog in the guide has the header row ticked | Geoscape "Unpacking GNAF" quick reference guide, https://geoscape.com.au/wp-content/uploads/2025/07/Unpacking-GNAF.pdf |
| Street type values in the guide's sample view are full words in capitals (CRESCENT, STREET, AVENUE, CIRCUIT, COURT, DRIVE, PLACE) | same guide, last page |
| G-NAF is published quarterly; the data.gov.au page lists an August 2026 release | https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc (read in a real browser; a plain fetch returned 403) |

## Licence and attribution

From the data.gov.au dataset page, quoted:

- "Use of the G-NAF downloaded from data.gov.au is subject to the End User Licence Agreement (EULA)."
- The EULA terms are based on Creative Commons Attribution 4.0 International, with one added restriction: the open G-NAF "must not be used for the generation of an address or the compilation of an address for the sending of mail unless the user has verified that each address to be used for the sending of mail is capable of receiving mail by reference to a secondary source of information."
- Use must be consistent with the Australian Privacy Principles under the Privacy Act 1988 (Cth).
- Preferred attribution for the licensed material: "G-NAF (c) Geoscape Australia licensed by the Commonwealth of Australia under the Open Geo-coded National Address File (G-NAF) End User Licence Agreement."
- Preferred attribution for adapted material: "Incorporates or developed using G-NAF (c) Geoscape Australia licensed by the Commonwealth of Australia under the Open Geo-coded National Address File (G-NAF) End User Licence Agreement."

What that means here: this repository contains no G-NAF content, so no attribution line is owed for the repository itself. Anyone who runs `parcelkey load-gnaf` on a real release owes the attribution above, must read the EULA, and must not use the result to compile addresses for mailing. The EULA PDF itself was not read; the terms above are as the dataset page summarises them.

## What was not confirmed

- The values of FLAT_TYPE_AUT and LEVEL_TYPE_AUT were not on any page loaded. The generator uses UNIT, APARTMENT, FLAT, SUITE and VILLA for flats and `L` for a level. The loader does not depend on them.
- The geocode type code `PC` and the LEVEL_GEOCODED_CODE value 7 in generated rows are placeholders. The loader does not read either.
- Whether current real files carry a GEOMETRY column in ADDRESS_DEFAULT_GEOCODE is not settled by the pages loaded. The loader matches columns by header name, skips a column the tables here do not have and reports it, so either shape loads.
- The Data Product Description says nothing about the encoding of the files. The loader reads UTF-8.
- Generated PIDs follow the shape `GA` plus a state code plus nine digits, starting at 900000001. A generated PID could equal a real one by chance; it would mean nothing.
- Generated suburb and street names are built from syllables. A coincidence with a real name is possible and means nothing.
- Generated phone numbers are landline numbers written as (0x) 5550 xxxx. A cached summary of the ACMA list of numbers reserved for fiction shows `(02) 5550 xxxx`; ACMA's own page would not load for me, so the exact reserved ranges were not confirmed. Generated mobile numbers do not exist; mobile forms (0412 345 678, +61 412 345 678, (04) 1234 5678) are covered by unit tests only.
- Emails are on example.com, example.net and example.org, which are reserved for documentation (RFC 2606).
