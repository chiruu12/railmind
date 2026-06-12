/**
 * TypeScript mirror of backend/app/contracts/{entities,events}.py.
 * FROZEN after Phase 0 — changes only via the main session, kept in sync
 * with the Python contracts and docs/CONTRACTS.md.
 */

// ── Entities ────────────────────────────────────────────────────────────────

export type TrainStatus = 'scheduled' | 'running' | 'delayed' | 'at_platform' | 'terminated'
export type TrainPriority = 1 | 2 | 3 // 1=premium 2=express 3=local
export type CrewStatus = 'on_duty' | 'spare' | 'off_duty'
export type DecisionStatus = 'proposed' | 'approved' | 'rejected' | 'auto'
export type AlertSeverity = 'info' | 'warning' | 'critical'
export type ScenarioType = 'delay' | 'platform_block' | 'crew_sick'

export interface Station {
  code: string
  name: string
  lat: number
  lon: number
  platform_count: number
  km_offset: number
}

export interface StationStop {
  station_code: string
  sched_arrival: string | null // ISO datetime; null at origin
  sched_departure: string | null // null at terminus
  platform: number
}

export interface Train {
  number: string
  name: string
  priority: TrainPriority
  route: StationStop[]
  status: TrainStatus
  delay_min: number
  km_offset: number
  speed_kmph: number
  crew_id: string | null
}

export interface Crew {
  id: string
  name: string
  home_station: string
  assigned_train: string | null
  duty_start: string | null
  max_duty_hours: number
  status: CrewStatus
}

export interface PlatformAssignment {
  station_code: string
  platform: number
  train_number: string
  arrival: string
  departure: string
}

export interface AgentDecision {
  id: string
  ts: string
  agent: string
  trigger: string
  options_considered: string[]
  chosen: string
  rationale: string
  status: DecisionStatus
}

export interface KPISnapshot {
  total_delay_min: number
  knock_on_delays_avoided: number
  pct_instant_platforming: number
  decisions_made: number
}

export interface NetworkState {
  sim_time: string
  sim_speed: number
  running: boolean
  trains: Train[]
  stations: Station[]
  assignments: PlatformAssignment[]
  crews: Crew[]
  kpis: KPISnapshot
}

// ── Events (WS /ws wire format) ─────────────────────────────────────────────

export interface EventEnvelope<T = unknown> {
  topic: string
  ts: string
  payload: T
}

export interface SimTick {
  sim_time: string
  sim_speed: number
  running: boolean
}

export interface TrainPosition {
  train_number: string
  lat: number
  lon: number
  km_offset: number
  speed_kmph: number
  status: TrainStatus
  delay_min: number
}

export interface TrainStatusChanged {
  train_number: string
  status: TrainStatus
  delay_min: number
  next_station: string | null
  eta_next: string | null
}

export interface DelayDetected {
  train_number: string
  delay_min: number
  cause: string
  downstream_stops: string[]
}

export interface PlatformConflict {
  station_code: string
  platform: number
  train_numbers: string[]
  window_start: string
  window_end: string
}

export interface PlatformReassigned {
  station_code: string
  train_number: string
  old_platform: number
  new_platform: number
  rationale: string
  decision_id: string
}

export interface CrewDutyBreach {
  crew_id: string
  train_number: string
  projected_hours: number
  limit_hours: number
  breach_station: string
}

export interface CrewSwapped {
  old_crew_id: string
  new_crew_id: string
  train_number: string
  station_code: string
  rationale: string
  decision_id: string
}

export interface PassengerAlert {
  severity: AlertSeverity
  train_number: string
  message: string
  channels: string[]
}

export interface AgentThought {
  agent: string
  text: string
  decision_id: string | null
}

export interface DecisionProposed {
  decision: AgentDecision
}

export interface DecisionResolved {
  decision_id: string
  status: DecisionStatus
  resolved_by: string
  note: string | null
}

export interface ScenarioInjected {
  scenario_type: ScenarioType
  params: Record<string, unknown>
}

export type KPIUpdated = KPISnapshot

/** Map of topic → payload type, for typed event handling. */
export interface TopicPayloads {
  'sim.tick': SimTick
  'train.position': TrainPosition
  'train.status': TrainStatusChanged
  'delay.detected': DelayDetected
  'platform.conflict': PlatformConflict
  'platform.reassigned': PlatformReassigned
  'crew.duty_breach': CrewDutyBreach
  'crew.swapped': CrewSwapped
  'passenger.alert': PassengerAlert
  'agent.thought': AgentThought
  'decision.proposed': DecisionProposed
  'decision.resolved': DecisionResolved
  'scenario.injected': ScenarioInjected
  'kpi.updated': KPIUpdated
}

export type Topic = keyof TopicPayloads
