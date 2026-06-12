/**
 * Live twin store (zustand). Hydrates from GET /api/state (or the mock server's
 * `state.snapshot` envelope) and folds every WS EventEnvelope from useEventStream.
 */

import { create } from 'zustand'
import type {
  AgentDecision,
  AgentThought,
  Crew,
  CrewDutyBreach,
  CrewSwapped,
  DecisionProposed,
  DecisionResolved,
  DecisionStatus,
  DelayDetected,
  EventEnvelope,
  KPISnapshot,
  KPIUpdated,
  NetworkState,
  PassengerAlert,
  PlatformAssignment,
  PlatformConflict,
  PlatformReassigned,
  ScenarioInjected,
  SimTick,
  Station,
  Train,
  TrainPosition,
  TrainStatus,
  TrainStatusChanged,
} from './api/types'
import { shiftIso } from './lib/format'

const FEED_CAP = 200
const ALERT_CAP = 30

export type FeedTone = 'info' | 'warning' | 'critical' | 'success'

type FeedInput =
  | { kind: 'thought'; ts: string; agent: string; text: string; decisionId: string | null }
  | { kind: 'decision'; ts: string; decisionId: string }
  | { kind: 'event'; ts: string; topic: string; label: string; tone: FeedTone }

export type FeedItem = FeedInput & { id: number }

const EMPTY_KPIS: KPISnapshot = {
  total_delay_min: 0,
  knock_on_delays_avoided: 0,
  pct_instant_platforming: 100,
  decisions_made: 0,
}

export interface RailState {
  hydrated: boolean
  lastEventAt: number
  simTime: string | null
  simSpeed: number
  running: boolean
  stations: Station[]
  trains: Record<string, Train>
  positions: Record<string, TrainPosition>
  assignments: PlatformAssignment[]
  crews: Crew[]
  kpis: KPISnapshot
  feed: FeedItem[]
  decisions: Record<string, AgentDecision>
  alerts: PassengerAlert[]
  conflicts: PlatformConflict[]
  /** `${station}:${train}` → wall-clock ms of the last reassignment (Gantt highlight). */
  reassignedAt: Record<string, number>

  hydrate: (snapshot: NetworkState) => void
  applyEnvelope: (envelope: EventEnvelope) => void
  resolveDecisionOptimistic: (id: string, status: DecisionStatus) => void
}

let feedSeq = 0

