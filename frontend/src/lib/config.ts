/**
 * Backend origin for production builds.
 *
 * Dev: VITE_API_BASE is unset → '' → relative /api paths, proxied to :8000
 * by the Vite dev server (see vite.config.ts).
 * Prod: set VITE_API_BASE to the Render backend, e.g.
 *   VITE_API_BASE=https://railmind-backend.onrender.com
 * (and VITE_WS_URL=wss://railmind-backend.onrender.com/ws — see api/ws.ts).
 */
export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

export const apiUrl = (path: string): string => `${API_BASE}${path}`
