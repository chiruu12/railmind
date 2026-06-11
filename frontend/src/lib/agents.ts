/** Agent identity → display name + color classes for chips in the feed. */

export interface AgentTheme {
  /** Chip classes (text + bg + border). Literal strings so Tailwind v4 generates them. */
  chip: string
  /** Bare dot/accent color class. */
  dot: string
}

const THEMES: Array<[prefix: string, theme: AgentTheme]> = [
  ['train-agent', { chip: 'text-sky-300 bg-sky-500/10 border-sky-500/40', dot: 'bg-sky-400' }],
  [
    'station-agent',
    { chip: 'text-amber-300 bg-amber-500/10 border-amber-500/40', dot: 'bg-amber-400' },
  ],
  [
    'crew-agent',
    { chip: 'text-violet-300 bg-violet-500/10 border-violet-500/40', dot: 'bg-violet-400' },
  ],
  [
    'orchestrator',
    { chip: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/40', dot: 'bg-emerald-400' },
  ],
  [
    'passenger-agent',
    { chip: 'text-rose-300 bg-rose-500/10 border-rose-500/40', dot: 'bg-rose-400' },
  ],
]

const DEFAULT_THEME: AgentTheme = {
  chip: 'text-zinc-300 bg-zinc-500/10 border-zinc-500/40',
  dot: 'bg-zinc-400',
}

export function agentTheme(agent: string): AgentTheme {
  const found = THEMES.find(([prefix]) => agent.startsWith(prefix))
  return found ? found[1] : DEFAULT_THEME
}
