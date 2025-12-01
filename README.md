# JigglyLogin
A digital sign-in system built for Raiding Doncaster and Beyond. Trainer data is guarded by the soothing lullabies of Jigglypuff.

# Google Auth cmds
to log in: gcloud auth login
setting project: gcloud config set project pogo-passport

# Deploy
Make sure cmd is executable: chmod +x deploy.sh
Use ./deploy.sh to deploy to Google

"Signup failed due to server error. Please try again shortly"

# When using codex to push, and you need to pull from git to codespaces run:
git status
git fetch origin
git reset --hard origin/main

# Check old deployments and revisions
run in terminal for table of revisions, time and traffic %:
gcloud run revisions list --service jigglylogin --region europe-west1

To send traffic back to an old version:
gcloud run services update-traffic jigglylogin \
  --to-revisions REVISION_NAME=100 \
  --region europe-west1

Confirm cmd:
gcloud run services describe jigglylogin --region europe-west1

# See Logs:
error only logs:
./logs-errors.sh

Logs debug:
./logs-debug.sh

# Update Commands
pip install --upgrade pip

## Digital Reward Codes Inbox Drops
The admin dashboard now exposes a panel for uploading comma-separated reward codes and sending them via inbox notifications. To enable it:

1. Ensure Supabase is configured (`USE_SUPABASE=1`) and create the `digital_reward_codes` table (RLS disabled):
   ```sql
   create table if not exists public.digital_reward_codes (
     id uuid primary key default gen_random_uuid(),
     code text unique not null,
     status text not null default 'AVAILABLE',
     category text not null default 'General',
     batch_label text,
     uploaded_by text,
     assigned_to text,
     assigned_by text,
     assigned_by_type text,
     assigned_source text,
     redeemed_at timestamptz,
     notification_id uuid,
     notification_subject text,
     created_at timestamptz not null default timezone('utc', now()),
     updated_at timestamptz not null default timezone('utc', now())
   );

   create index if not exists digital_reward_codes_status_idx on public.digital_reward_codes(status);
   create index if not exists digital_reward_codes_category_idx on public.digital_reward_codes(category);
   create index if not exists digital_reward_codes_assigned_to_idx on public.digital_reward_codes(assigned_to);
   create index if not exists digital_reward_codes_source_idx on public.digital_reward_codes(assigned_source);

   alter table public.digital_reward_codes disable row level security;
   ```
2. Optional: cap uploads per batch with `DIGITAL_CODE_UPLOAD_LIMIT` (default `400`) or change the table name via `DIGITAL_CODE_TABLE`.
3. Admins can paste `CODE1,CODE2,…`, label the batch, pick a category bucket, and assign codes using `{code}` inside the inbox message template. Category buckets and source tags now track who redeemed a code, through which feature, and when it happened.
