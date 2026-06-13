import { useEffect, useRef } from 'react'
import clsx from 'clsx'
import type { FeedItem, FeedTone } from '../../store'
import { useStore } from '../../store'
import { agentTheme } from '../../lib/agents'
import { plainText } from '../../lib/format'
import { resolveDecision } from '../../lib/http'

function wallTime(ts: string): string {
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en-IN', { hour12: false })
}

function AgentChip({ agent }: { agent: string }) {
  return (
    <span
      className={clsx(
        'inline-flex shrink-0 items-center rounded-full border px-2 py-px text-[10px] font-semibold',
        agentTheme(agent).chip,
      )}
    >
      {agent}
    </span>
  )
}

const TONE_DOT: Record<FeedTone, string> = {
  info: 'bg-sky-400',
  warning: 'bg-amber-400',
  critical: 'bg-red-500',
  success: 'bg-emerald-400',
}

const STATUS_BADGE: Record<string, string> = {
  proposed: 'border-amber-500/50 bg-amber-500/10 text-amber-300',
  approved: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300',
  rejected: 'border-red-500/50 bg-red-500/10 text-red-300',
  auto: 'border-sky-500/50 bg-sky-500/10 text-sky-300',
}

function DecisionCard({ decisionId }: { decisionId: string }) {
  const decision = useStore((s) => s.decisions[decisionId])
  const resolveOptimistic = useStore((s) => s.resolveDecisionOptimistic)
  if (!decision) return null

  const onResolve = (status: 'approved' | 'rejected') => {
    resolveOptimistic(decision.id, status) // optimistic; mock server ignores the POST
    resolveDecision(decision.id, status, 'operator override').catch(() => {})
  }

  return (
    <div className="feed-in space-y-2 rounded-md border border-zinc-700/80 bg-zinc-900/90 p-2.5 shadow-lg shadow-black/30">
      <div className="flex items-center gap-2">
        <AgentChip agent={decision.agent} />
        <span className="text-[10px] tabular-nums text-zinc-600">{decision.id}</span>
        <span
          className={clsx(
            'ml-auto rounded-full border px-2 py-px text-[10px] font-bold uppercase tracking-wide',
            STATUS_BADGE[decision.status] ?? STATUS_BADGE.proposed,
          )}
        >
          {decision.status}
        </span>
      </div>
      <p className="text-[11px] leading-snug text-zinc-500">
        <span className="font-semibold text-zinc-400">Trigger:</span> {plainText(decision.trigger)}
      </p>
      <ul className="space-y-1">
        {decision.options_considered.map((option) => {
          const chosen = option === decision.chosen
          return (
            <li
              key={option}
              className={clsx(
                'flex gap-1.5 rounded-sm px-1.5 py-0.5 text-[11px] leading-snug',
                chosen
                  ? 'bg-emerald-500/10 font-medium text-emerald-300'
                  : 'text-zinc-500',
              )}
            >
              <span className="shrink-0">{chosen ? '✓' : '·'}</span>
              <span>{plainText(option)}</span>
            </li>
          )
        })}
      </ul>
      <p className="border-l-2 border-zinc-700 pl-2 text-[11px] italic leading-snug text-zinc-400">
        {plainText(decision.rationale)}
      </p>
      {decision.status === 'proposed' && (
        <div className="flex gap-2 pt-0.5">
          <button
            type="button"
            onClick={() => onResolve('approved')}
            className="flex-1 rounded border border-emerald-600/70 bg-emerald-600/15 py-1 text-[11px] font-semibold text-emerald-300 transition-colors hover:bg-emerald-600/30"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => onResolve('rejected')}
            className="flex-1 rounded border border-red-600/70 bg-red-600/15 py-1 text-[11px] font-semibold text-red-300 transition-colors hover:bg-red-600/30"
          >
            Reject
          </button>
        </div>
      )}
    </div>
  )
}

function FeedRow({ item }: { item: FeedItem }) {
  switch (item.kind) {
    case 'thought':
      return (
        <div className="feed-in space-y-1">
          <div className="flex items-center gap-2">
            <AgentChip agent={item.agent} />
            <span className="ml-auto text-[9px] tabular-nums text-zinc-700">
              {wallTime(item.ts)}
            </span>
          </div>
          <p className="pl-1 text-[11px] leading-snug text-zinc-300">{plainText(item.text)}</p>
        </div>
      )
    case 'decision':
      return <DecisionCard decisionId={item.decisionId} />
    case 'event':
      return (
        <div className="feed-in flex items-start gap-2 rounded-sm bg-zinc-900/50 px-1.5 py-1">
          <span className={clsx('mt-1 h-1.5 w-1.5 shrink-0 rounded-full', TONE_DOT[item.tone])} />
          <p className="text-[10px] leading-snug text-zinc-400">{plainText(item.label)}</p>
        </div>
      )
  }
}

export default function AgentFeed() {
  const feed = useStore((s) => s.feed)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [feed.length])

  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-zinc-800 bg-zinc-950">
      <div className="flex items-center gap-2 border-b border-zinc-800 px-3 py-2">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-zinc-400">
          Agent activity
        </h2>
        <span className="ml-auto text-[10px] tabular-nums text-zinc-600">{feed.length} events</span>
      </div>
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
        }}
        className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-3"
      >
        {feed.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <span className="h-2 w-2 animate-ping rounded-full bg-zinc-600" />
            <p className="text-xs text-zinc-500">Awaiting agent activity…</p>
            <p className="max-w-[220px] text-[11px] text-zinc-600">
              Inject a scenario from the left panel to wake the agent cascade.
            </p>
          </div>
        ) : (
          feed.map((item) => <FeedRow key={item.id} item={item} />)
        )}
      </div>
    </aside>
  )
}
