import { Suspense, lazy, useEffect } from 'react'
import type { ComponentType } from 'react'
import { Link, Route, Routes } from 'react-router'
import { useEventStream } from './api/ws'
import { fetchState } from './lib/http'
import { useStore } from './store'
import ControlRoomPage from './features/control-room/ControlRoomPage'

/**
 * WS5 ships src/features/passenger/index.tsx later. import.meta.glob resolves to an
 * empty map while the module is missing, so this app builds and runs without it —
 * never turn this into a static import.
 */
const passengerModules = import.meta.glob<{ default: ComponentType }>(
  './features/passenger/index.tsx',
)
const passengerLoader = passengerModules['./features/passenger/index.tsx']

function PassengerPlaceholder() {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-zinc-950">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900">
        <span className="h-3 w-3 animate-ping rounded-full bg-amber-400" />
      </div>
      <div className="text-center">
        <p className="text-lg font-semibold text-zinc-200">Passenger view — coming online</p>
        <p className="mt-1 text-sm text-zinc-500">
          The passenger assistant module has not been deployed to this build yet.
        </p>
      </div>
      <Link
        to="/"
        className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-900"
      >
        ← Back to control room
      </Link>
    </div>
  )
}

const PassengerView = lazy(async () => {
  if (!passengerLoader) return { default: PassengerPlaceholder }
  try {
    return await passengerLoader()
  } catch {
    return { default: PassengerPlaceholder }
  }
})

/** Mounted once for every route: hydrates the store and folds the live event stream. */
function EventBridge() {
  const applyEnvelope = useStore((s) => s.applyEnvelope)
  const hydrate = useStore((s) => s.hydrate)
  useEventStream(applyEnvelope)
  useEffect(() => {
    fetchState()
      .then(hydrate)
      .catch(() => {
        // Backend REST not up (e.g. mock-server-only dev) — the mock's
        // state.snapshot envelope hydrates the store instead.
      })
  }, [hydrate])
  return null
}

export default function App() {
  return (
    <>
      <EventBridge />
      <Routes>
        <Route path="/" element={<ControlRoomPage />} />
        <Route
          path="/passenger"
          element={
            <Suspense fallback={<PassengerPlaceholder />}>
              <PassengerView />
            </Suspense>
          }
        />
      </Routes>
    </>
  )
}
