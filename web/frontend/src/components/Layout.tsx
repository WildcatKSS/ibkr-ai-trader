import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { clearToken, getSettings } from '../api'

const baseLinks = [
  { to: '/', label: 'Dashboard' },
  { to: '/trades', label: 'Trades' },
  { to: '/performance', label: 'Performance' },
  { to: '/backtesting', label: 'Backtesting' },
  { to: '/logs', label: 'Logs' },
  { to: '/ml', label: 'ML Model' },
  { to: '/settings', label: 'Settings' },
]

const UNIVERSE_LINK = { to: '/universe', label: 'Universe' }

export default function Layout() {
  const navigate = useNavigate()
  const [links, setLinks] = useState(baseLinks)

  useEffect(() => {
    // Show the Universe nav item only when approval mode is enabled.
    getSettings()
      .then((s) => {
        if (s.UNIVERSE_APPROVAL_MODE === 'approval') {
          setLinks([
            ...baseLinks.slice(0, -1),
            UNIVERSE_LINK,
            baseLinks[baseLinks.length - 1],
          ])
        } else {
          setLinks(baseLinks)
        }
      })
      .catch(() => { /* Fallback to base links on error */ })
  }, [])

  function handleLogout() {
    clearToken()
    navigate('/login')
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <nav className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <span className="text-lg font-bold text-white tracking-tight">
            IBKR AI Trader
          </span>
          <div className="flex gap-1">
            {links.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded text-sm transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-400 hover:text-white hover:bg-gray-800'
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="text-sm text-gray-400 hover:text-white transition-colors"
        >
          Logout
        </button>
      </nav>
      <main className="max-w-7xl mx-auto px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}
