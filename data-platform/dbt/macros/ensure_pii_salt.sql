{#-
    Creates the secret salt that mask_pii mixes into every hash: one random row, kept across builds so a customer's
    hash stays the same. No reader role has a grant on ops.pii_salt; the view owner (the dbt role) reads it.
-#}
{% macro ensure_pii_salt() -%}
    create schema if not exists ops;
    create table if not exists ops.pii_salt (
        id boolean primary key default true check (id),
        salt text not null check (salt <> '')
    );
    insert into ops.pii_salt (salt) values (gen_random_uuid()::text || gen_random_uuid()::text) on conflict do nothing;
{%- endmacro %}
