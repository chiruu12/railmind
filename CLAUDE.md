# Rail Saarthi — Agentic OS for Indian Railways

Hackathon MVP (FAR AWAY 2026, submission ~June 14). Multi-agent control room over a simulated railway corridor: Train/Station/Crew/Passenger agents negotiate delays, platforms, and crew swaps on a live digital twin.

**Read first:** `docs/SPEC.md` (what we build), `docs/AGENT_PLAN.md` (who builds what), `docs/CONTRACTS.md` (frozen interfaces + conventions — mandatory for all workstream agents).

## Directory Structure

- `backend/` — FastAPI app (Python 3.12, uv). `app/contracts/` (FROZEN), `app/sim/` (WS1), `app/bus/` + `app/api/` (WS2), `app/agents/` (WS3, voice in WS5)
- `frontend/` — React 19 + TS + Vite + Tailwind v4. `src/api/` (frozen types), `src/features/control-room/` (WS4), `src/features/passenger/` (WS5)
- `data/` — seed corridor (timetable.json, stations.csv, crews.json)
- `scripts/` — seed, mock_ws, demo runner
- `docs/` — spec, plan, contracts, demo script

## Tech Stack

FastAPI + Pydantic v2 + SQLModel/SQLite · in-process asyncio event bus · Hive agents behind `AgentRuntime` adapter (Agno fallback) · Groq/Anthropic/OpenAI LLMs · Deepgram voice · React/Leaflet/Zustand frontend

## Development

- `make dev` — backend :8000 + frontend :5173
- `make test` — backend pytest · `make lint` — ruff
- Backend deps: `uv` only. Frontend: `pnpm`. Workstream agents must NOT add dependencies.
- Secrets in `backend/.env` (copy from `.env.example`). `AGENT_LLM=off` for LLM-free runs/tests.

## Conventions

- Contracts (`backend/app/contracts/`, `frontend/src/api/types.ts`) are frozen; changes only via main session
- All domain time is naive IST sim-time (sim day 2026-06-13); never wall clock
- Components get the `EventBus` via constructor injection
- Workstream agents stay inside their "Owns" dirs (AGENT_PLAN.md) and do not commit
- Read existing files before creating new ones — match patterns
- Keep commit messages short: one line, under 50 characters when possible
- Describe WHAT shipped, not HOW you got there
- No multi-paragraph commit bodies unless truly necessary
- Never expose internal process or iteration history in public-facing output

## Testing

- Backend: pytest under `backend/tests/`, `asyncio_mode=auto`, LLM calls always behind `AGENT_LLM` flag (off in tests)
- Frontend: `pnpm build` must pass (tsc); visual checks against `scripts/mock_ws.py`

## Architecture Decisions

- Deterministic rules compute feasible options; LLMs only choose between them and explain (demo can never produce an infeasible assignment)
- Single process, no external infra (no Kafka/K8s/Postgres) — demo reliability over scale
- Provider fallback chain: Groq → Anthropic → OpenAI → rule-only templates
