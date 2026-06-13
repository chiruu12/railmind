# Rail Saarthi — Submission Pitch Pack

Copy-paste-ready text for the FAR AWAY 2026 submission form, plus judge Q&A prep.

## 100-word description

Rail Saarthi is a multi-agent control room for Indian Railways. Autonomous Train, Station, Crew, and Passenger-Info agents negotiate over a live digital twin of the Delhi–Kanpur–Prayagraj corridor: detecting delays, reallocating platforms, swapping crews, and alerting passengers by text and voice — with every decision streamed live, human-overridable, and audit-logged. The safety architecture is the core innovation: deterministic rules compute what's feasible, LLMs choose among safe options and explain why, so an unsafe action is structurally impossible. Built on Hive, our own open-source agent framework, addressing a network of 19 million daily passengers whose punctuality has fallen below 74%.

*(~100 words)*

## 250-word description

Indian Railways moves 19 million passengers daily across 69,000 km, yet punctuality has collapsed from ~94.2% (2020) to ~73.6% (2023). Over 80% of critical routes run beyond capacity, so a single delayed train cascades into platform conflicts, crew duty-hour violations, and uninformed passengers — and today, humans untangle every cascade by phone and intuition.

Rail Saarthi puts a team of AI agents in the control room. On a live digital twin of the New Delhi–Kanpur–Prayagraj–DDU corridor (8 real trains, 4 stations), autonomous agents handle disruption end to end: a Train Agent detects a delay and projects downstream impact; the Station Agent finds the platform conflict and negotiates a reassignment; the Crew Agent catches the 9-hour duty breach and schedules a swap with a spare crew; an Orchestrator approves; the Passenger-Info Agent broadcasts alerts — and passengers can literally ask the system "Where is my train?" by voice (Deepgram STT/TTS).

Three design choices set Rail Saarthi apart. First, safety: deterministic rules (headway buffers, duty math) compute the feasible options; LLMs only choose among them and explain their reasoning — infeasible actions are structurally impossible. Second, transparency: every agent's reasoning streams live to the dashboard, every decision is human-overridable, and everything persists to an audit log. Third, foundation: it runs on Hive, our own open-source agent framework.

Research shows multi-agent dispatch can lift network throughput ~34% over naive methods. Rail Saarthi is the working, watchable, override-able version of that idea — one corridor today, architected for the network tomorrow.

*(~245 words)*

## Problem

- **Scale under stress:** 19M daily passengers, 69,000 km of track; punctuality down from ~94.2% (2020) to ~73.6% (2023); 80%+ of critical routes over capacity (22% above 150%).
- **Cascades, not incidents:** one 25-minute delay creates a platform clash at the next junction, pushes a crew past its 9-hour legal duty limit, and leaves passengers guessing. Each knock-on is handled manually and separately today — timetabling, platforming, crew rostering, and passenger info are siloed systems stitched together by phone calls.
- **Stakes:** delays compound network-wide on shared passenger/freight track; crew fatigue is a safety issue; passengers get reactive, minimal information.

## Solution

A multi-agent operating layer over a railway digital twin:

- **Train Agents** watch every train, detect slippage, project downstream impact deterministically.
- **Station Agents** manage platform boards, detect occupancy conflicts (5-minute headway rule), and negotiate reassignments from rule-validated candidates.
- **A Crew Agent** enforces duty law (max 9 hours), finds spare crews at the right stations, proposes swaps.
- **An Orchestrator** (Claude) arbitrates between agents, approves or rejects proposals, and computes the KPI delta vs a no-agent baseline.
- **A Passenger-Info Agent** turns operational events into human alerts and answers live questions by text and voice.
- **A human operator** sits above it all with one-click override and a complete audit trail.

## How it works

FastAPI backend hosts the digital twin (tick-based sim, 1 sim-minute/second), an in-process async event bus, and the agent layer built on **Hive** (github.com/chiruu12/Hive) behind a thin runtime adapter. Every event hits the bus → fans out to subscribing agents, the WebSocket layer (live React dashboard: Leaflet corridor map, platform Gantt, streaming agent feed, KPI strip), and a SQLite audit log. Agents follow a strict loop: **rules compute feasible candidates → LLM picks one and writes the rationale → Orchestrator/human resolves → sim applies the change**. LLM routing: Groq for fast agent turns, Claude where reasoning quality is visible, OpenAI as fallback, and a rule-only templated mode (`AGENT_LLM=off`) so the system runs with zero API keys. Voice is Deepgram STT in, Aura TTS out, grounded in live twin state.

## Impact

