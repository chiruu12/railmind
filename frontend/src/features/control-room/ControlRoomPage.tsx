import { Link } from 'react-router'
import AgentFeed from './AgentFeed'
import CorridorMap from './CorridorMap'
import KpiStrip from './KpiStrip'
import PlatformGantt from './PlatformGantt'
import ScenarioDrawer from './ScenarioDrawer'

export default function ControlRoomPage() {
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex items-center gap-4 border-b border-zinc-800 bg-zinc-950/95 px-4 py-2">
        <div className="flex items-center gap-2">
          <img
            src="/brand/rail-saarthi-mark.png"
            alt="Rail Saarthi"
            className="h-7 w-7 rounded-full"
          />
          <h1 className="text-base font-bold tracking-tight text-zinc-100">
            Rail <span className="text-amber-400">Saarthi</span>
          </h1>
          <span className="hidden text-[10px] font-medium uppercase tracking-widest text-zinc-600 lg:block">
            NDLS – CNB – PRYJ – DDU corridor
          </span>
        </div>
        <KpiStrip />
        <Link
          to="/passenger"
          className="rounded-md border border-zinc-800 px-2.5 py-1 text-[11px] font-medium text-zinc-400 hover:border-zinc-700 hover:text-zinc-200"
        >
          Passenger view ↗
        </Link>
      </header>

      <div className="flex min-h-0 flex-1">
        <ScenarioDrawer />
        <main className="flex min-w-0 flex-1 flex-col gap-2 p-2">
          <section className="min-h-0 flex-[5]">
            <CorridorMap />
          </section>
          <section className="min-h-0 flex-[4]">
            <PlatformGantt />
          </section>
        </main>
        <AgentFeed />
      </div>
    </div>
  )
}
