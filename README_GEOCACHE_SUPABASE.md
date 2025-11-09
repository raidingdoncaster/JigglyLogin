# Geocache Quest – Supabase Setup

Copy the SQL below into the Supabase SQL editor (or your migration tool) to create the tables, indexes, triggers, and policies required for the geocache quest feature.

```sql
-- 0. Extensions (uuid + hashing helpers)
create extension if not exists pgcrypto;
create extension if not exists "uuid-ossp";

-- 1. Profiles: one row per quest player
create table if not exists public.geocache_profiles (
    id uuid primary key default gen_random_uuid(),
    trainer_name text not null,
    trainer_name_lc text generated always as (lower(trim(trainer_name))) stored,
    campfire_name text,
    pin_hash text not null,
    rdab_user_id uuid,
    metadata jsonb default '{}'::jsonb,
    created_at timestamptz default timezone('utc', now()),
    updated_at timestamptz default timezone('utc', now())
);

create unique index if not exists geocache_profiles_trainer_lc_idx
    on public.geocache_profiles (trainer_name_lc);

create index if not exists geocache_profiles_rdab_user_idx
    on public.geocache_profiles (rdab_user_id);

-- 2. Quest session state (one active row per profile; keep history)
create table if not exists public.geocache_sessions (
    id uuid primary key default gen_random_uuid(),
    profile_id uuid not null references public.geocache_profiles(id) on delete cascade,
    current_act int not null default 1,
    last_scene text,
    branch text,
    choices jsonb not null default '{}'::jsonb,
    inventory jsonb not null default '{}'::jsonb,
    progress_flags jsonb not null default '{}'::jsonb,
    ending_choice text,
    ended_at timestamptz,
    created_at timestamptz default timezone('utc', now()),
    updated_at timestamptz default timezone('utc', now())
);

create index if not exists geocache_sessions_profile_idx
    on public.geocache_sessions (profile_id);

create index if not exists geocache_sessions_current_act_idx
    on public.geocache_sessions (current_act);

-- 3. Optional event log (useful for audit / analytics)
create table if not exists public.geocache_session_events (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references public.geocache_sessions(id) on delete cascade,
    event_type text not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz default timezone('utc', now())
);

create index if not exists geocache_session_events_session_idx
    on public.geocache_session_events (session_id, created_at);

-- 4. Catalog of physical / digital triggers (compass codes, sigils, etc.)
create table if not exists public.geocache_artifacts (
    id uuid primary key default gen_random_uuid(),
    slug text not null,
    display_name text not null,
    code text,
    nfc_uid text,
    location_hint text,
    location_lat numeric(9,6),
    location_lng numeric(9,6),
    metadata jsonb default '{}'::jsonb,
    created_at timestamptz default timezone('utc', now()),
    updated_at timestamptz default timezone('utc', now())
);

create unique index if not exists geocache_artifacts_slug_idx
    on public.geocache_artifacts (slug);

create index if not exists geocache_artifacts_code_idx
    on public.geocache_artifacts (code);

-- 5. Trigger helper to keep updated_at fresh
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists trg_touch_geocache_profiles on public.geocache_profiles;
create trigger trg_touch_geocache_profiles
before update on public.geocache_profiles
for each row execute function public.touch_updated_at();

drop trigger if exists trg_touch_geocache_sessions on public.geocache_sessions;
create trigger trg_touch_geocache_sessions
before update on public.geocache_sessions
for each row execute function public.touch_updated_at();

drop trigger if exists trg_touch_geocache_artifacts on public.geocache_artifacts;
create trigger trg_touch_geocache_artifacts
before update on public.geocache_artifacts
for each row execute function public.touch_updated_at();

-- 6. Row-Level Security (service-role + future auth usage)
alter table public.geocache_profiles enable row level security;
alter table public.geocache_sessions enable row level security;
alter table public.geocache_session_events enable row level security;
alter table public.geocache_artifacts enable row level security;

-- service_role: full access
drop policy if exists "service_role_all_geocache_profiles" on public.geocache_profiles;
create policy "service_role_all_geocache_profiles"
    on public.geocache_profiles
    for all
    using (true)
    with check (true);

drop policy if exists "service_role_all_geocache_sessions" on public.geocache_sessions;
create policy "service_role_all_geocache_sessions"
    on public.geocache_sessions
    for all
    using (true)
    with check (true);

drop policy if exists "service_role_all_geocache_events" on public.geocache_session_events;
create policy "service_role_all_geocache_events"
    on public.geocache_session_events
    for all
    using (true)
    with check (true);

drop policy if exists "service_role_all_geocache_artifacts" on public.geocache_artifacts;
create policy "service_role_all_geocache_artifacts"
    on public.geocache_artifacts
    for all
    using (true)
    with check (true);

-- Optional: if you later map rdab_user_id to Supabase auth.uid()
drop policy if exists "user_read_profile" on public.geocache_profiles;
create policy "user_read_profile"
    on public.geocache_profiles
    for select using (auth.uid() = rdab_user_id);

drop policy if exists "user_write_profile" on public.geocache_profiles;
create policy "user_write_profile"
    on public.geocache_profiles
    for all using (auth.uid() = rdab_user_id)
    with check (auth.uid() = rdab_user_id);

drop policy if exists "user_read_session" on public.geocache_sessions;
create policy "user_read_session"
    on public.geocache_sessions
    for select using (
        exists (
            select 1 from public.geocache_profiles p
            where p.id = geocache_sessions.profile_id
              and p.rdab_user_id = auth.uid()
        )
    );

drop policy if exists "user_write_session" on public.geocache_sessions;
create policy "user_write_session"
    on public.geocache_sessions
    for all using (
        exists (
            select 1 from public.geocache_profiles p
            where p.id = geocache_sessions.profile_id
              and p.rdab_user_id = auth.uid()
        )
    )
    with check (
        exists (
            select 1 from public.geocache_profiles p
            where p.id = geocache_sessions.profile_id
              and p.rdab_user_id = auth.uid()
        )
    );

-- 7. Convenience view for dashboards (optional but handy)
drop view if exists public.geocache_session_summary;
create view public.geocache_session_summary as
select
    s.id as session_id,
    s.profile_id,
    p.trainer_name,
    p.campfire_name,
    s.current_act,
    s.last_scene,
    s.branch,
    s.ending_choice,
    s.created_at,
    s.updated_at,
    s.ended_at
from public.geocache_sessions s
join public.geocache_profiles p on p.id = s.profile_id;
```

Happy quest building!