- **Operations:** automated cascade handling — in the demo scenario, one injected delay is fully resolved (re-platform + crew swap + passenger comms) with zero required human input, and the KPI panel quantifies knock-on delay avoided vs the naive baseline live.
- **Safety & labor:** duty-hour violations are caught at projection time, not after the fact; every decision is traceable.
- **Passengers:** proactive platform/ETA alerts and a voice assistant instead of reactive enquiry boards.
- **Ceiling:** published multi-agent dispatch research reports ~34% throughput gains over naive dispatching — that's the prize at network scale.

## Honesty table: real today vs roadmap

| Capability | Today (working demo) | Roadmap |
|---|---|---|
| Network | 1 corridor digital twin: NDLS–CNB–PRYJ–DDU, 8 trains, 4 stations, simulated | Real corridors via NTES/FOIS/COA feeds; multi-corridor, then zonal |
| Data | Fabricated timetable with real train numbers + real station coordinates; sim is source of truth | Live running status, real rosters, sensor/SCADA ingestion |
| Delay prediction | Deterministic ETA projection from current delay | ML predictor trained on historical running data (pluggable slot in the twin) |
| Agents | Train, Station, Crew, Passenger-Info, Orchestrator | + Freight (DFC routing), Maintenance (predictive), Emergency response |
| Safety assurance | Rules-validate-LLM-decides + human override + audit log | Formal verification of the deterministic core (CBTC-style) |
| Infra | Single FastAPI process, in-process bus, SQLite | Kafka/NATS bus, horizontally scaled agent services, production datastores |
| KPI claims | Measured live in-sim vs naive baseline for the demo scenario | Validated on pilot-corridor real data |

## Anticipated judge Q&A

**Q: Why multiple agents instead of one big model?**
Because the problem is structurally distributed — stations, trains, and crews have local state, local constraints, and conflicting interests, exactly like the human org chart they mirror. Decomposition keeps each agent's context small and fast (Groq-speed turns), lets them run concurrently, and makes the negotiation *visible* — judges and operators watch agents argue and resolve, which is also the published direction for rail: junction-level multi-agent systems showed ~34% throughput gains over naive dispatching. A single model would be an opaque, slow, unauditable blob.

**Q: How do you stop an LLM from making an unsafe dispatch decision?**
It can't — by construction. Feasibility (platform occupancy with 5-minute headway buffers, 9-hour crew duty math, swap-location rules) is computed by deterministic code over twin state. The LLM only selects among pre-validated options and writes the rationale. On top of that: the Orchestrator is a single approval point, humans can reject anything, every decision is audit-logged with options-considered, and if all LLM providers fail the agents degrade to pure rules. Long-term, that deterministic core is exactly the piece you formally verify.

**Q: This is simulated. What's the path to real data?**
Deliberate MVP choice — there's no public real-time dispatch API, and a sim lets us inject reproducible disruptions. But the seam is clean: the twin is fed by a data layer, and the agents only see twin state. Swapping the sim feed for NTES live running status (plus FOIS for freight, COA for crews — all identified in our research) changes the ingestion layer, not the agents. Step one of the roadmap is a 5-station pilot corridor with real feeds in partnership with the railway. Station coordinates are already real (Datameet open data).

**Q: Can this scale to 12,000 trains and 7,500 stations?**
The architecture is the scaling story: agents are decoupled by a pub/sub bus, so the in-process asyncio bus swaps for Kafka/NATS and agents become horizontally scaled services without changing agent logic — that's why everything talks through events, never shared state. Hot-path decisions run on fast inference (Groq today); per-junction decomposition keeps each agent's problem small regardless of network size, which is precisely why the research community converged on junction-level multi-agent designs.

**Q: What did you actually build vs reuse?**
The agent framework itself is ours — Hive (github.com/chiruu12/Hive), open source, supporting multiple LLM providers, tool decorators, and structured output. For this hackathon we built on top of it: the digital twin sim engine, event bus with audit sink, five agents with deterministic tools, the control-room dashboard (map, Gantt, live agent feed, KPI panel), human override flow, and the voice passenger assistant.

**Q: What happens when the LLM is wrong or hallucinates?**
A wrong *choice* among feasible options is suboptimal, never unsafe — and the Orchestrator plus human override catch it. A hallucinated action is impossible because actions execute only through typed tools over validated candidates. The audit log makes every miss reviewable afterward, which is how a system like this earns operational trust incrementally: assist first, automate later.

**Q: Why should we believe the KPI numbers?**
The comparison is measured, not asserted: the same sim runs a naive no-agent baseline (FIFO platforming, no proactive crew action), and the KPI panel shows the live delta for the scenario on screen. We don't claim real-world percentages — the ~34% figure we cite is from published research, labeled as such, and our roadmap validates our own numbers on pilot real data.
