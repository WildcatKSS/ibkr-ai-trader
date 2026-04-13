import { useEffect, useState } from 'react'
import { getTrades, type Trade } from '../api'

const PAGE_SIZE = 25

export default function TradeHistory() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [statusFilter, setStatusFilter] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    getTrades({
      symbol: symbolFilter || undefined,
      status_filter: statusFilter || undefined,
      limit: PAGE_SIZE,
      offset,
    })
      .then((data) => {
        setTrades(data.trades)
        setTotal(data.total)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [offset, statusFilter, symbolFilter])

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">Trade History</h1>

      {/* Filters */}
      <div className="flex gap-3 items-center">
        <input
          type="text"
          placeholder="Symbol..."
          value={symbolFilter}
          onChange={(e) => {
            setSymbolFilter(e.target.value.toUpperCase())
            setOffset(0)
          }}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white w-32 focus:outline-none focus:border-blue-500"
        />
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value)
            setOffset(0)
          }}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          <option value="">All statuses</option>
          <option value="closed">Closed</option>
          <option value="open">Open</option>
          <option value="filled">Filled</option>
          <option value="pending">Pending</option>
          <option value="dryrun">Dryrun</option>
          <option value="error">Error</option>
        </select>
        <span className="text-sm text-gray-500">{total} trades total</span>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead className="text-gray-400 border-b border-gray-800">
            <tr>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Symbol</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Shares</th>
              <th className="px-4 py-3">Entry</th>
              <th className="px-4 py-3">Exit</th>
              <th className="px-4 py-3">P&L</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">ML Prob</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-gray-500">
                  Loading...
                </td>
              </tr>
            ) : trades.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-gray-500">
                  No trades found
                </td>
              </tr>
            ) : (
              trades.map((t) => (
                <tr key={t.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-4 py-2 text-gray-400 whitespace-nowrap">
                    {t.created_at ? new Date(t.created_at).toLocaleString() : '--'}
                  </td>
                  <td className="px-4 py-2 font-medium text-white">{t.symbol}</td>
                  <td
                    className={`px-4 py-2 ${
                      t.action === 'long' ? 'text-green-400' : 'text-red-400'
                    }`}
                  >
                    {t.action.toUpperCase()}
                  </td>
                  <td className="px-4 py-2">{t.shares}</td>
                  <td className="px-4 py-2">${t.entry_price.toFixed(2)}</td>
                  <td className="px-4 py-2">
                    {t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '--'}
                  </td>
                  <td
                    className={`px-4 py-2 font-medium ${
                      t.pnl != null && t.pnl > 0
                        ? 'text-green-400'
                        : t.pnl != null && t.pnl < 0
                        ? 'text-red-400'
                        : ''
                    }`}
                  >
                    {t.pnl != null ? `$${t.pnl.toFixed(2)}` : '--'}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${
                        t.status === 'closed'
                          ? 'bg-gray-700 text-gray-300'
                          : t.status === 'filled'
                          ? 'bg-green-900/50 text-green-400'
                          : t.status === 'error'
                          ? 'bg-red-900/50 text-red-400'
                          : 'bg-blue-900/50 text-blue-400'
                      }`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-gray-400">
                    {(t.ml_probability * 100).toFixed(0)}%
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center gap-3 justify-center">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="px-3 py-1 text-sm bg-gray-800 rounded hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <span className="text-sm text-gray-400">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
            className="px-3 py-1 text-sm bg-gray-800 rounded hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
