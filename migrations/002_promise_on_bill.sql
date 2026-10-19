-- Run once in the Supabase SQL editor after 001.
-- The promise moves onto the bill; the promises table goes.
alter table bills add column promise text;
update bills set promise = p.text from promises p where p.id = bills.promise_id;
alter table bills alter column promise set not null;
alter table bills drop column promise_id;
drop table promises;
create index bills_user_provider on bills (user_id, provider, uploaded_at);
