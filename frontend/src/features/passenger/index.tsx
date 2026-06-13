/**
 * Passenger view (WS5) — "Rail Saarthi".
 *
 * Mobile-frame page: centered ~390px phone bezel against the dimmed dark app,
 * light UI inside. My-train selector + live status card (folds train.status /
 * platform.reassigned WS events), severity-colored passenger.alert banner
 * stack, chat thread with text input and a mic round-trip via /api/voice.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import type {
  AlertSeverity,
  NetworkState,
  Train,
  TrainStatus,
} from '../../api/types'
import { isTopic, useEventStream } from '../../api/ws'
import { fetchState, postChat, postVoice } from './api'
import { useVoiceRecorder } from './useVoiceRecorder'

// ── view models ──────────────────────────────────────────────────────────────

interface LiveStatus {
  status: TrainStatus
  delayMin: number
  nextStation: string | null
  eta: string | null // ISO sim-time
}

interface AlertItem {
  id: number
  severity: AlertSeverity
  message: string
}

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
}

let nextId = 1
const newId = () => nextId++

const GREETING =
  'Namaste! I am Rail Saarthi. Ask me about any train or station on this corridor — by text or voice.'

const QUICK_REPLIES = [
  'Where is my train?',
  'Any delays?',
  'Platform info',
  'Trains at CNB',
]

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const m = /T(\d{2}:\d{2})/.exec(iso)
  return m ? m[1] : iso
}

function initialLive(train: Train, simTime: string): LiveStatus {
  for (const stop of train.route) {
    if (!stop.sched_arrival) continue
    const eta = new Date(
      new Date(stop.sched_arrival).getTime() + train.delay_min * 60_000,
    )
    if (eta >= new Date(simTime)) {
      const pad = (n: number) => String(n).padStart(2, '0')
      const etaIso = `${eta.getFullYear()}-${pad(eta.getMonth() + 1)}-${pad(eta.getDate())}T${pad(eta.getHours())}:${pad(eta.getMinutes())}:00`
      return {
        status: train.status,
        delayMin: train.delay_min,
        nextStation: stop.station_code,
        eta: etaIso,
      }
    }
  }
  return { status: train.status, delayMin: train.delay_min, nextStation: null, eta: null }
}

const STATUS_STYLE: Record<TrainStatus, string> = {
  scheduled: 'bg-zinc-200 text-zinc-700',
  running: 'bg-emerald-100 text-emerald-700',
  delayed: 'bg-red-100 text-red-700',
  at_platform: 'bg-sky-100 text-sky-700',
  terminated: 'bg-zinc-200 text-zinc-500',
}

const STATUS_LABEL: Record<TrainStatus, string> = {
  scheduled: 'Scheduled',
  running: 'On time',
  delayed: 'Delayed',
  at_platform: 'At platform',
  terminated: 'Arrived',
}

const SEVERITY_STYLE: Record<AlertSeverity, string> = {
  info: 'border-sky-300 bg-sky-50 text-sky-800',
  warning: 'border-amber-300 bg-amber-50 text-amber-800',
  critical: 'border-red-300 bg-red-50 text-red-800',
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function PassengerPage() {
  const [state, setState] = useState<NetworkState | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [platformOverrides, setPlatformOverrides] = useState<Record<string, number>>({})
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [muted, setMuted] = useState(false)

  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: newId(), role: 'assistant', text: GREETING },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const [instanceId] = useState(() => crypto.randomUUID().slice(0, 8))
  const sessionId = selected ? `yatri-${instanceId}-${selected}` : `yatri-${instanceId}`
  const selectedRef = useRef(selected)
  useLayoutEffect(() => { selectedRef.current = selected })
  const mutedRef = useRef(muted)
  useLayoutEffect(() => { mutedRef.current = muted })
  const threadRef = useRef<HTMLDivElement | null>(null)

  // Initial twin snapshot → train list + default selection (a delayed train if any).
  const load = useCallback(() => {
    setLoadError(false)
    fetchState()
      .then((s) => {
        setState(s)
        setSelected((cur) => {
          if (cur && s.trains.some((t) => t.number === cur)) return cur
          const delayed = s.trains.find((t) => t.delay_min >= 5)
          return (delayed ?? s.trains[0])?.number ?? null
        })
      })
      .catch(() => setLoadError(true))
  }, [])
  // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount sets initial state
  useEffect(() => { load() }, [load])

  const derivedLive = useMemo(() => {
    if (!state || !selected) return null
    const train = state.trains.find((t) => t.number === selected)
    return train ? initialLive(train, state.sim_time) : null
  }, [state, selected])

  const [wsLive, setWsLive] = useState<LiveStatus | null>(null)
  const live = wsLive ?? derivedLive

  const prevSelected = useRef(selected)
  useEffect(() => {
    if (prevSelected.current !== selected) {
      prevSelected.current = selected
      setWsLive(null)
      setPlatformOverrides({})
      setAlerts([])
      setMessages([{ id: newId(), role: 'assistant', text: GREETING }])
      setInput('')
    }
  }, [selected])

  // Fold live WS events for the selected train.
  const { connected } = useEventStream((env) => {
    const train = selectedRef.current
    if (!train) return
    if (isTopic(env, 'train.status') && env.payload.train_number === train) {
      const p = env.payload
      setWsLive({
        status: p.status,
        delayMin: p.delay_min,
        nextStation: p.next_station,
        eta: p.eta_next,
      })
    } else if (isTopic(env, 'platform.reassigned') && env.payload.train_number === train) {
      const p = env.payload
      setPlatformOverrides((prev) => ({ ...prev, [p.station_code]: p.new_platform }))
      setAlerts((prev) =>
        [
          {
            id: newId(),
            severity: 'info' as AlertSeverity,
            message: `Platform change at ${p.station_code}: ${p.old_platform} → ${p.new_platform}.`,
          },
          ...prev,
        ].slice(0, 5),
      )
    } else if (isTopic(env, 'passenger.alert') && env.payload.train_number === train) {
      const p = env.payload
      setAlerts((prev) =>
        [{ id: newId(), severity: p.severity, message: p.message }, ...prev].slice(0, 5),
      )
    }
  })

  const isNearBottom = () => {
    const el = threadRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const appendMessage = (role: ChatMessage['role'], text: string) =>
    setMessages((prev) => [...prev, { id: newId(), role, text }])

  const shouldScroll = useRef(true)
  useEffect(() => {
    if (shouldScroll.current) {
      threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight })
    }
  }, [messages, busy])

  const sendMessage = async (text: string) => {
    const message = text.trim()
    if (!message || busy) return
    shouldScroll.current = true
    setInput('')
    appendMessage('user', message)
    setBusy(true)
    try {
      const { reply } = await postChat(message, sessionId, selected)
      appendMessage('assistant', reply)
    } catch {
      appendMessage('assistant', 'Sorry, I could not reach the assistant. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const sendText = () => sendMessage(input)

  const onAudio = useCallback(async (blob: Blob) => {
    shouldScroll.current = true
    appendMessage('user', '🎤 Voice message')
    setBusy(true)
    try {
      const res = await postVoice(blob, sessionId, selectedRef.current)
      if (res.transcript) {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated.findLastIndex((m) => m.role === 'user')
          if (last >= 0) updated[last] = { ...updated[last], text: `🎤 ${res.transcript}` }
          return updated
        })
      }
      appendMessage('assistant', res.reply_text)
      if (res.reply_audio_b64 && res.reply_audio_mime && !mutedRef.current) {
        const audio = new Audio(`data:${res.reply_audio_mime};base64,${res.reply_audio_b64}`)
        void audio.play().catch((e) => console.warn('audio playback failed', e))
      }
    } catch (err) {
      console.warn('voice request failed', err)
      appendMessage('assistant', 'Voice processing took too long. Please try again, or type your question instead.')
    } finally {
      setBusy(false)
    }
  }, [sessionId])

  const recorder = useVoiceRecorder(onAudio)

  const selectedTrain = state?.trains.find((t) => t.number === selected) ?? null
  const platform = (() => {
    if (!live?.nextStation || !selectedTrain || !state) return null
    const override = platformOverrides[live.nextStation]
    if (override !== undefined) return override
    const assignment = state.assignments.find(
      (a) => a.train_number === selectedTrain.number && a.station_code === live.nextStation,
    )
    if (assignment) return assignment.platform
    return (
      selectedTrain.route.find((s) => s.station_code === live.nextStation)?.platform ?? null
    )
  })()

  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-hidden bg-zinc-950">
      {/* dimmed control-room backdrop */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(63,63,70,0.35),transparent_70%)]" />

      {/* phone bezel */}
      <div className="relative z-10 flex h-[min(800px,94vh)] w-[390px] flex-col overflow-hidden rounded-[2.8rem] border-[10px] border-zinc-800 bg-zinc-50 text-zinc-900 shadow-[0_0_80px_rgba(0,0,0,0.8)]">
        {/* notch */}
        <div className="absolute left-1/2 top-1.5 h-5 w-28 -translate-x-1/2 rounded-full bg-zinc-800" />

        {/* header */}
        <header className="flex items-center justify-between bg-indigo-700 px-5 pb-3 pt-9 text-white">
          <div className="flex items-center gap-2.5">
            <img
              src="/brand/rail-saarthi-mark.png"
              alt="Rail Saarthi"
              className="h-9 w-9 rounded-full ring-1 ring-white/20"
            />
            <div>
              <h1 className="text-lg font-bold leading-tight">Rail Saarthi</h1>
              <p className="text-[11px] text-indigo-200">Live passenger assistant</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setMuted((m) => !m)}
            title={muted ? 'Unmute voice replies' : 'Mute voice replies'}
            aria-label={muted ? 'Unmute voice replies' : 'Mute voice replies'}
            className="rounded-full bg-indigo-600 px-3 py-1.5 text-sm hover:bg-indigo-500"
          >
            {muted ? '🔇' : '🔊'}
          </button>
        </header>

        {!connected && (
          <div className="bg-amber-500 px-4 py-1.5 text-center text-[11px] font-medium text-white">
            Live updates paused — reconnecting…
          </div>
        )}

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {/* top info area — scrolls independently, capped at 45% so chat always has room */}
          <div className="max-h-[45%] shrink-0 overflow-y-auto px-4 pt-3">
            <div className="flex flex-col gap-3">
              {!state && !loadError && (
                <div className="flex items-center justify-center gap-2 py-6 text-sm text-zinc-400">
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-zinc-300 border-t-indigo-600" />
                  Loading network state…
                </div>
              )}

              {/* my-train selector */}
              <label className="block text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                My train
                <select
                  value={selected ?? ''}
                  onChange={(e) => setSelected(e.target.value || null)}
                  className="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm font-normal normal-case tracking-normal text-zinc-900"
                >
                  {!state && <option value="">Loading trains…</option>}
                  {state?.trains.map((t) => (
                    <option key={t.number} value={t.number}>
                      {t.number} — {t.name}
                    </option>
                  ))}
                </select>
              </label>

              {loadError && (
                <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Could not load the live network state.{' '}
                  <button type="button" onClick={load} className="font-semibold underline">
                    Retry
                  </button>
                </div>
              )}

              {/* live status card */}
              {selectedTrain && live && (
                <section className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-bold">
                        {selectedTrain.number} {selectedTrain.name}
                      </p>
                      <p className="text-[11px] text-zinc-500">
                        {selectedTrain.route[0]?.station_code} →{' '}
                        {selectedTrain.route[selectedTrain.route.length - 1]?.station_code}
                      </p>
                    </div>
                    <span
                      className={clsx(
                        'rounded-full px-2.5 py-1 text-[11px] font-semibold',
                        STATUS_STYLE[live.status],
                      )}
                    >
                      {STATUS_LABEL[live.status]}
                    </span>
                  </div>
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                    <div className="rounded-xl bg-zinc-100 p-2">
                      <dt className="text-[10px] uppercase tracking-wide text-zinc-500">Delay</dt>
                      <dd
                        className={clsx(
                          'text-sm font-bold',
                          live.delayMin >= 5 ? 'text-red-600' : 'text-emerald-600',
                        )}
                      >
                        {live.delayMin > 0 ? `+${live.delayMin} min` : 'On time'}
                      </dd>
                    </div>
                    <div className="rounded-xl bg-zinc-100 p-2">
                      <dt className="text-[10px] uppercase tracking-wide text-zinc-500">Next stop</dt>
                      <dd className="text-sm font-bold">
                        {live.nextStation ?? '—'}
                        <span className="block text-[10px] font-normal text-zinc-500">
                          ETA {fmtTime(live.eta)}
                        </span>
                      </dd>
                    </div>
                    <div className="rounded-xl bg-zinc-100 p-2">
                      <dt className="text-[10px] uppercase tracking-wide text-zinc-500">Platform</dt>
                      <dd className="text-sm font-bold">{platform ?? '—'}</dd>
                    </div>
                  </dl>
                </section>
              )}

              {/* alert banner stack */}
              {alerts.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  {alerts.map((a) => (
                    <div
                      key={a.id}
                      className={clsx(
                        'rounded-lg border px-3 py-2 text-xs',
                        SEVERITY_STYLE[a.severity],
                      )}
                    >
                      {a.message}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* chat thread — always gets remaining space */}
          <section className="mx-4 mb-3 mt-2 flex min-h-0 flex-1 flex-col rounded-2xl border border-zinc-200 bg-white shadow-sm">
            <div ref={threadRef} onScroll={() => { shouldScroll.current = isNearBottom() }} className="flex-1 space-y-2 overflow-y-auto p-3">
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={clsx('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}
                >
                  <p
                    className={clsx(
                      'max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-[13px] leading-snug',
                      m.role === 'user'
                        ? 'rounded-br-sm bg-indigo-600 text-white'
                        : 'rounded-bl-sm bg-zinc-100 text-zinc-800',
                    )}
                  >
                    {m.text}
                  </p>
                </div>
              ))}
              {busy && <p className="px-1 text-xs text-zinc-400">Yatri is thinking…</p>}
            </div>

            {messages.length <= 1 && !busy && (
              <div className="flex flex-wrap gap-1.5 px-3 pb-1">
                {QUICK_REPLIES.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => void sendMessage(q)}
                    className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}

            {recorder.micError && (
              <p className="px-3 pb-1 text-[11px] text-amber-700">{recorder.micError}</p>
            )}

            {/* composer */}
            <div className="flex items-center gap-2 border-t border-zinc-200 p-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void sendText()
                }}
                placeholder="Ask about any train or station…"
                className="min-w-0 flex-1 rounded-full border border-zinc-300 bg-zinc-50 px-4 py-2 text-sm outline-none focus:border-indigo-400"
              />
              <button
                type="button"
                onClick={recorder.toggle}
                title={recorder.recording ? 'Stop recording' : 'Ask by voice'}
                aria-label={recorder.recording ? 'Stop recording' : 'Ask by voice'}
                className={clsx(
                  'flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-lg',
                  recorder.recording
                    ? 'animate-pulse bg-red-600 text-white'
                    : 'bg-zinc-200 text-zinc-700 hover:bg-zinc-300',
                )}
              >
                {recorder.recording ? '■' : '🎙️'}
              </button>
              <button
                type="button"
                onClick={() => void sendText()}
                disabled={busy || !input.trim()}
                aria-label="Send message"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white disabled:opacity-40"
              >
                ➤
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
