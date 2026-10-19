-- Run once in the Supabase SQL editor.
-- Mirrors the USER -> BILL -> LINE_ITEM design. Names are snake_case for Postgres.

create table users (
  id bigint generated always as identity primary key,
  name text,
  email text unique,
  created_at timestamptz default now()
);

create table bills (
  id bigint generated always as identity primary key,
  user_id bigint not null references users(id),
  uploaded_at timestamptz default now(),
  expectation text,                       -- what they were promised, in their words
  final_message text,                     -- draft while status = 'D', the sent text once 'A'
  status char(1) not null default 'D'
    check (status in ('D', 'A')),         -- D = draft proposed, A = accepted
  file_path text,                         -- the bill file lives in Storage; the row holds the link
  words_changed int,                      -- proof: words edited before accepting
  accepted_at timestamptz
);

create table line_items (
  id bigint generated always as identity primary key,
  bill_id bigint not null references bills(id) on delete cascade,
  quantity int default 1,
  description text,
  amount numeric(12, 2) not null,         -- numeric, not float: money must add up exactly
  add_deduct boolean not null default true, -- true = adds to the bill, false = discount/credit
  computed boolean not null default false,  -- true = a printed subtotal/total, checked by code
  is_flagged boolean not null default false,
  message text,                           -- why it was flagged, every number from code
  version int not null default 1,
  is_latest boolean not null default true,
  created_at timestamptz default now()
);

create index line_items_bill_latest on line_items (bill_id) where is_latest;

-- Private bucket for the uploaded bills.
insert into storage.buckets (id, name, public) values ('bills', 'bills', false)
on conflict (id) do nothing;

-- Hardcoded user until auth lands.
insert into users (name, email) values ('Demo', 'demo@example.com');
