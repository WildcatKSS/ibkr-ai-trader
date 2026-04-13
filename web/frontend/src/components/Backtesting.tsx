import { useState } from 'react'
import { runBacktest, type BacktestResult, type BacktestParams } from '../api'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'

export default function Backtesting() {
  const [params, setParams] = useState<BacktestParams>({
    symbol: 'SPY',
    initial_capital: 100000,
    position_size_pct: 2,
    stop_loss_atr: 1.0,
    take_profit_atr: 2.0,
    ml_min_probability: 0.55,
  })
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleRun() {
    setError('')
    setResult(null)
    setLoading(true)
    try {
      const res = await runBacktest(params)
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  const equityCurve = result
    ? result.equity_curve.map((v, i) => ({ bar: i, equity: v }))
    : []

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Backtesting</h1>

      {/* Parameters form */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Parameters</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Field
            label="Symbol"
            value={params.symbol}
            onChange={(v) => setParams({ ...params, symbol: v.toUpperCase() })}
          />
          <Field
            label="Initial Capital"
            value={String(params.initial_capital)}
            onChange={(v) => setParams({ ...params, initial_capital: Number(v) || 0 })}
            type="number"
          />
          <Field
            label="Position Size %"
            value={String(params.position_size_pct)}
            onChange={(v) => setParams({ ...params, position_size_pct: Number(v) || 0 })}
            type="number"
          />
          <Field
            label="Stop Loss (ATR)"
            value={String(params.stop_loss_atr)}
            onChange={(v) => setParams({ ...params, stop_loss_atr: Number(v) || 0 })}
            type="number"
          />
          <Field
            label="Take Profit (ATR)"
            value={String(params.take_profit_atr)}
            onChange={(v) => setParams({ ...params, take_profit_atr: Number(v) || 0 })}
            type="number"
          />
          <Field
            label="Min ML Probability"
            value={String(params.ml_min_probability)}
            onChange={(v) => setParams({ ...params, ml_min_probability: Number(v) || 0 })}
            type="number"
          />
        </div>
        <div className="mt-4">
          <button
            onClick={handleRun}
            disabled={loading || !params.symbol}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-white rounded font-medium transition-colors"
          >
            {loading ? 'Running...' : 'Run Backtest'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-400">
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Summary */}
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold text-white mb-4">
              Results: {result.symbol}
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <ResultStat
                label="Final Equity"
                value={`$${result.final_equity.toLocaleString()}`}
              />
              <ResultStat label="Trade Count" value={String(result.trade_count)} />
              <ResultStat
                label="Total Return"
                value={`${(result.metrics.total_return_pct ?? 0).toFixed(2)}%`}
                color={
                  (result.metrics.total_return_pct ?? 0) >= 0
                    ? 'text-green-400'
                    : 'text-red-400'
                }
              />
              <ResultStat
                label="Win Rate"
                value={`${(result.metrics.win_rate ?? 0).toFixed(1)}%`}
              />
              <ResultStat
                label="Sharpe Ratio"
                value={(result.metrics.sharpe_ratio ?? 0).toFixed(2)}
              />
              <ResultStat
                label="Max Drawdown"
                value={`${(result.metrics.max_drawdown_pct ?? 0).toFixed(2)}%`}
                color="text-red-400"
              />
              <ResultStat
                label="Profit Factor"
                value={(result.metrics.profit_factor ?? 0).toFixed(2)}
              />
              <ResultStat
                label="Avg Trade P&L"
                value={`$${(result.metrics.avg_trade_pnl ?? 0).toFixed(2)}`}
              />
            </div>
          </div>

          {/* Equity curve */}
          {equityCurve.length > 1 && (
            <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
              <h2 className="text-lg font-semibold text-white mb-4">Equity Curve</h2>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={equityCurve}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis dataKey="bar" stroke="#6b7280" fontSize={12} />
                  <YAxis stroke="#6b7280" fontSize={12} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#1f2937',
                      border: '1px solid #374151',
                      borderRadius: '8px',
                      color: '#f3f4f6',
                    }}
                    formatter={(value) => [`$${Number(value).toLocaleString()}`, 'Equity']}
                  />
                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Trade log */}
          {result.trades.length > 0 && (
            <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-x-auto">
              <div className="px-6 py-4 border-b border-gray-800">
                <h2 className="text-lg font-semibold text-white">
                  Trade Log ({result.trades.length})
                </h2>
              </div>
              <table className="w-full text-sm text-left">
                <thead className="text-gray-400 border-b border-gray-800">
                  <tr>
                    <th className="px-4 py-2">Entry</th>
                    <th className="px-4 py-2">Exit</th>
                    <th className="px-4 py-2">Action</th>
                    <th className="px-4 py-2">Shares</th>
                    <th className="px-4 py-2">Entry $</th>
                    <th className="px-4 py-2">Exit $</th>
                    <th className="px-4 py-2">P&L</th>
                    <th className="px-4 py-2">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => (
                    <tr key={i} className="border-b border-gray-800/50">
                      <td className="px-4 py-2 text-gray-400 whitespace-nowrap text-xs">
                        {t.entry_time}
                      </td>
                      <td className="px-4 py-2 text-gray-400 whitespace-nowrap text-xs">
                        {t.exit_time || '--'}
                      </td>
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
                      <td className="px-4 py-2 text-gray-400">{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
}) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
      />
    </div>
  )
}

function ResultStat({
  label,
  value,
  color = 'text-white',
}: {
  label: string
  value: string
  color?: string
}) {
  return (
    <div>
      <p className="text-gray-500 text-xs">{label}</p>
      <p className={`font-bold text-lg ${color}`}>{value}</p>
    </div>
  )
}
