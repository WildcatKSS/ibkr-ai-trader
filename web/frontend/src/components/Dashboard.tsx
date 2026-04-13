import { useEffect, useState } from 'react'
import { getStatus, getPortfolio, getPerformance, type PortfolioData, type PerformanceData } from '../api'

export default function Dashboard() {
  const [status, setStatus] = useState<{
    trading_mode: string
    market_open: boolean
    trading_day: boolean
  } | null>(null)
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null)
  const [perf, setPerf] = useState<PerformanceData | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([getStatus(), getPortfolio(), getPerformance('all')])
      .then(([s, p, pf]) => {
        setStatus(s)
        setPortfolio(p)
        setPerf(pf)
      })
      .catch((e) => setError(e.message))
  }, [])

  if (error) return <p className="text-red-400">{error}</p>
  if (!status) return <p className="text-gray-400">Loading...</p>

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Dashboard</h1>

      {/* Status cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card
          label="Trading Mode"
          value={status.trading_mode.toUpperCase()}
          color={
            status.trading_mode === 'live'
              ? 'text-red-400'
              : status.trading_mode === 'paper'
              ? 'text-yellow-400'
              : 'text-blue-400'
          }
        />
        <Card
          label="Market"
          value={status.market_open ? 'OPEN' : 'CLOSED'}
          color={status.market_open ? 'text-green-400' : 'text-gray-400'}
        />
        <Card
          label="Daily P&L"
          value={portfolio ? `$${portfolio.daily_pnl.toFixed(2)}` : '--'}
          color={
            portfolio && portfolio.daily_pnl > 0
              ? 'text-green-400'
              : portfolio && portfolio.daily_pnl < 0
              ? 'text-red-400'
              : 'text-gray-300'
          }
        />
        <Card
          label="Today's Trades"
          value={portfolio ? String(portfolio.daily_trades) : '--'}
          color="text-gray-300"
        />
      </div>

      {/* Performance summary */}
      {perf && perf.trade_count > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">All-Time Performance</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <Stat label="Total P&L" value={`$${perf.total_pnl.toFixed(2)}`} />
            <Stat label="Trade Count" value={String(perf.trade_count)} />
            <Stat label="Win Rate" value={`${perf.win_rate.toFixed(1)}%`} />
            <Stat label="Profit Factor" value={perf.profit_factor.toFixed(2)} />
            <Stat label="Avg P&L" value={`$${perf.avg_pnl.toFixed(2)}`} />
            <Stat label="Largest Win" value={`$${perf.largest_win.toFixed(2)}`} />
            <Stat label="Largest Loss" value={`$${perf.largest_loss.toFixed(2)}`} />
          </div>
        </div>
      )}

      {/* Open positions */}
      {portfolio && portfolio.open_positions.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">
            Open Positions ({portfolio.position_count})
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-gray-400 border-b border-gray-800">
                <tr>
                  <th className="pb-2">Symbol</th>
                  <th className="pb-2">Action</th>
                  <th className="pb-2">Shares</th>
                  <th className="pb-2">Entry</th>
                  <th className="pb-2">Target</th>
                  <th className="pb-2">Stop</th>
                  <th className="pb-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.open_positions.map((p, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="py-2 font-medium text-white">{p.symbol}</td>
                    <td className={p.action === 'long' ? 'text-green-400' : 'text-red-400'}>
                      {p.action.toUpperCase()}
                    </td>
                    <td>{p.shares}</td>
                    <td>${p.entry_price.toFixed(2)}</td>
                    <td className="text-green-400">${p.target_price.toFixed(2)}</td>
                    <td className="text-red-400">${p.stop_price.toFixed(2)}</td>
                    <td>{p.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {portfolio && portfolio.open_positions.length === 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-center text-gray-500">
          No open positions
        </div>
      )}
    </div>
  )
}

function Card({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-xl font-bold mt-1 ${color}`}>{value}</p>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-gray-500">{label}</p>
      <p className="text-white font-medium">{value}</p>
    </div>
  )
}
