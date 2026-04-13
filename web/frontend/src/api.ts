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
