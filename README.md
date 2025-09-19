# JigglyLogin
A digital sign-in system built for Raiding Doncaster and Beyond. Trainer data is guarded by the soothing lullabies of Jigglypuff.

# Google Auth cmds
to log in: gcloud auth login
setting project: gcloud config set project pogo-passport

# Deploy
Make sure cmd is executable: chmod +x deploy.sh
Use ./deploy.sh to deploy to Google

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