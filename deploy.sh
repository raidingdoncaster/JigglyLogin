#!/bin/bash
set -e

PROJECT_ID="pogo-passport"
SERVICE_NAME="jigglylogin"
REGION="europe-west1"

# Generate a version tag (date + short git commit hash)
VERSION_TAG=$(date +%Y%m%d-%H%M)-$(git rev-parse --short HEAD)

echo "🔄 Syncing with GitHub..."
git pull origin main
git add .
git commit -m "Auto-deploy update ($VERSION_TAG)" || echo "⚠️ No changes to commit"
git push origin main

echo "🚀 Building and deploying $SERVICE_NAME:$VERSION_TAG to Google Cloud Run..."

# Build and push container with version tag
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME:$VERSION_TAG

# Deploy to Cloud Run
gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/$SERVICE_NAME:$VERSION_TAG \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated

echo "✅ Deployment finished!"
echo "👉 URL: https://$SERVICE_NAME-56781668488.$REGION.run.app"
echo "📌 Version deployed: $VERSION_TAG"