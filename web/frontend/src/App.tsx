import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { isAuthenticated } from './api'
import Layout from './components/Layout'
import Login from './components/Login'
import Dashboard from './components/Dashboard'
import TradeHistory from './components/TradeHistory'
import Performance from './components/Performance'
import Settings from './components/Settings'
import Backtesting from './components/Backtesting'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="trades" element={<TradeHistory />} />
          <Route path="performance" element={<Performance />} />
          <Route path="settings" element={<Settings />} />
          <Route path="backtesting" element={<Backtesting />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  )
}
