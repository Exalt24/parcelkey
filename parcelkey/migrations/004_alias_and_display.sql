-- Aliases, primary and secondary links, and the display line of a G-NAF address.
-- ADDRESS_ALIAS and PRIMARY_SECONDARY are in the public G-NAF layout (Data Product Description, Appendix C). Reference data like the tables in 001: no agency_id, no row level security.
-- gnaf_address gains the principal of an alias spelling (null on a principal address) and the address spelled the way G-NAF itself spells it.

CREATE TABLE IF NOT EXISTS address_alias (
    address_alias_pid varchar(15)  PRIMARY KEY,
    date_created      date         NOT NULL,
    date_retired      date,
    principal_pid     varchar(15)  NOT NULL,
    alias_pid         varchar(15)  NOT NULL,
    alias_type_code   varchar(10)  NOT NULL,
    alias_comment     varchar(200)
);
CREATE INDEX IF NOT EXISTS address_alias_alias_ix ON address_alias (alias_pid);

CREATE TABLE IF NOT EXISTS primary_secondary (
    primary_secondary_pid varchar(15)  PRIMARY KEY,
    date_created          date         NOT NULL,
    date_retired          date,
    primary_pid           varchar(15)  NOT NULL,
    secondary_pid         varchar(15)  NOT NULL,
    ps_join_type_code     numeric(2,0) NOT NULL,
    ps_join_comment       varchar(500)
);

ALTER TABLE gnaf_address ADD COLUMN IF NOT EXISTS principal_pid text;
ALTER TABLE gnaf_address ADD COLUMN IF NOT EXISTS display_address text NOT NULL DEFAULT '';
