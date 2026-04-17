import { useEffect, useState } from 'react'
import {
  getServiceStatus,
  startService,
  stopService,
  restartService,
  type ServiceStatus,
} from '../api'

/**
 * BotControl — start/stop/restart the ibkr-bot systemd unit.
 *
 * Safety:
 *   - Stop and Restart require an explicit confirm dialog.
 *   - Stop shows a prominent warning if the market is open.
 *   - Status auto-refreshes every 10 seconds.
 *   - Buttons are disabled while an action is in progress.
 */
export default function BotControl({ marketOpen }: { marketOpen: boolean }) {
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function refresh() {
    try {
      setStatus(await getServiceStatus())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Status error')
    }
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 10_000)
    return () => clearInterval(t)
  }, [])

  async function runAction(name: 'start' | 'stop' | 'restart') {
    setErr('')
    setBusy(true)
    try {
      if (name === 'start') await startService()
      if (name === 'stop') await stopService()
      if (name === 'restart') await restartService()
      // Give systemd a moment to transition before polling state.
      setTimeout(refresh, 1500)
    } catch (e) {
      setErr(e instanceof Error ? e.message : `${name} failed`)
    } finally {
      setBusy(false)
    }
  }

  function confirmStop() {
    const msg = marketOpen
      ? 'WARNING: the market is currently OPEN. Stopping the bot means no new signals will be generated. Open positions will be closed by the shutdown routine (paper/live only). Continue?'
      : 'Stop the trading bot service?'
    if (window.confirm(msg)) runAction('stop')
  }

  function confirmRestart() {
    if (window.confirm('Restart the trading bot service? The current tick will finish first.'))
      runAction('restart')
  }

  const active = status?.active ?? false
  const dot = active ? 'bg-green-500' : 'bg-red-500'
  const label = status ? status.state.toUpperCase() : 'UNKNOWN'

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <span className={`inline-block h-3 w-3 rounded-full ${dot}`} />
          <div>
            <p className="text-xs text-gray-500 uppercase tracking-wide">Bot Service</p>
            <p className="text-lg font-semibold text-white">{label}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy || active}
            onClick={() => runAction('start')}
            className="px-3 py-1.5 bg-green-700 hover:bg-green-600 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
          >
            Start
          </button>
          <button
            type="button"
            disabled={busy || !active}
            onClick={confirmStop}
            className="px-3 py-1.5 bg-red-700 hover:bg-red-600 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
          >
            Stop
          </button>
          <button
            type="button"
            disabled={busy || !active}
            onClick={confirmRestart}
            className="px-3 py-1.5 bg-yellow-700 hover:bg-yellow-600 disabled:bg-gray-700 disabled:text-gray-500 text-white text-sm rounded transition-colors"
          >
            Restart
          </button>
        </div>
      </div>
      {err && <p className="text-xs text-red-400 mt-3">{err}</p>}
    </div>
  )
}
