import { useEffect, useState } from 'react'
import { getSettings, updateSetting } from '../api'

const IMPORTANT_KEYS = [
  'TRADING_MODE',
  'EOD_CLOSE_MINUTES',
  'UNIVERSE_POOL',
  'UNIVERSE_MAX_SYMBOLS',
  'UNIVERSE_APPROVAL_MODE',
  'ML_MIN_PROBABILITY',
  'ML_FORWARD_BARS',
  'ML_LONG_THRESHOLD_PCT',
  'ML_SHORT_THRESHOLD_PCT',
  'POSITION_SIZE_METHOD',
  'POSITION_SIZE_PCT',
  'POSITION_SIZE_FIXED',
  'MAX_DAILY_LOSS',
  'MAX_CONSECUTIVE_LOSSES',
  'DRYRUN_WATCHLIST',
]

export default function Settings() {
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [editKey, setEditKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((e) => setError(e.message))
  }, [])

  function startEdit(key: string) {
    setEditKey(key)
    setEditValue(settings[key] || '')
    setMessage('')
  }

  async function handleSave() {
    if (!editKey) return
    setSaving(true)
    setMessage('')
    try {
      await updateSetting(editKey, editValue)
      setSettings((prev) => ({ ...prev, [editKey]: editValue }))
      setEditKey(null)
      setMessage(`${editKey} updated`)
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (error) return <p className="text-red-400">{error}</p>

  const importantSettings = IMPORTANT_KEYS.filter((k) => k in settings)
  const otherSettings = Object.keys(settings)
    .filter((k) => !IMPORTANT_KEYS.includes(k))
    .sort()

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Settings</h1>

      {message && (
        <p className={`text-sm ${message.includes('updated') ? 'text-green-400' : 'text-red-400'}`}>
          {message}
        </p>
      )}

      {/* Important settings */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg">
        <div className="px-4 py-3 border-b border-gray-800">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
            Key Settings
          </h2>
        </div>
        <div className="divide-y divide-gray-800">
          {importantSettings.map((key) => (
            <SettingRow
              key={key}
              settingKey={key}
              value={settings[key]}
              isEditing={editKey === key}
              editValue={editValue}
              saving={saving}
              onEdit={() => startEdit(key)}
              onChangeEdit={setEditValue}
              onSave={handleSave}
              onCancel={() => setEditKey(null)}
            />
          ))}
        </div>
      </div>

      {/* Other settings */}
      {otherSettings.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg">
          <div className="px-4 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
              All Other Settings
            </h2>
          </div>
          <div className="divide-y divide-gray-800">
            {otherSettings.map((key) => (
              <SettingRow
                key={key}
                settingKey={key}
                value={settings[key]}
                isEditing={editKey === key}
                editValue={editValue}
                saving={saving}
                onEdit={() => startEdit(key)}
                onChangeEdit={setEditValue}
                onSave={handleSave}
                onCancel={() => setEditKey(null)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function SettingRow({
  settingKey,
  value,
  isEditing,
  editValue,
  saving,
  onEdit,
  onChangeEdit,
  onSave,
  onCancel,
}: {
  settingKey: string
  value: string
  isEditing: boolean
  editValue: string
  saving: boolean
  onEdit: () => void
  onChangeEdit: (v: string) => void
  onSave: () => void
  onCancel: () => void
}) {
  return (
    <div className="px-4 py-3 flex items-center justify-between gap-4">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-mono text-gray-300">{settingKey}</p>
        {isEditing ? (
          <div className="flex items-center gap-2 mt-1">
            <input
              value={editValue}
              onChange={(e) => onChangeEdit(e.target.value)}
              className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm text-white flex-1 focus:outline-none focus:border-blue-500"
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSave()
                if (e.key === 'Escape') onCancel()
              }}
              autoFocus
            />
            <button
              onClick={onSave}
              disabled={saving}
              className="px-2 py-1 text-xs bg-blue-600 rounded hover:bg-blue-700 text-white"
            >
              Save
            </button>
            <button
              onClick={onCancel}
              className="px-2 py-1 text-xs bg-gray-700 rounded hover:bg-gray-600 text-gray-300"
            >
              Cancel
            </button>
          </div>
        ) : (
          <p className="text-sm text-gray-500 truncate">{value}</p>
        )}
      </div>
      {!isEditing && (
        <button
          onClick={onEdit}
          className="text-xs text-blue-400 hover:text-blue-300 shrink-0"
        >
          Edit
        </button>
      )}
    </div>
  )
}
