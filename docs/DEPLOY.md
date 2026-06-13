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

## Live URLs

| Piece | URL |
|-------|-----|
| Frontend (control room + passenger app) | https://railmind-demo.vercel.app |
| Backend API + WebSocket | https://railmind-drct.onrender.com |

The frontend reaches the backend via `frontend/src/lib/apiBase.ts` (`API_BASE`
from `VITE_API_BASE`) for HTTP and `frontend/src/api/ws.ts` (`VITE_WS_URL`) for
the WebSocket.

## 1. Backend → Render (Blueprint)

Deploy via the **Blueprint** (`render.yaml`), never a manual service — a manual
service is easy to misconfigure (wrong branch) and was the source of much grief.

1. Render dashboard → **New + → Blueprint** → select `chiruu12/railmind` → Apply.
   `render.yaml` pins everything: Docker, `branch: main`, `autoDeploy: true`,
   region, `/health` check, `AGENT_LLM=on`, `OPENAI_MODEL=gpt-4o-mini`.
2. Set the secret API keys in the service's **Environment** tab (the Blueprint
   marks these `sync: false`): `GROQ_API_KEY`, `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`.
3. First Docker build takes ~5–10 min. Check `…/health` → `{"status":"ok"}`.

`autoDeploy: true` means every push to `main` redeploys automatically.

**Docker build** — context is the **repo root** (`dockerContext: .`,
`dockerfilePath: ./backend/Dockerfile`) because the Dockerfile copies both
`backend/` and `data/`. A `backend/`-only context fails at `COPY data /data`.

**Free-tier note:** the service spins down after ~15 min idle (~30–60s cold
start). Open the URL ~1 min before a demo to wake it. The sim boots paused;
press Start when ready. SQLite lives at `/tmp` (no persistent disk on free tier),
so audit history resets on restart — fine for the demo.

Local image sanity check:

```bash
docker build -f backend/Dockerfile -t railmind-backend .
docker run --rm -p 8099:8000 -e PORT=8000 railmind-backend
curl localhost:8099/health
```

## 2. Frontend → Vercel

Set in the Vercel project → **Settings → Environment Variables** (Production +
Preview), then redeploy:

| Var | Value |
|-----|-------|
| `VITE_API_BASE` | `https://railmind-drct.onrender.com` |
| `VITE_WS_URL`   | `wss://railmind-drct.onrender.com/ws` |

Leave both **unset locally** — the Vite dev server proxies `/api` and `/ws` to
`:8000` (see `vite.config.ts`). Vercel project Root Directory is `frontend`,
framework Vite, output `dist`. CORS allows `*.vercel.app` (`backend/app/main.py`).

> If the env vars must be set/changed, use the **dashboard UI** — the Vercel CLI
> in some environments writes them back empty. A CLI deploy can inject them at
> build time instead:
> `vercel deploy --prod --build-env VITE_API_BASE=… --build-env VITE_WS_URL=…`.

## Verify end-to-end

```bash
BASE=https://railmind-drct.onrender.com
curl -s -X POST $BASE/api/sim/reset; curl -s -X POST $BASE/api/sim/start
curl -s -X POST $BASE/api/scenarios -H 'Content-Type: application/json' \
  -d '{"scenario_type":"delay","params":{"train_number":"12302","delay_min":25,"cause":"loco traction failure"}}'
sleep 6
curl -s -X POST $BASE/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"Why is 12302 late?","session_id":"verify"}'
# expect: "...25 minutes late due to a loco traction failure..."
curl -s -X POST $BASE/api/sim/reset; curl -s -X POST $BASE/api/sim/pause
```

## Fixes log (deployment hardening)

| PR | Fix |
|----|-----|
| #10 | Deploy backend on Render; remove the broken Vercel-serverless attempt |
| #11 | Surface delay cause in passenger assistant replies |
| #12 | Strip markdown (`**`, code, lists) from the agent activity feed (display) |
| #13 | Render injected scenarios as readable text instead of raw JSON |
| #14 | Strip markdown at the source (passenger app + TTS, not just the feed) |
| #15 | Default `OPENAI_MODEL=gpt-4o-mini` — gpt-5* reject the `max_tokens` param |
| #16 | Harden the Render Blueprint (branch `main`, `autoDeploy`, pinned model) |
| #17 | **Root cause:** `SimEngine.delay_causes` is a `@property` but the chat called it as a method `delay_causes()` → `TypeError` swallowed by a broad `except` → cause was never added to context. Read it as a property; align test fixtures. |

Lesson: #17's symptom (cause never appears) looked identical to a stale deploy,
so we chased infra. The tell was a **brand-new Blueprint service still failing** —
that ruled out deployment and pointed at logic. Keep test fakes' interfaces
(method vs property) in lockstep with the real class, or bugs hide behind them.
