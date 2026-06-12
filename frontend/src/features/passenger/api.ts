/**
 * Passenger feature API (WS5) — relative /api fetches, proxied to :8000 in dev.
 */

import type { NetworkState } from '../../api/types'

export interface ChatResponse {
  reply: string
}

export interface VoiceResponse {
  reply_text: string
  reply_audio_b64: string | null
  reply_audio_mime: string | null
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.url} → ${res.status}`)
  return (await res.json()) as T
}

export function fetchState(): Promise<NetworkState> {
  return fetch('/api/state').then((res) => asJson<NetworkState>(res))
}

export function postChat(message: string, sessionId: string): Promise<ChatResponse> {
  return fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  }).then((res) => asJson<ChatResponse>(res))
}

export function postVoice(audio: Blob, sessionId: string): Promise<VoiceResponse> {
  const form = new FormData()
  form.append('audio', audio, 'clip.webm')
  form.append('session_id', sessionId)
  return fetch('/api/voice', { method: 'POST', body: form }).then((res) =>
    asJson<VoiceResponse>(res),
  )
}
