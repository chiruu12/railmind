import { useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { useStore } from '../../store'
import { postScenario, setSimSpeed, simControl } from '../../lib/http'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2 border-b border-zinc-800/80 px-3 py-3">
      <h3 className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{title}</h3>
      {children}
    </section>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="block text-[10px] font-medium text-zinc-500">{children}</span>
}

const inputCls =
  'w-full rounded border border-zinc-700/80 bg-zinc-900 px-2 py-1 text-[11px] text-zinc-200 outline-none focus:border-amber-500/60'
const buttonCls =
  'w-full rounded border border-amber-600/70 bg-amber-600/15 py-1.5 text-[11px] font-semibold text-amber-300 transition-colors hover:bg-amber-600/30 disabled:opacity-40'
const ctrlBtnCls =
  'flex-1 rounded border border-zinc-700 bg-zinc-900 py-1 text-[11px] font-medium text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100'

export default function ScenarioDrawer() {
  const trains = useStore((s) => s.trains)
  const stations = useStore((s) => s.stations)
  const crews = useStore((s) => s.crews)
  const simSpeed = useStore((s) => s.simSpeed)

  const trainList = useMemo(
    () => Object.values(trains).sort((a, b) => a.number.localeCompare(b.number)),
    [trains],
  )
  const onDutyCrews = useMemo(() => crews.filter((c) => c.status === 'on_duty'), [crews])

  const [open, setOpen] = useState(true)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const statusTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // delay form
  const [delayTrain, setDelayTrain] = useState('12302')
  const [delayMin, setDelayMin] = useState(25)
  const [cause, setCause] = useState('loco traction failure')
  // platform block form
  const [blockStation, setBlockStation] = useState('CNB')
  const [blockPlatform, setBlockPlatform] = useState(1)
  const [blockDuration, setBlockDuration] = useState(45)
  // crew sick form
  const [sickCrew, setSickCrew] = useState('')
  // speed slider
  const [speed, setSpeed] = useState(simSpeed)
  const speedTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const report = (ok: boolean, text: string) => {
    setStatus({ ok, text })
    if (statusTimer.current) clearTimeout(statusTimer.current)
    statusTimer.current = setTimeout(() => setStatus(null), 4000)
  }

  const fire = (label: string, promise: Promise<unknown>) => {
    promise
      .then(() => report(true, `${label} sent`))
      .catch(() => report(false, `${label} failed — backend API offline?`))
  }

  if (!open) {
    return (
      <div className="flex w-9 shrink-0 flex-col items-center border-r border-zinc-800 bg-zinc-950 py-2">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200"
          title="Open scenario panel"
        >
          ▸
        </button>
        <span
          className="mt-3 text-[9px] font-bold uppercase tracking-[0.2em] text-zinc-600"
          style={{ writingMode: 'vertical-rl' }}
        >
          Scenarios
        </span>
      </div>
    )
  }

  const blockStationObj = stations.find((s) => s.code === blockStation)
  const platformCount = blockStationObj?.platform_count ?? 6

  return (
    <aside className="flex w-72 shrink-0 flex-col overflow-y-auto border-r border-zinc-800 bg-zinc-950">
      <div className="flex items-center border-b border-zinc-800 px-3 py-2">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-zinc-400">
          Scenario console
        </h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="ml-auto rounded p-1 text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200"
          title="Collapse"
        >
          ◂
        </button>
      </div>

      <Section title="Simulation">
        <div className="flex gap-2">
          <button type="button" className={ctrlBtnCls} onClick={() => fire('sim start', simControl('start'))}>
            ▶ Start
          </button>
          <button type="button" className={ctrlBtnCls} onClick={() => fire('sim pause', simControl('pause'))}>
            ⏸ Pause
          </button>
          <button type="button" className={ctrlBtnCls} onClick={() => fire('sim reset', simControl('reset'))}>
            ↺ Reset
          </button>
        </div>
        <div>
          <div className="flex items-baseline justify-between">
            <Label>Speed</Label>
            <span className="text-[11px] font-semibold tabular-nums text-zinc-300">×{speed}</span>
          </div>
          <input
            type="range"
            min={0.5}
            max={8}
            step={0.5}
            value={speed}
            className="w-full accent-amber-500"
            onChange={(e) => {
              const v = Number(e.target.value)
              setSpeed(v)
              if (speedTimer.current) clearTimeout(speedTimer.current)
              speedTimer.current = setTimeout(() => fire('sim speed', setSimSpeed(v)), 350)
            }}
          />
        </div>
      </Section>

      <Section title="Inject delay">
        <div className="space-y-1.5">
          <Label>Train</Label>
          <select className={inputCls} value={delayTrain} onChange={(e) => setDelayTrain(e.target.value)}>
            {trainList.length === 0 && <option value="12302">12302 — Howrah Rajdhani</option>}
            {trainList.map((t) => (
              <option key={t.number} value={t.number}>
                {t.number} — {t.name}
              </option>
            ))}
          </select>
          <div className="flex items-baseline justify-between">
            <Label>Delay</Label>
            <span className="text-[11px] font-semibold tabular-nums text-amber-300">
              +{delayMin} min
            </span>
          </div>
          <input
            type="range"
            min={5}
            max={60}
            step={5}
            value={delayMin}
            className="w-full accent-amber-500"
            onChange={(e) => setDelayMin(Number(e.target.value))}
          />
          <Label>Cause</Label>
          <input
            type="text"
            className={inputCls}
            value={cause}
            onChange={(e) => setCause(e.target.value)}
            placeholder="e.g. loco traction failure"
          />
          <button
            type="button"
            className={buttonCls}
            onClick={() =>
              fire(
                `delay ${delayTrain} +${delayMin}m`,
                postScenario('delay', {
                  train_number: delayTrain,
                  delay_min: delayMin,
                  cause,
                }),
              )
            }
          >
            ⚡ Inject delay
          </button>
        </div>
      </Section>

      <Section title="Block platform">
        <div className="space-y-1.5">
          <div className="flex gap-2">
            <div className="flex-1">
              <Label>Station</Label>
              <select
                className={inputCls}
                value={blockStation}
                onChange={(e) => {
                  setBlockStation(e.target.value)
                  setBlockPlatform(1)
                }}
              >
                {stations.length === 0 && <option value="CNB">CNB</option>}
                {stations.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.code}
                  </option>
                ))}
              </select>
            </div>
            <div className="w-16">
              <Label>Platform</Label>
              <select
                className={inputCls}
                value={blockPlatform}
                onChange={(e) => setBlockPlatform(Number(e.target.value))}
              >
                {Array.from({ length: platformCount }, (_, i) => (
                  <option key={i + 1} value={i + 1}>
                    P{i + 1}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex items-baseline justify-between">
            <Label>Duration</Label>
            <span className="text-[11px] font-semibold tabular-nums text-zinc-300">
              {blockDuration} min
            </span>
          </div>
          <input
            type="range"
            min={15}
            max={120}
            step={15}
            value={blockDuration}
            className="w-full accent-amber-500"
            onChange={(e) => setBlockDuration(Number(e.target.value))}
          />
          <button
            type="button"
            className={buttonCls}
            onClick={() =>
              fire(
                `block ${blockStation} P${blockPlatform}`,
                postScenario('platform_block', {
                  station_code: blockStation,
                  platform: blockPlatform,
                  duration_min: blockDuration,
                }),
              )
            }
          >
            ⛔ Block platform
          </button>
        </div>
      </Section>

      <Section title="Crew sick">
        <div className="space-y-1.5">
          <Label>Crew</Label>
          <select className={inputCls} value={sickCrew} onChange={(e) => setSickCrew(e.target.value)}>
            <option value="">— select on-duty crew —</option>
            {onDutyCrews.map((c) => (
              <option key={c.id} value={c.id}>
                {c.id} · {c.name} ({c.assigned_train ?? 'unassigned'})
              </option>
            ))}
          </select>
          <button
            type="button"
            className={buttonCls}
            disabled={sickCrew === ''}
            onClick={() => fire(`crew sick ${sickCrew}`, postScenario('crew_sick', { crew_id: sickCrew }))}
          >
            🤒 Report sick
          </button>
        </div>
      </Section>

      <div className="mt-auto px-3 py-2">
        {status && (
          <p
            className={clsx(
              'feed-in rounded border px-2 py-1 text-[10px] font-medium',
              status.ok
                ? 'border-emerald-600/50 bg-emerald-600/10 text-emerald-300'
                : 'border-red-600/50 bg-red-600/10 text-red-300',
            )}
          >
            {status.text}
          </p>
        )}
      </div>
    </aside>
  )
}
