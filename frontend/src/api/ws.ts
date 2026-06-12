/**
 * WebSocket hook stub — WS4 hardens this (reconnect, event store wiring).
 * Connects to /ws (proxied to the backend in dev, see vite.config.ts).
 */

import { useEffect, useRef, useLayoutEffect } from 'react'
import type { EventEnvelope, Topic, TopicPayloads } from './types'

export type EventHandler = (envelope: EventEnvelope) => void

export function wsUrl(): string {
  const override = import.meta.env.VITE_WS_URL as string | undefined
  if (override) return override
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

export function useEventStream(onEvent: EventHandler): void {
  const handlerRef = useRef(onEvent)
  useLayoutEffect(() => { handlerRef.current = onEvent })

  useEffect(() => {
    let socket: WebSocket | null = null
    let closed = false
    let retryMs = 500

    const connect = () => {
      socket = new WebSocket(wsUrl())
      socket.onmessage = (msg) => {
        try {
          handlerRef.current(JSON.parse(msg.data) as EventEnvelope)
        } catch {
          // ignore malformed frames
        }
      }
      socket.onopen = () => {
        retryMs = 500
      }
      socket.onclose = () => {
        if (!closed) {
          setTimeout(connect, retryMs)
          retryMs = Math.min(retryMs * 2, 5000)
        }
      }
    }
    connect()

    return () => {
      closed = true
      socket?.close()
    }
  }, [])
}

export function isTopic<T extends Topic>(
  envelope: EventEnvelope,
  topic: T,
): envelope is EventEnvelope<TopicPayloads[T]> {
  return envelope.topic === topic
}
