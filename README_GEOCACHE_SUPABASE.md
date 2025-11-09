# Geocache Quest – SB SQL -- internal only


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

## Act II Data Seeds

Populate these Supabase rows so the new minigames function:

- Technical assets (locations, artifact codes/NFC) are baked into `data/geocache_assets.json`; edit that file for any on-site changes. Supabase rows are optional if you prefer database management.
- Location IDs referenced in the story (`doncaster_minster`, `mansion_house_trail`, `lovers_statue`) should stay consistent across front-end and any admin tooling. The backend stores rounded coordinates from the browser check-in, so you only need a separate table if you plan on validating ranges server-side later.
- Additional Act III locations: `pink_bike_stage`, `sir_nigel_square`.
- Act VI location: `market_hub` (market return check-in).
  - The final battle is handled in-app; no extra artifact rows required.

> ℹ️ Location checks now enforce the latitude/longitude and `radius_m` configured in `data/geocache_story.json`. Keep the coordinates up to date for each venue to avoid “location_out_of_range” errors during live events.

## Manual Testing Checklist

1. **Feature flag** – export `USE_GEOCACHE_QUEST=1` (plus Supabase creds) and restart the Flask app. Visit `/geocache` to confirm the quest shell loads.
2. **Account creation/login** – from the quest start screen create a profile (trainer + PIN + optional campfire). Verify:
   - New row in `sheet1` (trainer, pin_hash).
   - New row in `geocache_profiles` (metadata contains last_session_id after first sync).
3. **Act I progression** – scan compass, mark puzzle complete, accept mission:
   - `geocache_sessions.progress_flags` contains `compass_found` and `compass_repaired`.
   - Act advance to II only allowed once flags exist.
4. **Act II path** – perform location check-ins (Minster, Mansion, Lovers), solve riddle, focus test, sigils:
   - Each flag appears in `progress_flags`.
   - Attempting to jump to Act III without required flags returns a 409 from `/geocache/session`.
5. **Act III path** – location/tap tasks at Pink Bike, defeat Eldarni, recover Sigil of Might:
   - `illusion_battle_won` recorded with `hits`.
   - Act advance to VI only when all flags present.
6. **Act VI finale** – check in at market, defeat Dr Order, choose ending:
   - Combat mini-game updates `order_defeated`.
   - Ending selection writes `ending_choice` and epilogue data into `progress_flags.ending_selected`.
7. **Resume flow** – refresh, use “Reload save”, enter PIN, confirm state hydrates.
8. **Admin link** – log into `/admin_dashboard` and use the “Geocache Quest” card to open `/geocache` in a new tab.
