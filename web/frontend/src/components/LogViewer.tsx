import { useCallback, useEffect, useRef, useState } from 'react'
import { requestStreamToken } from '../api'

/**
 * LogViewer — live Server-Sent Events stream of bot log entries.
 *
 * Authentication flow:
 *   1. POST /api/logs/stream-token with Bearer → receive a short-lived token.
 *   2. Open EventSource on /api/logs/stream?stream_token=... to start streaming.
 *
 * Behaviour:
 *   - Auto-scrolls to the latest entry when the user is at the bottom.
 *   - Keeps at most 1 000 entries in memory to prevent DOM bloat.
 *   - Filters (category/level) re-open the stream with a fresh token.
 *   - On connection drop or stream error, automatically reconnects after 3 s.
 */

interface LogEntry {
  id: number
  timestamp: string | null
  level: string
  category: string
  module: string
  message: string
  extra: Record<string, unknown> | null
}

const CATEGORIES = [
  '', 'universe', 'signals', 'ml', 'risk',
  'trading', 'ibkr', 'web', 'claude', 'sentiment',
]
const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const MAX_ENTRIES = 1000

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'text-gray-500',
  INFO: 'text-blue-300',
  WARNING: 'text-yellow-300',
  ERROR: 'text-red-400',
  CRITICAL: 'text-red-500 font-bold',
}

export default function LogViewer() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [category, setCategory] = useState('')
  const [level, setLevel] = useState('')
  const [paused, setPaused] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')

  const esRef = useRef<EventSource | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const reconnectTimerRef = useRef<number | null>(null)

  const closeStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const openStream = useCallback(async () => {
    closeStream()
    setError('')
    try {
      const token = await requestStreamToken()
      const qs = new URLSearchParams({ stream_token: token })
      if (category) qs.set('category', category)
      if (level) qs.set('level', level)
      const es = new EventSource(`/api/logs/stream?${qs.toString()}`)
      esRef.current = es
      es.onopen = () => setConnected(true)
      es.onmessage = (ev) => {
        try {
          const data: LogEntry = JSON.parse(ev.data)
          setEntries((prev) => {
            const next = [...prev, data]
            return next.length > MAX_ENTRIES ? next.slice(-MAX_ENTRIES) : next
          })
        } catch {
          // Malformed payload — ignore.
        }
      }
      es.onerror = () => {
        setConnected(false)
        es.close()
        esRef.current = null
        // Reconnect after 3 seconds unless the user paused.
        reconnectTimerRef.current = window.setTimeout(() => {
          if (!paused) openStream()
        }, 3000)
      }
    } catch (e) {
      setConnected(false)
      setError(e instanceof Error ? e.message : 'Cannot start log stream')
      // Retry after 5 s on token-issue errors.
      reconnectTimerRef.current = window.setTimeout(() => {
        if (!paused) openStream()
      }, 5000)
    }
  }, [category, level, paused, closeStream])

  useEffect(() => {
    if (!paused) openStream()
    else closeStream()
    return closeStream
  }, [openStream, paused, closeStream])

  // Auto-scroll only when user is already near the bottom.
  useEffect(() => {
    if (!autoScrollRef.current) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries])

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    autoScrollRef.current = nearBottom
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Live Logs</h1>
        <div className="flex items-center gap-2 text-sm">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`}
          />
          <span className="text-gray-400">{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-wrap items-center gap-3">
        <Select label="Category" value={category} onChange={setCategory} options={CATEGORIES} />
        <Select label="Level" value={level} onChange={setLevel} options={LEVELS} />
        <button
          type="button"
          onClick={() => setPaused((p) => !p)}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-white text-sm rounded"
        >
          {paused ? 'Resume' : 'Pause'}
        </button>
        <button
          type="button"
          onClick={() => setEntries([])}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-white text-sm rounded"
        >
          Clear
        </button>
        <span className="ml-auto text-xs text-gray-500">{entries.length} / {MAX_ENTRIES}</span>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <div
        onScroll={handleScroll}
        className="bg-black border border-gray-800 rounded-lg p-3 font-mono text-xs h-[70vh] overflow-auto"
      >
        {entries.length === 0 && !error && (
          <p className="text-gray-500 italic">Waiting for log entries…</p>
        )}
        {entries.map((e) => (
          <div key={e.id} className="whitespace-pre-wrap leading-relaxed">
            <span className="text-gray-600">{e.timestamp?.slice(11, 19)}</span>{' '}
            <span className={LEVEL_COLORS[e.level] ?? 'text-gray-300'}>{e.level.padEnd(7)}</span>{' '}
            <span className="text-purple-400">[{e.category}]</span>{' '}
            <span className="text-gray-300">{e.message}</span>
            {e.extra && Object.keys(e.extra).length > 0 && (
              <span className="text-gray-500"> {JSON.stringify(e.extra)}</span>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

function Select({
  label, value, onChange, options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: string[]
}) {
  return (
    <label className="text-sm text-gray-300">
      {label}{' '}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
      >
        {options.map((o) => (
          <option key={o} value={o}>{o || 'all'}</option>
        ))}
      </select>
    </label>
  )
}
