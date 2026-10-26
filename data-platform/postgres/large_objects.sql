-- Large objects are writable storage outside every schema's privileges, so only the superuser may create or write
-- them. Function privileges live in each database, so this runs in the main database (already created from
-- template1) and in template1, which every later CREATE DATABASE (the eval database) copies.
REVOKE EXECUTE ON FUNCTION lo_create(oid), lo_creat(integer), lo_from_bytea(oid, bytea), lo_open(oid, integer),
  lowrite(integer, bytea), lo_put(oid, bigint, bytea) FROM PUBLIC;
