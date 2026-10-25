// Thin typed client for the FastAPI backend. Base URL and API key are configurable at runtime.
const DEFAULT_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

function read(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

// In production builds the backend URL is fixed at build time (VITE_API_BASE_URL) and the
// Settings page is hidden, so visitors cannot see or change it.
export const IS_PROD = import.meta.env.PROD && import.meta.env.VITE_SHOW_SETTINGS !== "true";

export const config = {
  get baseUrl() { return IS_PROD ? DEFAULT_BASE : read("qc.baseUrl", DEFAULT_BASE); },
  get apiKey() { return read("qc.apiKey", ""); },
  save(baseUrl: string, apiKey: string) {
    try { localStorage.setItem("qc.baseUrl", baseUrl); localStorage.setItem("qc.apiKey", apiKey); } catch { /* ignore */ }
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

// Random per-browser id: in the public demo it keeps each visitor's history separate.
function visitorId(): string {
  try {
    let id = localStorage.getItem("qc.visitor");
    if (!id) { id = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("qc.visitor", id); }
    return id;
  } catch { return "anonymous-visitor"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${config.baseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(config.apiKey ? { "X-API-Key": config.apiKey } : {}), "X-Visitor-Id": visitorId(), ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${config.baseUrl}. Is the API running (python main.py)?`);
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const body = await res.json(); msg = body.detail ?? msg; if (body.suggestions?.length) msg += ` (did you mean ${body.suggestions.join(", ")}?)`; } catch { /* ignore */ }
    throw new ApiError(res.status, `${res.status}: ${msg}`);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const q = (p: Record<string, string | number | undefined>) => {
  const s = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => { if (v !== undefined && v !== "") s.set(k, String(v)); });
  const str = s.toString();
  return str ? `?${str}` : "";
};

export interface CityRow { city: string; stores: number; total_orders: number; breached_count: number; breach_rate: number | null; weighted_avg_or2a: number | null; problem_hours: number; pileup_hours: number }
export interface StoreRow { store: string; city?: string; total_orders: number; breached_count: number; breach_rate: number | null; weighted_avg_or2a: number | null; problem_hours: number }
export interface HourRow { hour: number; total_orders: number; breached_count: number; breach_rate: number | null; weighted_avg_or2a: number | null; pileup_stores: number; order_projection: number | null }
export interface Overview { stores: number; total_orders: number; breached_count: number; breach_rate: number | null; weighted_avg_or2a: number | null; problem_hours: number; store_hours: number; pileup_hours: number; cancelled_orders: number; date: string; city: string | null }
export interface DateInfo { date: string; synthetic: boolean }
export interface Meta { cities: { name: string; normalized_name: string; stores: number }[]; dates: string[]; date_info: DateInfo[]; stores: { code: string; city: string }[] }
export interface TrendRow { date: string; synthetic: boolean; total_orders: number; breached_count: number; breach_rate: number | null; weighted_avg_or2a: number | null; problem_hours: number }
export interface Citation { id: string; source_path: string; title: string; heading: string | null; doc_type: string; is_sample: boolean; excerpt: string }
export interface Factor { factor: string; hours: number[]; breached_orders_in_flagged_hours: number; share_of_breached_orders: number | null; evidence_ids: string[]; citation_ids: string[]; assessment: string }
export interface Report {
  investigation_id: string; question: string; intent: string; intent_source: string; status: string;
  scope: Record<string, unknown>; answer_markdown: string; narrative: string | null;
  observed_metrics: { name: string; value: number | string | null; unit: string | null; evidence_id: string }[];
  contributing_factors: Factor[]; evidence: { id: string; kind: string; summary: string; source: string }[];
  citations: Citation[]; missing_data: string[]; confidence: { level: string; limitations: string[] };
  next_steps: string[]; corrective_actions: { action: string; addresses: string; citation_ids: string[]; source_note: string }[];
  validation: { name: string; passed: boolean; detail: string }[];
  llm: { used: boolean; model: string | null; note: string | null }; timings_ms: Record<string, number>; disclaimer: string;
}
export interface HistoryRow { id: string; created_at: string; question: string; intent: string; status: string; latency_ms: number; thread_id: string | null }

export const api = {
  health: () => request<{ status: string; database: boolean; auth_enabled: boolean; ollama: { reachable?: boolean; llm_installed?: boolean; embedding_installed?: boolean } }>("/health"),
  meta: () => request<Meta>("/api/v1/analytics/meta"),
  overview: (date?: string, city?: string) => request<{ overview: Overview; by_hour: HourRow[] }>(`/api/v1/analytics/overview${q({ date, city })}`),
  cities: (date?: string) => request<CityRow[]>(`/api/v1/analytics/cities${q({ date })}`),
  cityStores: (city: string, date?: string) => request<{ city: string; date: string; stores: StoreRow[] }>(`/api/v1/analytics/cities/${encodeURIComponent(city)}/stores${q({ date })}`),
  dailyTrend: (city?: string, store?: string) => request<TrendRow[]>(`/api/v1/analytics/daily-trend${q({ city, store })}`),
  breachByHour: (date?: string, city?: string, store?: string) => request<HourRow[]>(`/api/v1/analytics/breach-by-hour${q({ date, city, store })}`),
  topStores: (date?: string, city?: string, limit = 10, min_orders = 50) => request<StoreRow[]>(`/api/v1/analytics/top-stores${q({ date, city, limit, min_orders })}`),
  compareStores: (stores: string[], date?: string) => request<(StoreRow & { weighted_breach_rate: number | null; factor_counts: Record<string, number> })[]>(`/api/v1/analytics/stores/compare${q({ stores: stores.join(","), date })}`),
  investigate: (question: string, thread_id?: string) => request<Report>("/api/v1/investigations", { method: "POST", body: JSON.stringify({ question, thread_id }) }),
  history: () => request<HistoryRow[]>("/api/v1/investigations"),
  investigation: (id: string) => request<{ report: Report }>(`/api/v1/investigations/${id}`),
  reportMarkdown: (id: string) => request<string>(`/api/v1/investigations/${id}/report.md`),
};
