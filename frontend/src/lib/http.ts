/**
 * Thin REST helpers over the backend API (src/api/ is frozen, so this lives here).
 * All paths are relative — the Vite dev server proxies /api to :8000.
 */

import type { NetworkState, ScenarioType } from '../api/types'
import { API_BASE } from './apiBase'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    throw new Error(`${init?.method ?? 'GET'} ${path} failed (${res.status})`)
  }
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

function post<T = unknown>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export function fetchState(): Promise<NetworkState> {
  return request<NetworkState>('/api/state')
}

export function postScenario(
  scenario_type: ScenarioType,
  params: Record<string, unknown>,
): Promise<unknown> {
  return post('/api/scenarios', { scenario_type, params })
}

export function resolveDecision(
  id: string,
  status: 'approved' | 'rejected',
  note?: string,
): Promise<unknown> {
  return post(`/api/decisions/${id}/resolve`, { status, note: note ?? null })
}

export function simControl(action: 'start' | 'pause' | 'reset'): Promise<unknown> {
  return post(`/api/sim/${action}`)
}

export function setSimSpeed(speed: number): Promise<unknown> {
  return post('/api/sim/speed', { speed })
}
