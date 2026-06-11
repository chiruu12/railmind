# Contracts — read this first (every workstream agent)

The frozen inter-workstream surface. Source of truth:

- `backend/app/contracts/entities.py` — domain entities (Pydantic)
- `backend/app/contracts/events.py` — bus topics + payloads (Pydantic)
- `frontend/src/api/types.ts` — TS mirror of both
- This file — conventions + how to build against the contracts in isolation

**Do not edit any of these files.** If a contract blocks you, stub around it and report in your final summary; the main session owns changes.

## Conventions

- **Time:** all timestamps are naive ISO datetimes in IST sim-time (sim day `2026-06-13`, sim starts `08:00`). The sim clock is the only clock; never use wall time for domain logic. `EventEnvelope.ts` (wire timestamp) is UTC wall time.
- **Sim speed:** 1.0 = 1 sim-minute per real second (configurable via `POST /api/sim/speed`).
- **IDs:** trains by `number` (str), crews by `id` (`CR-xxx`), decisions `dec-NNNN` (zero-padded counter).
- **Headway rule:** a platform is occupied from `arrival` to `departure` + 5 min buffer. Overlap = conflict.
- **Duty rule:** crew duty ends `duty_start + max_duty_hours`; projected end-of-run past that = breach. Swaps only at a station where a spare crew (`status=spare`) is based (`home_station`).
- **Origin boarding:** a train occupies its origin platform from 15 sim-min before `sched_departure`.
- **Python:** 3.12, FastAPI, Pydantic v2, `uv run` for everything, tests with pytest (`uv run pytest`). Line length 100 (ruff).
- **Frontend:** React 19 + TS + Vite + Tailwind v4 (via `@tailwindcss/vite`, no config file). Dev server proxies `/api` and `/ws` to :8000.
- **Secrets:** via `.env` at `backend/` cwd (pydantic-settings, see `backend/app/settings.py`). Never hardcode keys. `AGENT_LLM=off` disables LLM calls (templated rationale fallback) — default for tests.

## Bus usage

```python
from app.bus.bus import EventBus
from app.contracts.events import DelayDetected

bus = EventBus()
bus.subscribe("delay.detected", my_async_handler)   # or "*" for everything
await bus.publish(DelayDetected(train_number="12302", delay_min=25,
                                cause="loco failure", downstream_stops=["CNB", "PRYJ", "DDU"]))
```

The bus interface (`subscribe`, `publish`, `EventBus.envelope`) is frozen; WS2 may extend internals (audit, replay) but not break it. Components receive the bus instance via constructor injection — never create module-level singletons (except the one in `main.py`).

## Topics

| Topic | Payload class | Publisher → Subscribers |
|---|---|---|
| `sim.tick` | SimTick | Sim → UI |
| `train.position` | TrainPosition | Sim → UI |
| `train.status` | TrainStatusChanged | Sim → Train Agents, UI |
| `delay.detected` | DelayDetected | Train Agent → Station/Crew/Orchestrator, UI |
| `platform.conflict` | PlatformConflict | Station Agent → Orchestrator, UI |
| `platform.reassigned` | PlatformReassigned | Station Agent → Sim, Passenger Agent, UI |
| `crew.duty_breach` | CrewDutyBreach | Crew Agent → Orchestrator, UI |
| `crew.swapped` | CrewSwapped | Crew Agent → Sim, Passenger Agent, UI |
| `passenger.alert` | PassengerAlert | Passenger Agent → UI |
| `agent.thought` | AgentThought | all agents → UI (live feed) |
| `decision.proposed` | DecisionProposed | agents → Orchestrator, UI |
| `decision.resolved` | DecisionResolved | Orchestrator/human → agents, UI |
| `scenario.injected` | ScenarioInjected | API → Sim |
| `kpi.updated` | KPIUpdated | Sim → UI |

## REST surface (WS2 implements; others consume)

| Route | Notes |
|---|---|
| `GET /api/state` | → `NetworkState` |
| `GET /api/trains/{number}` | → `Train` |
| `GET /api/decisions` | → `AgentDecision[]` (newest first) |
| `POST /api/scenarios` | body `{scenario_type, params}` → publishes `scenario.injected` |
| `POST /api/decisions/{id}/resolve` | body `{status: "approved"\|"rejected", note?}` |
| `POST /api/chat` | body `{message, session_id}` → `{reply}` (WS5) |
| `POST /api/voice` | multipart audio → `{reply_text, reply_audio_b64}` (WS5) |
| `WS /ws` | stream of `EventEnvelope` JSON |
| `POST /api/sim/start` · `/api/sim/pause` · `/api/sim/reset` · `/api/sim/speed` (`{speed}`) | sim control |

## Working in isolation

- **Backend WS:** instantiate your own `EventBus`, publish fixture events from `tests/fixtures.py` examples (create them under your own dir if needed).
- **WS4 (frontend):** write `scripts/mock_ws.py` first — a tiny websockets server on **:8001** replaying a canned demo-cascade event sequence (use payload examples from `events.py`); point the WS hook at it via `VITE_WS_URL` env override.
- **Ports:** backend 8000 · frontend 5173 · mock WS 8001. Don't bind anything else.
- **Dependencies are pre-installed.** Do not run `uv add`/`pnpm add` — if you truly need a package, note it in your final summary instead.
- **Do not commit** — the main session handles git.
- Seed data lives in `data/` (timetable.json, stations.csv, crews.json). The `_comment` keys explain the engineered demo conflict.
