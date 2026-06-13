/**
 * Shared formatting + corridor math helpers (pure functions, no React).
 * All domain timestamps are naive IST sim-time ISO strings (docs/CONTRACTS.md).
 */

import type { Station, Train } from '../api/types'

/** Shift a naive ISO datetime string by N minutes (string-safe, no TZ surprises). */
export function shiftIso(iso: string, minutes: number): string {
  const [datePart, timePart = '00:00:00'] = iso.split('T')
  const [y, mo, d] = datePart.split('-').map(Number)
  const [h, mi, se] = timePart.split(':')
  const dt = new Date(
    Date.UTC(y, mo - 1, d, Number(h), Number(mi), Math.floor(parseFloat(se ?? '0')) || 0),
  )
  dt.setUTCMinutes(dt.getUTCMinutes() + minutes)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}` +
    `T${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}:${pad(dt.getUTCSeconds())}`
  )
}

/** Minutes since midnight for a naive ISO datetime ("...T09:35:00" → 575). */
export function minOfDay(iso: string): number {
  return Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16))
}

/** "HH:MM" from a naive ISO datetime, or a placeholder. */
export function timeHM(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 16) : '--:--'
}

/** "HH:MM:SS" from a naive ISO datetime, or a placeholder. */
export function timeHMS(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 19) : '--:--:--'
}

/** Status accent hex by delay: emerald on-time, amber 5–15 min, red >15. */
export function delayColor(delayMin: number): string {
  if (delayMin > 15) return '#ef4444'
  if (delayMin >= 5) return '#f59e0b'
  return '#10b981'
}

/** Tailwind text class matching delayColor (literal strings so v4 picks them up). */
export function delayTextClass(delayMin: number): string {
  if (delayMin > 15) return 'text-red-400'
  if (delayMin >= 5) return 'text-amber-400'
  return 'text-emerald-400'
}

export function delayLabel(delayMin: number): string {
  return delayMin > 0 ? `+${delayMin} min` : 'on time'
}

/**
 * Strip Markdown markup so LLM-authored agent text renders as clean plain text.
 * The feed shows raw strings, so stray `**bold**`, `` `code` ``, links and
 * bullets would otherwise leak their syntax into the UI.
 */
export function plainText(input: string): string {
  return input
    .replace(/```[\s\S]*?```/g, (m) => m.replace(/```/g, '').trim()) // fenced code
    .replace(/`([^`]+)`/g, '$1') // inline code
    .replace(/!?\[([^\]]+)\]\([^)]*\)/g, '$1') // links / images → text
    .replace(/(\*\*|__)(.*?)\1/g, '$2') // bold
    .replace(/(\*|_)(.*?)\1/g, '$2') // italic
    .replace(/~~(.*?)~~/g, '$1') // strikethrough
    .replace(/^\s{0,3}#{1,6}\s+/gm, '') // headings
    .replace(/^\s*>\s?/gm, '') // blockquotes
    .replace(/^\s*[-*+]\s+/gm, '') // bullet markers
    .replace(/[ \t]+\n/g, '\n') // trailing spaces
    .trim()
}

/** Next scheduled stop (delay-adjusted) strictly after the current sim time. */
export function nextStopOf(
  train: Train,
  simTime: string | null,
): { station_code: string; eta: string } | null {
  if (!simTime) return null
  for (const stop of train.route) {
    const sched = stop.sched_arrival ?? stop.sched_departure
    if (!sched) continue
    const eta = shiftIso(sched, train.delay_min)
    if (eta > simTime) return { station_code: stop.station_code, eta }
  }
  return null
}

/** Interpolate lat/lon along the corridor for a km offset (stations define the line). */
export function latLonAtKm(stations: Station[], km: number): [number, number] {
  const sorted = [...stations].sort((a, b) => a.km_offset - b.km_offset)
  if (sorted.length === 0) return [0, 0]
  if (km <= sorted[0].km_offset) return [sorted[0].lat, sorted[0].lon]
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i]
    const b = sorted[i + 1]
    if (km <= b.km_offset) {
      const f = (km - a.km_offset) / Math.max(b.km_offset - a.km_offset, 0.001)
      return [a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f]
    }
  }
  const last = sorted[sorted.length - 1]
  return [last.lat, last.lon]
}
