# Deployment

Two pieces, two hosts:

- **Backend** (FastAPI: REST + WebSocket `/ws` + the always-on sim tick loop) →
  **Render** (free web service). Vercel functions can't host this — no WebSocket
  servers, no persistent background loops, no shared in-memory state.
- **Frontend** (static React build) → **Vercel** (Hobby/free).

```
Browser ──HTTP /api──▶ Render backend  (VITE_API_BASE)
        ──WS  /ws ───▶ Render backend  (VITE_WS_URL)
Vercel serves the static React bundle.
```

## 1. Backend → Render

Render deploys from a Git repo via the `render.yaml` Blueprint at the repo root.

1. Commit and push this repo to GitHub (Render needs a remote).
2. Render dashboard → **New + → Blueprint** → pick this repo. It reads
   `render.yaml` and creates the `railmind-backend` web service (Docker).
3. After the first deploy, set the secret env vars in the service's
   **Environment** tab (left `sync: false` in the blueprint so they stay out of git):
   - `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`
   - Flip `AGENT_LLM` to `on` once keys are set (defaults to `off` = rule-only).
4. Backend URL will be `https://railmind-backend.onrender.com`
   (verify the exact host in the dashboard). Check `…/health` returns
   `{"status":"ok"}`.

**Free-tier note:** the service spins down after ~15 min idle (~30–60s cold
start). Open the URL ~1 min before a demo to wake it. The sim boots paused; press
Start in the control room when ready. State resets if it sleeps mid-session.

Local image sanity check (optional, build context is the repo root):

```bash
docker build -f backend/Dockerfile -t railmind-backend .
docker run --rm -p 8099:8000 -e PORT=8000 railmind-backend
curl localhost:8099/health
```

## 2. Frontend → Vercel

The frontend reaches the backend via two build-time env vars (set in the Vercel
project → **Settings → Environment Variables**, then redeploy):

| Var | Value |
|-----|-------|
| `VITE_API_BASE` | `https://railmind-backend.onrender.com` |
| `VITE_WS_URL`   | `wss://railmind-backend.onrender.com/ws` |

Leave both **unset locally** — the Vite dev server proxies `/api` and `/ws` to
`:8000` (see `vite.config.ts`). The Vercel project's root directory is `frontend/`.

CORS is already open to `*.vercel.app` in `backend/app/main.py`.
