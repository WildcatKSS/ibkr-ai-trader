import { useCallback, useEffect, useState } from 'react'
import {
  MlJob,
  MlVersion,
  getMlCurrent,
  getMlJob,
  getMlJobs,
  getMlVersions,
  startMlRetrain,
  startMlRollback,
} from '../api'

/**
 * MLModel — LightGBM model management screen.
 *
 * Replaces the CLI commands:
 *   python -m bot.ml.trainer --retrain --data ...
 *   python -m bot.ml.versioning --rollback <version>
 *
 * Users can kick off a retrain on a symbol (historical 5-min bars pulled
 * from IBKR), roll back to any previously registered version, and follow
 * the running job.  Jobs run on a background thread on the server — this
 * page polls /api/ml/jobs/{id} every 3 s until the job reaches a terminal
 * state.
 */
export default function MLModel() {
  const [current, setCurrent] = useState<string | null>(null)
  const [versions, setVersions] = useState<MlVersion[]>([])
  const [jobs, setJobs] = useState<MlJob[]>([])
  const [activeJob, setActiveJob] = useState<MlJob | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  // Retrain form
  const [symbol, setSymbol] = useState('SPY')
  const [nBars, setNBars] = useState(5000)
  const [forwardBars, setForwardBars] = useState(6)
  const [longThreshold, setLongThreshold] = useState(0.3)
  const [shortThreshold, setShortThreshold] = useState(0.3)

  const refresh = useCallback(async () => {
    try {
      const [cur, vers, js] = await Promise.all([
        getMlCurrent(),
        getMlVersions(),
        getMlJobs(),
      ])
      setCurrent(cur.version)
      setVersions(vers)
      setJobs(js)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Poll the active job while running.
  useEffect(() => {
    if (!activeJob || activeJob.status === 'done' || activeJob.status === 'failed') return
    const interval = window.setInterval(async () => {
      try {
        const latest = await getMlJob(activeJob.id)
        setActiveJob(latest)
        if (latest.status === 'done' || latest.status === 'failed') {
          refresh()
        }
      } catch {
        // stay silent; the next poll will retry
      }
    }, 3000)
    return () => window.clearInterval(interval)
  }, [activeJob, refresh])

  async function handleRetrain() {
    if (!symbol.trim()) {
      setError('Symbol is required')
      return
    }
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const { job_id } = await startMlRetrain({
        symbol: symbol.trim().toUpperCase(),
        n_bars: nBars,
        forward_bars: forwardBars,
        long_threshold_pct: longThreshold,
        short_threshold_pct: shortThreshold,
      })
      const job = await getMlJob(job_id)
      setActiveJob(job)
      setMessage(`Retrain job #${job_id} started for ${symbol.toUpperCase()}`)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleRollback(version: string) {
    if (version === current) return
    if (!confirm(`Roll back active model to ${version}?`)) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const { job_id } = await startMlRollback(version)
      const job = await getMlJob(job_id)
      setActiveJob(job)
      setMessage(`Rolled back to ${version}`)
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">ML Model Management</h1>

      {error && <p className="text-sm text-red-400">{error}</p>}
      {message && <p className="text-sm text-green-400">{message}</p>}

      {/* Current version card */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h2 className="text-lg font-semibold text-white mb-2">Active Model</h2>
        <p className="text-gray-300 font-mono">
          {current || <span className="text-gray-500 italic">none — train a new model to start</span>}
        </p>
      </section>

      {/* Retrain form */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-4">
        <h2 className="text-lg font-semibold text-white">Retrain</h2>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <LabeledInput label="Symbol" value={symbol} onChange={setSymbol} />
          <LabeledNumber label="Bars (500–20 000)" value={nBars} onChange={setNBars} min={500} max={20000} />
          <LabeledNumber label="Forward bars" value={forwardBars} onChange={setForwardBars} min={1} max={50} />
          <LabeledNumber label="Long % ≥" value={longThreshold} onChange={setLongThreshold} step={0.05} />
          <LabeledNumber label="Short % ≥" value={shortThreshold} onChange={setShortThreshold} step={0.05} />
        </div>
        <button
          type="button"
          onClick={handleRetrain}
          disabled={busy}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white text-sm rounded"
        >
          {busy ? 'Starting…' : 'Start Retrain'}
        </button>
        <p className="text-xs text-gray-500">
          Historical 5-minute bars are fetched from IBKR. TWS/Gateway must be running.
          Training runs on a background thread; safe to navigate away.
        </p>
      </section>

      {/* Active job progress */}
      {activeJob && (
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <h2 className="text-lg font-semibold text-white mb-2">Active Job #{activeJob.id}</h2>
          <JobDetail job={activeJob} />
        </section>
      )}

      {/* Version history */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h2 className="text-lg font-semibold text-white mb-3">Versions</h2>
        {versions.length === 0 ? (
          <p className="text-gray-500 italic">No versions yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-400 border-b border-gray-800">
                  <th className="py-2 pr-4">Version</th>
                  <th className="py-2 pr-4">Trained</th>
                  <th className="py-2 pr-4">Samples</th>
                  <th className="py-2 pr-4">Metrics</th>
                  <th className="py-2 pr-4"></th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version} className="border-b border-gray-800/60">
                    <td className="py-2 pr-4 font-mono text-gray-200">
                      {v.version}{' '}
                      {v.is_current && <span className="ml-2 text-xs bg-green-700 text-white px-2 py-0.5 rounded">current</span>}
                    </td>
                    <td className="py-2 pr-4 text-gray-400">{v.trained_at?.slice(0, 19)}</td>
                    <td className="py-2 pr-4 text-gray-400">{v.n_samples.toLocaleString()}</td>
                    <td className="py-2 pr-4 text-gray-400">
                      {Object.entries(v.metrics || {})
                        .map(([k, val]) => `${k}=${val}`)
                        .join('  ')}
                    </td>
                    <td className="py-2 pr-4 text-right">
                      <button
                        type="button"
                        disabled={busy || v.is_current}
                        onClick={() => handleRollback(v.version)}
                        className="text-sm text-blue-400 hover:text-blue-300 disabled:text-gray-600"
                      >
                        Roll back
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Recent jobs */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h2 className="text-lg font-semibold text-white mb-3">Recent Jobs</h2>
        {jobs.length === 0 ? (
          <p className="text-gray-500 italic">No jobs yet.</p>
        ) : (
          <div className="space-y-2 text-sm">
            {jobs.slice(0, 15).map((j) => (
              <button
                key={j.id}
                type="button"
                onClick={() => setActiveJob(j)}
                className="block w-full text-left bg-gray-800/60 hover:bg-gray-800 px-3 py-2 rounded"
              >
                <span className="text-gray-400">#{j.id}</span>{' '}
                <span className="text-gray-200">{j.job_type}</span>{' '}
                <span className={STATUS_COLORS[j.status] ?? 'text-gray-400'}>{j.status}</span>{' '}
                {j.version && <span className="text-gray-500 font-mono">{j.version}</span>}{' '}
                <span className="text-gray-500">{j.started_at?.slice(0, 19)}</span>
                {j.error && <span className="text-red-400 ml-2">— {j.error.slice(0, 60)}</span>}
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'text-yellow-400',
  running: 'text-blue-400',
  done: 'text-green-400',
  failed: 'text-red-400',
}

function JobDetail({ job }: { job: MlJob }) {
  return (
    <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
      <div>
        <dt className="text-gray-500">Type</dt>
        <dd className="text-gray-200">{job.job_type}</dd>
      </div>
      <div>
        <dt className="text-gray-500">Status</dt>
        <dd className={STATUS_COLORS[job.status] ?? 'text-gray-200'}>{job.status}</dd>
      </div>
      <div>
        <dt className="text-gray-500">Started</dt>
        <dd className="text-gray-400">{job.started_at?.slice(0, 19)}</dd>
      </div>
      <div>
        <dt className="text-gray-500">Finished</dt>
        <dd className="text-gray-400">{job.finished_at?.slice(0, 19) || '—'}</dd>
      </div>
      {job.version && (
        <div className="col-span-2">
          <dt className="text-gray-500">Version</dt>
          <dd className="font-mono text-gray-200">{job.version}</dd>
        </div>
      )}
      {job.metrics && Object.keys(job.metrics).length > 0 && (
        <div className="col-span-2 md:col-span-4">
          <dt className="text-gray-500">Metrics</dt>
          <dd className="text-gray-300 font-mono">
            {Object.entries(job.metrics).map(([k, v]) => `${k}=${v}`).join('  ')}
          </dd>
        </div>
      )}
      {job.error && (
        <div className="col-span-2 md:col-span-4">
          <dt className="text-gray-500">Error</dt>
          <dd className="text-red-400 whitespace-pre-wrap">{job.error}</dd>
        </div>
      )}
    </dl>
  )
}

function LabeledInput({
  label, value, onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <label className="text-sm text-gray-300 block">
      <span className="block mb-1 text-gray-400">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5"
      />
    </label>
  )
}

function LabeledNumber({
  label, value, onChange, min, max, step,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
  step?: number
}) {
  return (
    <label className="text-sm text-gray-300 block">
      <span className="block mb-1 text-gray-400">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step ?? 1}
        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5"
      />
    </label>
  )
}
