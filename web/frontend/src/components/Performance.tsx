import { useEffect, useState } from 'react'
import { getPerformance, getTrades, type PerformanceData } from '../api'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'

const PERIODS = [
  { value: '1d', label: '1 Day' },
  { value: '7d', label: '7 Days' },
  { value: '30d', label: '30 Days' },
  { value: 'all', label: 'All Time' },
]

export default function Performance() {
  const [period, setPeriod] = useState('all')
  const [data, setData] = useState<PerformanceData | null>(null)
  const [pnlCurve, setPnlCurve] = useState<{ trade: number; cumPnl: number }[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    getPerformance(period)
      .then(setData)
      .catch((e) => setError(e.message))
  }, [period])

  useEffect(() => {
    getTrades({ status_filter: 'closed', limit: 500 })
      .then((res) => {
        const sorted = [...res.trades].reverse()
        let cum = 0
        const curve = sorted.map((t, i) => {
          cum += t.pnl ?? 0
          return { trade: i + 1, cumPnl: Math.round(cum * 100) / 100 }
        })
        setPnlCurve(curve)
      })
      .catch(() => {})
  }, [])

  if (error) return <p className="text-red-400">{error}</p>
  if (!data) return <p className="text-gray-400">Loading...</p>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Performance</h1>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-3 py-1 text-sm rounded transition-colors ${
                period === p.value
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard
          label="Total P&L"
          value={`$${data.total_pnl.toFixed(2)}`}
          color={data.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <MetricCard label="Trade Count" value={String(data.trade_count)} />
        <MetricCard
          label="Win Rate"
          value={`${data.win_rate.toFixed(1)}%`}
          color={data.win_rate >= 50 ? 'text-green-400' : 'text-red-400'}
        />
        <MetricCard label="Profit Factor" value={data.profit_factor.toFixed(2)} />
        <MetricCard label="Avg P&L" value={`$${data.avg_pnl.toFixed(2)}`} />
        <MetricCard
          label="Largest Win"
          value={`$${data.largest_win.toFixed(2)}`}
          color="text-green-400"
        />
        <MetricCard
          label="Largest Loss"
          value={`$${data.largest_loss.toFixed(2)}`}
          color="text-red-400"
        />
      </div>

      {/* P&L Curve */}
      {pnlCurve.length > 1 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Cumulative P&L</h2>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={pnlCurve}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="trade" stroke="#6b7280" fontSize={12} />
              <YAxis stroke="#6b7280" fontSize={12} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1f2937',
                  border: '1px solid #374151',
                  borderRadius: '8px',
                  color: '#f3f4f6',
                }}
              />
              <Line
                type="monotone"
                dataKey="cumPnl"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
                name="Cumulative P&L"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

function MetricCard({
  label,
  value,
  color = 'text-white',
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-xl font-bold mt-1 ${color}`}>{value}</p>
    </div>
  )
}
