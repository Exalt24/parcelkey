-- Tables in the public G-NAF layout (names and columns from the Geoscape G-NAF Data Product Description, Appendix C).
-- Reference data: it holds no agency or customer data, so it has no agency_id and no row level security. The app role may read it and nothing else.
-- Constraints are kept to primary keys on purpose: a bulk load of the real files should not depend on the order of the tables.

CREATE TABLE IF NOT EXISTS state (
    state_pid          varchar(15) PRIMARY KEY,
    date_created       date        NOT NULL,
    date_retired       date,
    state_name         varchar(50) NOT NULL,
    state_abbreviation varchar(3)  NOT NULL
);

CREATE TABLE IF NOT EXISTS locality (
    locality_pid        varchar(15)  PRIMARY KEY,
    date_created        date         NOT NULL,
    date_retired        date,
    locality_name       varchar(100) NOT NULL,
    primary_postcode    varchar(4),
    locality_class_code char(1)      NOT NULL,
    state_pid           varchar(15)  NOT NULL,
    gnaf_locality_pid   varchar(15),
    gnaf_reliability_code numeric(1,0)
);

CREATE TABLE IF NOT EXISTS street_locality (
    street_locality_pid    varchar(15)  PRIMARY KEY,
    date_created           date         NOT NULL,
    date_retired           date,
    street_class_code      char(1)      NOT NULL,
    street_name            varchar(100) NOT NULL,
    street_type_code       varchar(15),
    street_suffix_code     varchar(15),
    locality_pid           varchar(15)  NOT NULL,
    gnaf_street_pid        varchar(15),
    gnaf_street_confidence numeric(1,0),
    gnaf_reliability_code  numeric(1,0)
);

CREATE TABLE IF NOT EXISTS address_detail (
    address_detail_pid  varchar(15) PRIMARY KEY,
    date_created        date        NOT NULL,
    date_last_modified  date,
    date_retired        date,
    building_name       varchar(200),
    lot_number_prefix   varchar(2),
    lot_number          varchar(5),
    lot_number_suffix   varchar(2),
    flat_type_code      varchar(7),
    flat_number_prefix  varchar(2),
    flat_number         numeric(5,0),
    flat_number_suffix  varchar(2),
    level_type_code     varchar(4),
    level_number_prefix varchar(2),
    level_number        numeric(3,0),
    level_number_suffix varchar(2),
    number_first_prefix varchar(3),
    number_first        numeric(6,0),
    number_first_suffix varchar(2),
    number_last_prefix  varchar(3),
    number_last         numeric(6,0),
    number_last_suffix  varchar(2),
    street_locality_pid varchar(15),
    location_description varchar(45),
    locality_pid        varchar(15) NOT NULL,
    alias_principal     char(1),
    postcode            varchar(4),
    private_street      varchar(75),
    legal_parcel_id     varchar(20),
    confidence          numeric(1,0),
    address_site_pid    varchar(15) NOT NULL,
    level_geocoded_code numeric(2,0) NOT NULL,
    property_pid        varchar(15),
    gnaf_property_pid   varchar(15),
    primary_secondary   varchar(1)
);
CREATE INDEX IF NOT EXISTS address_detail_street_ix ON address_detail (street_locality_pid);
CREATE INDEX IF NOT EXISTS address_detail_locality_ix ON address_detail (locality_pid);

CREATE TABLE IF NOT EXISTS address_default_geocode (
    address_default_geocode_pid varchar(15) PRIMARY KEY,
    date_created      date        NOT NULL,
    date_retired      date,
    address_detail_pid varchar(15) NOT NULL,
    geocode_type_code varchar(4)  NOT NULL,
    longitude         numeric(11,8),
    latitude          numeric(10,8)
);
CREATE INDEX IF NOT EXISTS address_default_geocode_addr_ix ON address_default_geocode (address_detail_pid);

CREATE TABLE IF NOT EXISTS flat_type_aut   (code varchar(7)  PRIMARY KEY, name varchar(50) NOT NULL, description varchar(30));
CREATE TABLE IF NOT EXISTS street_type_aut (code varchar(15) PRIMARY KEY, name varchar(50) NOT NULL, description varchar(15));
CREATE TABLE IF NOT EXISTS level_type_aut  (code varchar(4)  PRIMARY KEY, name varchar(50) NOT NULL, description varchar(30));
