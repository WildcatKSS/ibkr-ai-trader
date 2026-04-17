/**
 * API client for the IBKR AI Trader backend.
 *
 * All requests include the JWT token from sessionStorage.
 * On 401, the token is cleared and the user is redirected to login.
 */

const BASE = '';

function getToken(): string | null {
  return sessionStorage.getItem('token');
}

export function setToken(token: string): void {
  sessionStorage.setItem('token', token);
}

export function clearToken(): void {
  sessionStorage.removeItem('token');
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    window.location.hash = '#/login';
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

// Auth
export async function login(password: string): Promise<string> {
  const data = await request<{ access_token: string }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
  setToken(data.access_token);
  return data.access_token;
}

// Status
export async function getStatus(): Promise<{
  trading_mode: string;
  market_open: boolean;
  trading_day: boolean;
  timestamp: string;
}> {
  return request('/api/status');
}

// Settings
export async function getSettings(): Promise<Record<string, string>> {
  return request('/api/settings');
}

export async function updateSetting(key: string, value: string): Promise<void> {
  await request(`/api/settings/${key}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  });
}

// Trades
export interface Trade {
  id: number;
  symbol: string;
  action: string;
  trading_mode: string;
  status: string;
  shares: number;
  entry_price: number;
  target_price: number;
  stop_price: number;
  fill_price: number | null;
  exit_price: number | null;
  pnl: number | null;
  ibkr_order_id: number | null;
  ml_label: string;
  ml_probability: number;
  confirmed_15min: boolean;
  explanation: string | null;
  created_at: string | null;
  filled_at: string | null;
  closed_at: string | null;
}

export async function getTrades(params?: {
  symbol?: string;
  status_filter?: string;
  limit?: number;
  offset?: number;
}): Promise<{ total: number; trades: Trade[]; limit: number; offset: number }> {
  const qs = new URLSearchParams();
  if (params?.symbol) qs.set('symbol', params.symbol);
  if (params?.status_filter) qs.set('status_filter', params.status_filter);
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.offset) qs.set('offset', String(params.offset));
  const q = qs.toString();
  return request(`/api/trades${q ? `?${q}` : ''}`);
}

export async function getOpenTrades(): Promise<Trade[]> {
  return request('/api/trades/open');
}

// Performance
export interface PerformanceData {
  period: string;
  trade_count: number;
  total_pnl: number;
  win_rate: number;
  avg_pnl: number;
  largest_win: number;
  largest_loss: number;
  profit_factor: number;
  timestamp: string;
}

export async function getPerformance(period = 'all'): Promise<PerformanceData> {
  return request(`/api/performance?period=${period}`);
}

// Portfolio
export interface PortfolioData {
  open_positions: {
    symbol: string;
    action: string;
    shares: number;
    entry_price: number;
    fill_price: number | null;
    target_price: number;
    stop_price: number;
    status: string;
  }[];
  position_count: number;
  daily_pnl: number;
  daily_trades: number;
  timestamp: string;
}

export async function getPortfolio(): Promise<PortfolioData> {
  return request('/api/portfolio');
}

// Backtesting
export interface BacktestParams {
  symbol: string;
  initial_capital?: number;
  position_size_pct?: number;
  stop_loss_atr?: number;
  take_profit_atr?: number;
  ml_min_probability?: number;
}

export interface BacktestResult {
  symbol: string;
  initial_capital: number;
  final_equity: number;
  trade_count: number;
  metrics: Record<string, number>;
  parameters: Record<string, number>;
  trades: {
    entry_time: string;
    exit_time: string | null;
    action: string;
    entry_price: number;
    exit_price: number | null;
    shares: number;
    pnl: number | null;
    exit_reason: string;
  }[];
  equity_curve: number[];
}

export async function runBacktest(params: BacktestParams): Promise<BacktestResult> {
  return request('/api/backtesting/run', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// Logs
export interface LogEntry {
  id: number;
  timestamp: string;
  level: string;
  category: string;
  module: string;
  message: string;
  extra: Record<string, unknown> | null;
}

export async function getLogs(params?: {
  category?: string;
  level?: string;
  limit?: number;
}): Promise<LogEntry[]> {
  const qs = new URLSearchParams();
  if (params?.category) qs.set('category', params.category);
  if (params?.level) qs.set('level', params.level);
  if (params?.limit) qs.set('limit', String(params.limit));
  const q = qs.toString();
  return request(`/api/logs${q ? `?${q}` : ''}`);
}

// Bot service control
export interface ServiceStatus {
  active: boolean;
  state: string;
  unit: string;
}

export async function getServiceStatus(): Promise<ServiceStatus> {
  return request('/api/bot/service-status');
}

export async function startService(): Promise<{ ok: boolean; action: string }> {
  return request('/api/bot/start', { method: 'POST', body: JSON.stringify({}) });
}

export async function stopService(): Promise<{ ok: boolean; action: string }> {
  return request('/api/bot/stop', {
    method: 'POST',
    body: JSON.stringify({ confirm: 'STOP' }),
  });
}

export async function restartService(): Promise<{ ok: boolean; action: string }> {
  return request('/api/bot/restart', {
    method: 'POST',
    body: JSON.stringify({ confirm: 'RESTART' }),
  });
}

// Log streaming (SSE) — requires a short-lived stream token since EventSource
// cannot send Authorization headers.
export async function requestStreamToken(): Promise<string> {
  const data = await request<{ stream_token: string }>(
    '/api/logs/stream-token',
    { method: 'POST', body: JSON.stringify({}) },
  );
  return data.stream_token;
}

// ML model management
export interface MlVersion {
  version: string;
  trained_at: string;
  n_samples: number;
  metrics: Record<string, number>;
  is_current?: boolean;
}

export interface MlJob {
  id: number;
  job_type: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  version: string | null;
  metrics: Record<string, number> | null;
  error: string | null;
  params: Record<string, unknown> | null;
}

export async function getMlVersions(): Promise<MlVersion[]> {
  return request('/api/ml/versions');
}

export async function getMlCurrent(): Promise<{ version: string | null }> {
  return request('/api/ml/current');
}

export async function startMlRetrain(params: {
  forward_bars: number;
  long_threshold_pct: number;
  short_threshold_pct: number;
  symbol?: string;
  n_bars?: number;
}): Promise<{ job_id: number }> {
  return request('/api/ml/retrain', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function startMlRollback(version: string): Promise<{ job_id: number }> {
  return request('/api/ml/rollback', {
    method: 'POST',
    body: JSON.stringify({ version }),
  });
}

export async function getMlJob(id: number): Promise<MlJob> {
  return request(`/api/ml/jobs/${id}`);
}

export async function getMlJobs(): Promise<MlJob[]> {
  return request('/api/ml/jobs');
}

// Universe approval
export interface UniverseCandidate {
  symbol: string;
  score: number;
  analysis: string;
  passes_all_core: boolean;
  near_resistance: boolean;
  has_momentum: boolean;
  pullback_above_ema9: boolean;
}

export interface UniverseSelection {
  id: number;
  scan_date: string;
  candidates: UniverseCandidate[];
  selected_symbol: string | null;
  status: string;
  reasoning: string | null;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
}

export async function getPendingSelection(): Promise<UniverseSelection | null> {
  return request('/api/universe/pending');
}

export async function getSelectionHistory(): Promise<UniverseSelection[]> {
  return request('/api/universe/history');
}

export async function approveSelection(selectionId: number, symbol: string): Promise<UniverseSelection> {
  return request('/api/universe/approve', {
    method: 'POST',
    body: JSON.stringify({ selection_id: selectionId, symbol }),
  });
}

export async function rejectSelection(selectionId: number, reason: string): Promise<UniverseSelection> {
  return request('/api/universe/reject', {
    method: 'POST',
    body: JSON.stringify({ selection_id: selectionId, reason }),
  });
}
