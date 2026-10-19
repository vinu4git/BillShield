-- Run once in the Supabase SQL editor if you already ran the earlier schema.sql.
-- Each user gets a random token; the page sends it with every call instead of a guessable id.
alter table users add column token uuid not null default gen_random_uuid();
alter table users add constraint users_token_key unique (token);
alter table users alter column name set not null;
alter table users alter column email set not null;
