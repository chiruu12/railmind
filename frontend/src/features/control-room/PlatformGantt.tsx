import { useMemo, useState } from 'react'
import clsx from 'clsx'
import type { PlatformAssignment } from '../../api/types'
import { useStore } from '../../store'
import { minOfDay, timeHM } from '../../lib/format'

const DAY_START = 7 * 60 + 30 // 07:30
const DAY_END = 12 * 60 + 30 // 12:30
const SPAN = DAY_END - DAY_START
const HEADWAY_MIN = 5
const REASSIGN_HIGHLIGHT_MS = 8000

function pct(minute: number): number {
  return ((minute - DAY_START) / SPAN) * 100
}

interface Block {
  assignment: PlatformAssignment
  start: number // minutes of day (arrival)
  end: number // minutes of day (departure)
  conflicted: boolean
  reassigned: boolean
}

export default function PlatformGantt() {
  const stations = useStore((s) => s.stations)
  const assignments = useStore((s) => s.assignments)
  const trains = useStore((s) => s.trains)
  const conflicts = useStore((s) => s.conflicts)
  const reassignedAt = useStore((s) => s.reassignedAt)
  const simTime = useStore((s) => s.simTime)

  const [selected, setSelected] = useState('CNB')
  const station = stations.find((s) => s.code === selected) ?? stations[0]

  // lastEventAt is wall-clock ms refreshed by every envelope (≥1/s while live), so the
  // reassignment highlight expires without an impure Date.now() call during render.
  const lastEventAt = useStore((s) => s.lastEventAt)

  const blocks = useMemo<Block[]>(() => {
    if (!station) return []
    const now = lastEventAt
    const here = assignments
      .filter((a) => a.station_code === station.code)
      .map((a) => ({
        assignment: a,
        start: minOfDay(a.arrival),
        end: minOfDay(a.departure),
        conflicted: false,
        reassigned:
          now - (reassignedAt[`${a.station_code}:${a.train_number}`] ?? 0) <
          REASSIGN_HIGHLIGHT_MS,
      }))
      .filter((b) => b.end + HEADWAY_MIN > DAY_START && b.start < DAY_END)

    // Geometric conflict detection: same row, occupancy windows (incl. headway) overlap.
    for (let i = 0; i < here.length; i++) {
      for (let j = i + 1; j < here.length; j++) {
        const a = here[i]
        const b = here[j]
        if (a.assignment.platform !== b.assignment.platform) continue
        if (a.start < b.end + HEADWAY_MIN && b.start < a.end + HEADWAY_MIN) {
          a.conflicted = true
          b.conflicted = true
        }
      }
    }
    // Plus any live platform.conflict events for this station.
    for (const c of conflicts) {
      if (c.station_code !== station.code) continue
      for (const b of here) {
        if (b.assignment.platform === c.platform && c.train_numbers.includes(b.assignment.train_number)) {
          b.conflicted = true
        }
      }
    }
    return here
  }, [assignments, station, conflicts, reassignedAt, lastEventAt])

  if (!station) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900/40 text-sm text-zinc-500">
        Awaiting platform assignments…
      </div>
    )
  }

  const rows = station.platform_count
  const nowMin = simTime ? minOfDay(simTime) : null
  const hourMarks: number[] = []
  for (let m = DAY_START; m <= DAY_END; m += 30) hourMarks.push(m)

  return (
    <div className="flex h-full flex-col rounded-lg border border-zinc-800 bg-zinc-950">
      <div className="flex items-center gap-3 border-b border-zinc-800 px-3 py-1.5">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-zinc-400">
          Platform occupancy
        </h2>
        <div className="flex gap-1">
          {stations.map((s) => (
            <button
              key={s.code}
              type="button"
              onClick={() => setSelected(s.code)}
              className={clsx(
                'rounded px-2 py-0.5 text-[11px] font-semibold tabular-nums transition-colors',
                s.code === (station.code)
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300',
              )}
            >
              {s.code}
            </button>
          ))}
        </div>
        <span className="ml-auto hidden text-[10px] text-zinc-600 xl:block">
          blocks = arrival → departure · shaded tail = 5 min headway · red pulse = conflict
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-3 py-2">
        {/* time axis */}
        <div className="relative ml-9 h-4 shrink-0">
          {hourMarks.map((m) => (
            <span
              key={m}
              className="absolute -translate-x-1/2 text-[9px] tabular-nums text-zinc-600"
              style={{ left: `${pct(m)}%` }}
            >
              {`${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`}
            </span>
          ))}
        </div>

        <div className="flex min-h-0 flex-1">
          {/* platform labels */}
          <div className="relative w-9 shrink-0">
            {Array.from({ length: rows }, (_, i) => (
              <span
                key={i}
                className="absolute left-0 flex items-center text-[10px] font-semibold text-zinc-500"
                style={{ top: `${(i / rows) * 100}%`, height: `${100 / rows}%` }}
              >
                P{i + 1}
              </span>
            ))}
          </div>

          {/* chart canvas — rows are %-based so the chart fills the panel */}
          <div className="relative min-h-[100px] flex-1 overflow-hidden">
            {/* row stripes */}
            {Array.from({ length: rows }, (_, i) => (
              <div
                key={i}
                className={clsx(
                  'absolute inset-x-0 border-b border-zinc-800/70',
                  i % 2 === 1 && 'bg-zinc-900/40',
                )}
                style={{ top: `${(i / rows) * 100}%`, height: `${100 / rows}%` }}
              />
            ))}
              {/* grid lines */}
              {hourMarks.map((m) => (
                <div
                  key={m}
                  className="absolute inset-y-0 w-px bg-zinc-800/60"
                  style={{ left: `${pct(m)}%` }}
                />
              ))}
              {/* now line */}
              {nowMin !== null && nowMin >= DAY_START && nowMin <= DAY_END && (
                <div
                  className="absolute inset-y-0 z-20 w-px bg-amber-400/80 shadow-[0_0_6px_#f59e0b]"
                  style={{ left: `${pct(nowMin)}%` }}
                />
              )}

              {/* occupancy blocks (absolutely positioned; top/left transitions animate
                  delay shifts and platform reassignments) */}
              {blocks.map((b) => {
                const left = Math.max(pct(b.start), 0)
                const width = Math.max(pct(Math.min(b.end, DAY_END)) - left, 1.4)
                const headwayW = (HEADWAY_MIN / SPAN) * 100
                const top = `${((b.assignment.platform - 1 + 0.14) / rows) * 100}%`
                const height = `${(0.72 / rows) * 100}%`
                const wide = width > 7 // enough room for the times line
                const train = trains[b.assignment.train_number]
                return (
                  <div key={b.assignment.train_number}>
                    {/* headway shading */}
                    <div
                      className="absolute rounded-r-sm bg-zinc-600/25 transition-all duration-700 ease-in-out"
                      style={{ top, height, left: `${left + width}%`, width: `${headwayW}%` }}
                    />
                    <div
                      className={clsx(
                        'absolute z-10 flex flex-col justify-center rounded-sm border px-1 transition-all duration-700 ease-in-out',
                        b.conflicted
                          ? 'conflict-pulse border-red-500 bg-red-500/25 text-red-100'
                          : b.reassigned
                            ? 'border-emerald-400 bg-emerald-500/15 text-emerald-100 ring-1 ring-emerald-400/60'
                            : 'border-zinc-700 bg-zinc-800/95 text-zinc-300',
                      )}
                      style={{ top, height, left: `${left}%`, width: `${width}%` }}
                      title={`${b.assignment.train_number} ${train?.name ?? ''} · ${timeHM(
                        b.assignment.arrival,
                      )}–${timeHM(b.assignment.departure)} · P${b.assignment.platform}`}
                    >
                      <span className="whitespace-nowrap text-[10px] font-bold leading-tight tabular-nums">
                        {b.assignment.train_number}
                      </span>
                      {wide && (
                        <span className="truncate text-[9px] leading-tight text-current opacity-60">
                          {timeHM(b.assignment.arrival)}–{timeHM(b.assignment.departure)}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}

              {blocks.length === 0 && (
                <div className="absolute inset-0 flex items-center justify-center text-xs text-zinc-600">
                  No occupancy in the 07:30–12:30 window
                </div>
              )}
          </div>
        </div>
      </div>
    </div>
  )
}
