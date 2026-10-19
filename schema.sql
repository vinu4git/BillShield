-- Run once in the Supabase SQL editor.
-- USER -> PROMISE, USER -> BILL -> LINE_ITEM, BILL -> MESSAGE -> (MESSAGE_LINE_ITEM) -> LINE_ITEM

create table users (
  id bigint generated always as identity primary key,
  name text,
  email text unique,
  created_at timestamptz default now()
);

-- What they were promised. A new row when the promise changes, so history is kept.
create table promises (
  id bigint generated always as identity primary key,
  user_id bigint not null references users(id),
  provider text,                          -- e.g. Airtel, BESCOM
  text text not null,                     -- in their words
  valid_from timestamptz not null default now(),
  created_at timestamptz default now()
);

create table bills (
  id bigint generated always as identity primary key,
  user_id bigint not null references users(id),
  promise_id bigint references promises(id),
  provider text,
  period_start date,
  period_end date,
  due_date date,
  printed_total numeric(12, 2),           -- as printed on the bill
  computed_total numeric(12, 2),          -- sum computed by code
  file_path text,                         -- Storage link; the bytes live in a file
  uploaded_at timestamptz default now()
);

-- Facts read off the bill. Written once, never versioned.
create table line_items (
  id bigint generated always as identity primary key,
  bill_id bigint not null references bills(id) on delete cascade,
  description text,                       -- masked before saving
  charge_key text,                        -- normalised by code, matches a charge across bills
  kind text not null default 'charge'
    check (kind in ('charge', 'tax', 'discount', 'subtotal', 'total')),
  quantity numeric(12, 3) default 1,
  amount numeric(12, 2) not null,         -- numeric, not float: money must add up exactly
  is_flagged boolean not null default false,
  flag_reason text
    check (flag_reason in ('total_mismatch', 'promise_mismatch', 'unexplained')),
  expected_amount numeric(12, 2),         -- from the promise, or the computed total
  difference numeric(12, 2),              -- computed by code
  plain_words text                        -- the AI's explanation of the line
);

-- One row per draft. Revise adds a version; Accept marks it sent.
create table messages (
  id bigint generated always as identity primary key,
  bill_id bigint not null references bills(id) on delete cascade,
  version int not null default 1,
  is_latest boolean not null default true,
  draft_text text not null,               -- as proposed
  sent_text text,                         -- as accepted
  words_changed int,                      -- computed by code
  status text not null default 'draft' check (status in ('draft', 'sent')),
  created_at timestamptz default now(),
  sent_at timestamptz,
  unique (bill_id, version)
);

-- The proof link: which lines a message disputes.
create table message_line_items (
  message_id bigint references messages(id) on delete cascade,
  line_item_id bigint references line_items(id) on delete cascade,
  primary key (message_id, line_item_id)
);

create index promises_user_provider on promises (user_id, provider, valid_from desc);
create index line_items_charge_key on line_items (charge_key);
create index messages_bill_latest on messages (bill_id) where is_latest;

-- Private bucket for the uploaded bills.
insert into storage.buckets (id, name, public) values ('bills', 'bills', false)
on conflict (id) do nothing;

-- Hardcoded user until auth lands.
insert into users (name, email) values ('Demo', 'demo@example.com');
