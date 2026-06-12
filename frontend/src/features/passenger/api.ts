/**
 * Passenger feature API (WS5) — relative /api fetches, proxied to :8000 in dev.
 */

import type { NetworkState } from '../../api/types'

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

function withTimeout(ms: number, signal?: AbortSignal): AbortSignal {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), ms)
  signal?.addEventListener('abort', () => {
    clearTimeout(timer)
    ctrl.abort()
  })
  ctrl.signal.addEventListener('abort', () => clearTimeout(timer))
  return ctrl.signal
}

export function fetchState(): Promise<NetworkState> {
  return fetch('/api/state', { signal: withTimeout(8000) }).then((res) =>
    asJson<NetworkState>(res),
  )
}

export function postChat(
  message: string,
  sessionId: string,
  trainNumber?: string | null,
): Promise<ChatResponse> {
  return fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId, train_number: trainNumber ?? undefined }),
    signal: withTimeout(15000),
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
  return fetch('/api/voice', { method: 'POST', body: form, signal: withTimeout(20000) }).then(
    (res) => asJson<VoiceResponse>(res),
  )
}