export const useStore = create<RailState>()((set, get) => {
  const addFeed = (input: FeedInput): void => {
    set((s) => ({
      feed: [...s.feed, { ...input, id: ++feedSeq } as FeedItem].slice(-FEED_CAP),
    }))
  }

  /** Update a train's delay (and optionally status); shift its Gantt blocks by the delta. */
  const applyDelay = (number: string, delayMin: number, status?: TrainStatus): void => {
    const s = get()
    const train = s.trains[number]
    if (!train) return
    const delta = delayMin - train.delay_min
    const trains = {
      ...s.trains,
      [number]: { ...train, delay_min: delayMin, ...(status ? { status } : {}) },
    }
    const assignments =
      delta === 0
        ? s.assignments
        : s.assignments.map((a) =>
            a.train_number === number
              ? { ...a, arrival: shiftIso(a.arrival, delta), departure: shiftIso(a.departure, delta) }
              : a,
          )
    set({ trains, assignments })
  }

  return {
    hydrated: false,
    lastEventAt: 0,
    simTime: null,
    simSpeed: 1,
    running: false,
    stations: [],
    trains: {},
    positions: {},
    assignments: [],
    crews: [],
    kpis: EMPTY_KPIS,
    feed: [],
    decisions: {},
    alerts: [],
    conflicts: [],
    reassignedAt: {},

    hydrate: (snapshot) => {
      const trains: Record<string, Train> = {}
      for (const t of snapshot.trains) trains[t.number] = t
      set({
        hydrated: true,
        simTime: snapshot.sim_time,
        simSpeed: snapshot.sim_speed,
        running: snapshot.running,
        stations: snapshot.stations,
        trains,
        assignments: snapshot.assignments,
        crews: snapshot.crews,
        kpis: snapshot.kpis,
      })
    },

    resolveDecisionOptimistic: (id, status) => {
      set((s) => {
        const decision = s.decisions[id]
        if (!decision) return s
        return { decisions: { ...s.decisions, [id]: { ...decision, status } } }
      })
    },

    applyEnvelope: (envelope) => {
      set({ lastEventAt: Date.now() })

      switch (envelope.topic) {
        case 'state.snapshot': {
          // Mock-server hydration path (production hydrates via GET /api/state).
          get().hydrate(envelope.payload as NetworkState)
          return
        }

        case 'sim.tick': {
          const p = envelope.payload as SimTick
          set({ simTime: p.sim_time, simSpeed: p.sim_speed, running: p.running })
          return
        }

        case 'train.position': {
          const p = envelope.payload as TrainPosition
          set((s) => ({ positions: { ...s.positions, [p.train_number]: p } }))
          applyDelay(p.train_number, p.delay_min, p.status)
          return
        }

        case 'train.status': {
          const p = envelope.payload as TrainStatusChanged
          applyDelay(p.train_number, p.delay_min, p.status)
          return
        }

        case 'delay.detected': {
          const p = envelope.payload as DelayDetected
          applyDelay(p.train_number, p.delay_min)
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Delay detected — ${p.train_number} +${p.delay_min} min (${p.cause}); downstream: ${p.downstream_stops.join(', ')}`,
            tone: 'warning',
          })
          return
        }

        case 'platform.conflict': {
          const p = envelope.payload as PlatformConflict
          set((s) => ({ conflicts: [...s.conflicts, p].slice(-20) }))
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Platform conflict @ ${p.station_code} P${p.platform}: ${p.train_numbers.join(' vs ')}`,
            tone: 'critical',
          })
          return
        }

        case 'platform.reassigned': {
          const p = envelope.payload as PlatformReassigned
          set((s) => ({
            assignments: s.assignments.map((a) =>
              a.station_code === p.station_code && a.train_number === p.train_number
                ? { ...a, platform: p.new_platform }
                : a,
            ),
            conflicts: s.conflicts.filter(
              (c) =>
                !(c.station_code === p.station_code && c.train_numbers.includes(p.train_number)),
            ),
            reassignedAt: {
              ...s.reassignedAt,
              [`${p.station_code}:${p.train_number}`]: Date.now(),
            },
          }))
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Reassigned — ${p.train_number} to P${p.new_platform} at ${p.station_code} (was P${p.old_platform})`,
            tone: 'success',
          })
          return
        }

        case 'crew.duty_breach': {
          const p = envelope.payload as CrewDutyBreach
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Duty breach — ${p.crew_id} on ${p.train_number}: ${p.projected_hours}h projected vs ${p.limit_hours}h limit (at ${p.breach_station})`,
            tone: 'critical',
          })
          return
        }

        case 'crew.swapped': {
          const p = envelope.payload as CrewSwapped
          set((s) => {
            const train = s.trains[p.train_number]
            const trains = train
              ? { ...s.trains, [p.train_number]: { ...train, crew_id: p.new_crew_id } }
              : s.trains
            const crews = s.crews.map((c) => {
              if (c.id === p.new_crew_id)
                return { ...c, assigned_train: p.train_number, status: 'on_duty' as const }
              if (c.id === p.old_crew_id)
                return { ...c, assigned_train: null, status: 'off_duty' as const }
              return c
            })
            return { trains, crews }
          })
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Crew swap — ${p.old_crew_id} → ${p.new_crew_id} on ${p.train_number} at ${p.station_code}`,
            tone: 'success',
          })
          return
        }

        case 'passenger.alert': {
          const p = envelope.payload as PassengerAlert
          set((s) => ({ alerts: [...s.alerts, p].slice(-ALERT_CAP) }))
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Passenger alert (${p.severity}) — ${p.message}`,
            tone: p.severity === 'critical' ? 'critical' : p.severity === 'warning' ? 'warning' : 'info',
          })
          return
        }

        case 'agent.thought': {
          const p = envelope.payload as AgentThought
          addFeed({
            kind: 'thought',
            ts: envelope.ts,
            agent: p.agent,
            text: p.text,
            decisionId: p.decision_id,
          })
          return
        }

        case 'decision.proposed': {
          const p = envelope.payload as DecisionProposed
          set((s) => ({ decisions: { ...s.decisions, [p.decision.id]: p.decision } }))
          addFeed({ kind: 'decision', ts: envelope.ts, decisionId: p.decision.id })
          return
        }

        case 'decision.resolved': {
          const p = envelope.payload as DecisionResolved
          set((s) => {
            const decision = s.decisions[p.decision_id]
            if (!decision) return s
            return { decisions: { ...s.decisions, [p.decision_id]: { ...decision, status: p.status } } }
          })
          return
        }

        case 'kpi.updated': {
          const p = envelope.payload as KPIUpdated
          set({
            kpis: {
              total_delay_min: p.total_delay_min,
              knock_on_delays_avoided: p.knock_on_delays_avoided,
              pct_instant_platforming: p.pct_instant_platforming,
              decisions_made: p.decisions_made,
            },
          })
          return
        }

        case 'scenario.injected': {
          const p = envelope.payload as ScenarioInjected
          addFeed({
            kind: 'event',
            ts: envelope.ts,
            topic: envelope.topic,
            label: `Scenario injected — ${p.scenario_type}: ${JSON.stringify(p.params)}`,
            tone: 'info',
          })
          return
        }

        default:
          return
      }
    },
  }
})
