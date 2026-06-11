import { useMemo } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip } from 'react-leaflet'
import { useStore } from '../../store'
import {
  delayColor,
  delayLabel,
  delayTextClass,
  latLonAtKm,
  nextStopOf,
  timeHM,
} from '../../lib/format'

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
const ATTRIBUTION =
  '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

/** Stable per-(train,color) icons so Leaflet keeps the DOM node and CSS transforms animate. */
const iconCache = new Map<string, L.DivIcon>()
function trainIcon(trainNumber: string, color: string): L.DivIcon {
  const key = `${trainNumber}:${color}`
  let icon = iconCache.get(key)
  if (!icon) {
    icon = L.divIcon({
      className: 'train-marker',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
      popupAnchor: [0, -10],
      html: `<div class="train-dot" style="--dot:${color}"><span class="train-dot-label">${trainNumber}</span></div>`,
    })
    iconCache.set(key, icon)
  }
  return icon
}

export default function CorridorMap() {
  const stations = useStore((s) => s.stations)
  const trains = useStore((s) => s.trains)
  const positions = useStore((s) => s.positions)
  const simTime = useStore((s) => s.simTime)

  const sorted = useMemo(
    () => [...stations].sort((a, b) => a.km_offset - b.km_offset),
    [stations],
  )
  const bounds = useMemo(
    () =>
      sorted.length > 0
        ? L.latLngBounds(sorted.map((s) => [s.lat, s.lon] as [number, number])).pad(0.25)
        : null,
    [sorted],
  )

  if (!bounds) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900/40 text-sm text-zinc-500">
        Awaiting network snapshot…
      </div>
    )
  }

  return (
    <div className="relative h-full overflow-hidden rounded-lg border border-zinc-800">
      <MapContainer bounds={bounds} className="h-full w-full" zoomControl={false}>
        <TileLayer url={TILE_URL} attribution={ATTRIBUTION} />

        <Polyline
          positions={sorted.map((s) => [s.lat, s.lon] as [number, number])}
          pathOptions={{ color: '#52525b', weight: 2.5, dashArray: '7 7', opacity: 0.9 }}
        />

        {sorted.map((station) => (
          <CircleMarker
            key={station.code}
            center={[station.lat, station.lon]}
            radius={6}
            pathOptions={{ color: '#a1a1aa', weight: 2, fillColor: '#18181b', fillOpacity: 1 }}
          >
            <Tooltip permanent direction="top" offset={[0, -8]} className="!text-[10px] !font-semibold">
              {station.code}
            </Tooltip>
            <Popup>
              <div className="space-y-0.5 text-xs">
                <div className="font-semibold text-zinc-100">{station.name}</div>
                <div className="text-zinc-400">
                  {station.platform_count} platforms · km {station.km_offset}
                </div>
              </div>
            </Popup>
          </CircleMarker>
        ))}

        {Object.values(trains).map((train) => {
          const pos = positions[train.number]
          const [lat, lon] = pos ? [pos.lat, pos.lon] : latLonAtKm(sorted, train.km_offset)
          const status = pos?.status ?? train.status
          const delay = pos?.delay_min ?? train.delay_min
          const color = status === 'terminated' ? '#52525b' : delayColor(delay)
          const next = nextStopOf(train, simTime)
          return (
            <Marker key={train.number} position={[lat, lon]} icon={trainIcon(train.number, color)}>
              <Popup>
                <div className="space-y-1 text-xs">
                  <div className="font-semibold text-zinc-100">
                    {train.number} · {train.name}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="capitalize text-zinc-400">{status.replace('_', ' ')}</span>
                    <span className={`font-semibold tabular-nums ${delayTextClass(delay)}`}>
                      {delayLabel(delay)}
                    </span>
                  </div>
                  <div className="text-zinc-400">
                    {next ? (
                      <>
                        Next stop <span className="font-medium text-zinc-200">{next.station_code}</span>{' '}
                        · eta <span className="tabular-nums">{timeHM(next.eta)}</span>
                      </>
                    ) : (
                      'At terminus'
                    )}
                  </div>
                </div>
              </Popup>
            </Marker>
          )
        })}
      </MapContainer>

      <div className="pointer-events-none absolute bottom-2 left-2 z-[1000] flex gap-3 rounded-md border border-zinc-800 bg-zinc-950/85 px-2.5 py-1.5 text-[10px] font-medium text-zinc-400">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-emerald-500" /> on time
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-amber-500" /> 5–15 min
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-red-500" /> &gt;15 min
        </span>
      </div>
    </div>
  )
}
