#!/usr/bin/env bash
# Deploy RailMind (frontend + backend serverless function) to Vercel.
# Syncs backend/app + data into frontend/api/_src so the Python
# function bundle is self-contained, then deploys from frontend/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

rsync -a --delete --exclude '__pycache__' "$ROOT/backend/app/" "$ROOT/frontend/api/_src/app/"
rsync -a --delete "$ROOT/data/" "$ROOT/frontend/api/_src/data/"

cd "$ROOT/frontend"
npx -y vercel deploy --prod --yes
