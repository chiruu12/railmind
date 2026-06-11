import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { useStore } from '../../store'
import { timeHMS } from '../../lib/format'

/** Animated count-up toward a changing numeric value. */
function useCountUp(value: number, durationMs = 700): number {
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value)
  useEffect(() => {
    const from = fromRef.current
    if (from === value) return
    fromRef.current = value
    const start = performance.now()
    let raf = 0
    const step = (t: number) => {
      const f = Math.min((t - start) / durationMs, 1)
      const eased = 1 - Math.pow(1 - f, 3)
      setDisplay(from + (value - from) * eased)
      if (f < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value, durationMs])
  return display
}

function Kpi({
  label,
  value,
  decimals = 0,
  suffix = '',
  accent = 'text-zinc-100',
}: {
  label: string
  value: number
  decimals?: number
  suffix?: string
  accent?: string
}) {
  const display = useCountUp(value)
  return (
    <div className="flex flex-col items-center px-3">
      <span className="text-[9px] font-semibold uppercase tracking-widest text-zinc-500">
        {label}
      </span>
      <span className={clsx('text-base font-bold leading-tight tabular-nums', accent)}>
        {display.toFixed(decimals)}
        {suffix}
      </span>
    </div>
  )
}

function LiveDot() {
  const lastEventAt = useStore((s) => s.lastEventAt)
  const [now, setNow] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const live = lastEventAt > 0 && now - lastEventAt < 5000
  return (
    <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest">
      <span
        className={clsx(
          'h-1.5 w-1.5 rounded-full',
          live ? 'animate-pulse bg-emerald-400' : 'bg-zinc-600',
        )}
      />
      <span className={live ? 'text-emerald-400' : 'text-zinc-600'}>
        {live ? 'live' : 'offline'}
      </span>
    </span>
  )
}

export default function KpiStrip() {
  const simTime = useStore((s) => s.simTime)
  const simSpeed = useStore((s) => s.simSpeed)
  const running = useStore((s) => s.running)
  const kpis = useStore((s) => s.kpis)

  return (
    <div className="flex flex-1 items-center justify-center gap-1 divide-x divide-zinc-800/80">
      <div className="flex flex-col items-center px-3">
        <span className="text-[9px] font-semibold uppercase tracking-widest text-zinc-500">
          Sim clock
        </span>
        <span className="text-base font-bold leading-tight tabular-nums text-amber-300">
          {timeHMS(simTime)}
          <span className="ml-1.5 text-[10px] font-medium text-zinc-500">
            ×{simSpeed}
            {running ? '' : ' ⏸'}
          </span>
        </span>
      </div>
      <Kpi
        label="Total delay"
        value={kpis.total_delay_min}
        suffix=" min"
        accent={kpis.total_delay_min > 0 ? 'text-amber-300' : 'text-zinc-100'}
      />
      <Kpi
        label="Knock-on avoided"
        value={kpis.knock_on_delays_avoided}
        accent="text-emerald-300"
      />
      <Kpi
        label="Instant platforming"
        value={kpis.pct_instant_platforming}
        decimals={1}
        suffix="%"
      />
      <Kpi label="Decisions" value={kpis.decisions_made} accent="text-sky-300" />
      <div className="pl-3">
        <LiveDot />
      </div>
    </div>
  )
}
