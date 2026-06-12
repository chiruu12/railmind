/**
 * Passenger feature API (WS5) — relative /api fetches, proxied to :8000 in dev.
 */

import type { NetworkState } from '../../api/types'
import { API_BASE } from '../../lib/apiBase'

export interface ChatResponse {
  reply: string
}

export interface VoiceResponse {
  transcript: string
  reply_text: string
  reply_audio_b64: string | null
  reply_audio_mime: string | null
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.url} → ${res.status}`)
  return (await res.json()) as T
}

export function fetchState(): Promise<NetworkState> {
  return fetch(`${API_BASE}/api/state`, { signal: AbortSignal.timeout(8000) }).then((res) =>
    asJson<NetworkState>(res),
  )
}

export function postChat(
  message: string,
  sessionId: string,
  trainNumber?: string | null,
): Promise<ChatResponse> {
  return fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId, train_number: trainNumber ?? undefined }),
    signal: AbortSignal.timeout(25000),
  }).then((res) => asJson<ChatResponse>(res))
}

export function postVoice(
  audio: Blob,
  sessionId: string,
  trainNumber?: string | null,
): Promise<VoiceResponse> {
  const form = new FormData()
  form.append('audio', audio, 'clip.webm')
  form.append('session_id', sessionId)
  if (trainNumber) form.append('train_number', trainNumber)
  return fetch(`${API_BASE}/api/voice`, {
    method: 'POST',
    body: form,
    signal: AbortSignal.timeout(45000),
  }).then((res) => asJson<VoiceResponse>(res))
}
