import { useCallback, useEffect, useState } from 'react'
import {
  UniverseSelection,
  approveSelection,
  getPendingSelection,
  getSelectionHistory,
  rejectSelection,
} from '../api'

/**
 * UniverseApproval — visible only when UNIVERSE_APPROVAL_MODE === 'approval'.
 *
 * Each trading day the engine writes a ranked list of candidate symbols and
 * waits for the user to approve exactly one.  This page shows the pending
 * scan (if any) with per-symbol analysis, lets the user click the chosen
 * symbol, and keeps a history of past decisions.
 */
export default function UniverseApproval() {
  const [pending, setPending] = useState<UniverseSelection | null>(null)
  const [history, setHistory] = useState<UniverseSelection[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [p, h] = await Promise.all([
        getPendingSelection(),
        getSelectionHistory(),
      ])
      setPending(p ?? null)
      setHistory(h)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
    // Poll every 30s so a freshly completed scan appears without a reload.
    const t = window.setInterval(refresh, 30_000)
    return () => window.clearInterval(t)
  }, [refresh])

  async function handleApprove() {
    if (!pending || !selected) return
    if (!confirm(`Approve ${selected} for today's trading?`)) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await approveSelection(pending.id, selected)
      setMessage(`${selected} approved — engine will start trading on the next tick.`)
      setSelected(null)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleReject() {
    if (!pending) return
    if (!confirm('Reject today\'s scan? No symbol will be traded today.')) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await rejectSelection(pending.id, rejectReason)
      setMessage('Scan rejected — no trading today.')
      setRejectReason('')
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Universe Approval</h1>

      {error && <p className="text-sm text-red-400">{error}</p>}
      {message && <p className="text-sm text-green-400">{message}</p>}

      {/* Pending scan */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h2 className="text-lg font-semibold text-white mb-3">
          Pending Scan
          {pending && (
            <span className="ml-2 text-sm font-normal text-gray-400">
              {pending.scan_date}
            </span>
          )}
        </h2>

        {!pending ? (
          <p className="text-gray-500 italic">
            No scan awaiting approval. A new scan runs automatically on each
            trading day.
          </p>
        ) : (
          <>
            {pending.reasoning && (
              <blockquote className="text-gray-300 italic border-l-2 border-gray-700 pl-3 mb-4 whitespace-pre-wrap">
                {pending.reasoning}
              </blockquote>
            )}

            <div className="space-y-2">
              {pending.candidates.map((c) => {
                const isSelected = selected === c.symbol
                return (
                  <button
                    key={c.symbol}
                    type="button"
                    onClick={() => setSelected(c.symbol)}
                    className={`w-full text-left rounded border px-4 py-3 transition-colors ${
                      isSelected
                        ? 'border-blue-500 bg-blue-500/10'
                        : 'border-gray-800 bg-gray-900 hover:bg-gray-800/60'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-mono text-white text-lg">{c.symbol}</span>
                        <span className="ml-3 text-sm text-gray-400">
                          score {c.score.toFixed(1)}
                        </span>
                        <span className="ml-2 text-xs">
                          {c.passes_all_core && (
                            <Tag label="7/7 core" color="green" />
                          )}
                          {c.near_resistance && <Tag label="near-resistance" color="yellow" />}
                          {c.has_momentum && <Tag label="momentum" color="purple" />}
                          {c.pullback_above_ema9 && <Tag label="pullback" color="blue" />}
                        </span>
                      </div>
                    </div>
                    {c.analysis && (
                      <p className="text-sm text-gray-300 mt-1">{c.analysis}</p>
                    )}
                  </button>
                )
              })}
            </div>

            <div className="flex flex-wrap items-center gap-3 mt-5">
              <button
                type="button"
                onClick={handleApprove}
                disabled={!selected || busy}
                className="px-4 py-2 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 text-white text-sm rounded"
              >
                {busy ? 'Approving…' : selected ? `Approve ${selected}` : 'Select a symbol'}
              </button>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Reject reason (optional)"
                  maxLength={500}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm w-64"
                />
                <button
                  type="button"
                  onClick={handleReject}
                  disabled={busy}
                  className="px-4 py-2 bg-red-700 hover:bg-red-600 disabled:bg-gray-700 text-white text-sm rounded"
                >
                  Reject scan
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* History */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h2 className="text-lg font-semibold text-white mb-3">History</h2>
        {history.length === 0 ? (
          <p className="text-gray-500 italic">No past scans.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-400 border-b border-gray-800">
                  <th className="py-2 pr-4">Date</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Symbol</th>
                  <th className="py-2 pr-4">Candidates</th>
                  <th className="py-2 pr-4">Decided</th>
                </tr>
              </thead>
              <tbody>
                {history.map((s) => (
                  <tr key={s.id} className="border-b border-gray-800/60">
                    <td className="py-2 pr-4 text-gray-200">{s.scan_date}</td>
                    <td className="py-2 pr-4">
                      <span className={STATUS_COLORS[s.status] ?? 'text-gray-400'}>{s.status}</span>
                    </td>
                    <td className="py-2 pr-4 font-mono text-gray-200">{s.selected_symbol || '—'}</td>
                    <td className="py-2 pr-4 text-gray-400">{s.candidates.length}</td>
                    <td className="py-2 pr-4 text-gray-500">
                      {s.decided_at?.slice(0, 19) || '—'}{' '}
                      {s.decided_by && <span className="text-xs">by {s.decided_by}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

const STATUS_COLORS: Record<string, string> = {
  pending_approval: 'text-yellow-400',
  approved: 'text-green-400',
  rejected: 'text-red-400',
  autonomous: 'text-blue-400',
}

function Tag({ label, color }: { label: string; color: string }) {
  const palette: Record<string, string> = {
    green: 'bg-green-800/50 text-green-300',
    yellow: 'bg-yellow-800/50 text-yellow-300',
    purple: 'bg-purple-800/50 text-purple-300',
    blue: 'bg-blue-800/50 text-blue-300',
  }
  return (
    <span className={`ml-2 px-2 py-0.5 rounded text-[10px] ${palette[color] || 'bg-gray-700 text-gray-200'}`}>
      {label}
    </span>
  )
}
