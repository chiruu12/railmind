# RailMind — 3-Minute Demo Runbook

Exact beats, clicks, and spoken lines for the live demo / submission video. Total runtime: ~3:00.

## Pre-demo checklist (do all of this BEFORE you start talking)

- [ ] `backend/.env` in place with all four keys (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`) and `AGENT_LLM=on`
- [ ] `make dev` running — backend healthy on :8000, frontend on :5173
- [ ] Reset the sim to a clean state: `POST /api/sim/reset` (or the Reset button in the scenario panel) — sim clock back at 08:00, all trains on schedule
- [ ] Sim running (`POST /api/sim/start` / Start button); trains visibly moving on the map
- [ ] **Two browser tabs ready:** Tab 1 = control room (`localhost:5173`), Tab 2 = passenger view (`localhost:5173/passenger`)
- [ ] Mic permission already granted in Tab 2 (test one voice round-trip beforehand, then reset the sim again)
- [ ] Audio output audible (TTS reply must be heard on the recording)
- [ ] Close every other tab/notification source; browser at 100% zoom; agent feed visible without scrolling
- [ ] Dry-run the full cascade once; reset again before going live

## Timeline

### 0:00 — Open on the live control room (Tab 1)

Map with moving trains, platform Gantt, empty-ish agent feed.

> "This is RailMind — a multi-agent control room for Indian Railways, running on a live digital twin of the Delhi–Kanpur–Prayagraj corridor. Eight real trains, four stations, and a team of AI agents watching every one of them. Indian Railways moves 19 million passengers a day, and punctuality has fallen from 94 to under 74 percent — because when one train slips, humans untangle the cascade by hand. Watch what agents do instead."

### 0:30 — Inject the delay

Open the scenario panel → select **Delay** → train **12302 (Howrah Rajdhani)** → **25 minutes** → Inject.

> "I'm delaying the Howrah Rajdhani — train 12302 — by 25 minutes. One click. Now keep your eyes on the agent feed."

### 0:40–1:45 — The cascade (narrate over the feed as each event lands)

**Beat 1 — Delay detection** (`delay.detected` appears; Train Agent thought streams)

> "The Train Agent caught it instantly — and it's projecting the knock-on: Kanpur arrival slips to 9:35, Prayagraj to 10:35, and the final stop to 11:25."

**Beat 2 — Platform conflict** (`platform.conflict` at CNB; Gantt block pulses red)

> "Kanpur's Station Agent just found the collision: 12302 now wants platform 1 at exactly 9:35 — the same slot as the Shiv Ganga Express. See it pulsing red on the Gantt."

**Beat 3 — Negotiation + orchestrator approval** (decision card appears; Orchestrator reasoning streams)

> "Here's the key: the deterministic rules computed which platforms are actually feasible — platform 2 is blocked by a terminating train, platform 4 by a local. The LLM only chooses among safe options and explains why. The Orchestrator — that's Claude — reviews and approves."

**Beat 4 — Platform move 1 → 3** (`platform.reassigned`; Gantt re-flows)

> "Approved. 12302 moves from platform 1 to platform 3, and the Gantt re-flows live. Zero human input so far."

**Beat 5 — Duty breach** (`crew.duty_breach` appears)

> "But the cascade isn't done. The Crew Agent ran the duty math: this crew started at 2 AM with a 9-hour legal limit — the delay pushes them past it. That's a labor-law violation waiting to happen."

**Beat 6 — Crew swap at PRYJ** (`crew.swapped` decision approved)

> "It found a spare crew — CR-201 — based at Prayagraj, the last stop before the breach, and scheduled the handover there. Approved and logged."

**Beat 7 — Passenger alerts** (`passenger.alert` banners; glance at Tab 2 briefly)

> "And the Passenger Info Agent has already pushed the platform change and new times to every affected passenger."

### 1:45 — Human override

Wait for (or trigger) the next agent proposal; on its decision card click **Reject**.

> "Critically, the human is always in command. I'm rejecting this proposal — watch the agents recompute with that constraint. Every decision, every option considered, every override goes into a persistent audit log. This is human-in-the-loop by design, not autonomy theater."

### 2:10 — Passenger voice query (Tab 2)

Switch to the passenger view. Tap the mic and **speak**: *"Where is train 12302?"*

Wait for the spoken TTS reply (it answers from live twin data: current position, new platform 3 at Kanpur, revised ETA).

> "Same twin, passenger side. That's Deepgram speech-to-text, an agent over live network state, and a spoken reply — not a canned chatbot."

### 2:40 — KPI close (Tab 1, KPI panel)

> "And the bottom line: the KPI panel compares this run against a naive no-agent baseline — knock-on delay avoided, trains platformed without waiting, every decision counted and auditable. We built this on Hive, our own open-source agent framework. One corridor today; the architecture — deterministic rules that validate, LLMs that decide, humans that override — is built to scale to the real network. This is RailMind."

— End at ~3:00.

## Failure fallbacks

| Failure | Action |
|---|---|
| LLM providers slow/flaky on stage | Agents auto-fall back Groq → Claude → OpenAI → rule-only. If you must, set `AGENT_LLM=off` in `backend/.env` and restart — the full cascade still runs with templated rationale. Spoken line: *"We're in deterministic fallback mode — the same safety rules drive everything; the LLM layer only adds negotiation and narration."* |
| Voice round-trip fails (mic/Deepgram) | Type the same question in the passenger chat box instead. Line: *"Text path, same agent."* |
| Cascade doesn't fire / sim wedged | `POST /api/sim/reset`, re-inject. If a second attempt fails, cut to the pre-recorded video. |
| Total demo loss | Play the pre-recorded video (keep it open in a paused tab throughout). Never debug live. |

## Video recording notes (2–3 min, this script verbatim)

- Record at 1920×1080, browser full-screen, cursor visible; voiceover with the talking lines above (re-record audio separately if needed).
- Use sim speed control to keep dead air out — bump speed between beats, never during an agent thought stream.
- **Capture list (in one continuous take if possible):**
  1. Control room idle — trains moving (5–10s establishing shot)
  2. Scenario inject click
  3. Agent feed during the full cascade — keep the feed in frame; zoom/crop in post if reasoning text is small
  4. Gantt conflict pulse → re-flow after reassignment
  5. Decision card Reject (override) + recompute
  6. Passenger view voice round-trip **with audible TTS reply**
  7. KPI panel close-up
  8. 2-second end card: RailMind + Hive repo URL (github.com/chiruu12/Hive)
- Keep one full successful screen recording as the disaster-recovery video for the live demo.
