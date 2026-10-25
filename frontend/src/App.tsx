import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import Markdown from "./Markdown";
import { ApiError, IS_PROD, api, config, type CityRow, type HistoryRow, type HourRow, type Meta, type Overview, type Report, type StoreRow, type TrendRow } from "./api";

const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null | undefined, d = 1) => (v == null ? "n/a" : v.toFixed(d));
const C = { series: "#3b6fd8", accent: "#d8573b", grid: "var(--grid)" };

function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let live = true;
    setLoading(true); setError(null); setStatus(null);
    fn().then((d) => live && setData(d))
      .catch((e: Error) => { if (live) { setError(e.message); setStatus(e instanceof ApiError ? e.status : null); } })
      .finally(() => live && setLoading(false));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, status };
}

function Status({ error, loading }: { error: string | null; loading: boolean; status?: number | null }) {
  if (error) return <div className="error">{error}</div>;
  if (loading) return <div className="muted">Loading…</div>;
  return null;
}

function Kpi({ label, value }: { label: string; value: string }) {
  return <div className="kpi"><div className="kpi-v">{value}</div><div className="kpi-l">{label}</div></div>;
}

function Settings({ onSaved }: { onSaved: () => void }) {
  const [base, setBase] = useState(config.baseUrl);
  const [key, setKey] = useState(config.apiKey);
  const [health, setHealth] = useState<Awaited<ReturnType<typeof api.health>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const check = () => { setError(null); api.health().then(setHealth).catch((e) => { setHealth(null); setError(e.message); }); };
  useEffect(check, []);
  const save = () => { config.save(base.replace(/\/$/, ""), key.trim()); check(); onSaved(); };
  const ok = (v: boolean | undefined) => (v ? "✅" : "⚠️");
  return (
    <div className="card settings">
      <h3>Connection</h3>
      <label>Backend URL <input value={base} onChange={(e) => setBase(e.target.value)} /></label>
      {health?.auth_enabled === false ? (
        <p className="muted small">No API key needed — the backend is running in local mode (authentication off).</p>
      ) : (
        <label>API key <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="qc_…" /></label>
      )}
      <div className="row">
        <button onClick={save}>Save</button>
        <button className="secondary" onClick={check}>Test connection</button>
      </div>
      {error && <div className="error">{error}</div>}
      {health && (
        <ul className="status-list">
          <li>{ok(true)} Backend reachable</li>
          <li>{ok(health.database)} Database {health.database ? "connected" : "not reachable"}</li>
          <li>{ok(health.ollama.reachable && health.ollama.llm_installed)} Local AI model {health.ollama.reachable && health.ollama.llm_installed ? "available" : "not running (answers still work, without AI extras)"}</li>
          <li>{ok(health.embeddings?.available)} Document search {health.embeddings?.available ? "semantic + keyword" : "keyword only"}</li>
          <li>{health.auth_enabled ? "🔒 Access key required" : "🔓 Open access (local only)"}</li>
        </ul>
      )}
    </div>
  );
}

function HourChart({ rows }: { rows: HourRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={rows.map((r) => ({ ...r, breach_pct: r.breach_rate == null ? null : +(r.breach_rate * 100).toFixed(1) }))}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="hour" tickLine={false} />
        <YAxis yAxisId="o" tickLine={false} axisLine={false} />
        <YAxis yAxisId="p" orientation="right" unit="%" tickLine={false} axisLine={false} />
        <Tooltip />
        <Legend />
        <Bar yAxisId="o" dataKey="total_orders" name="Orders" fill={C.series} radius={[3, 3, 0, 0]} />
        <Line yAxisId="p" dataKey="breach_pct" name="Late orders %" stroke={C.accent} strokeWidth={2} dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function TrendChart({ rows, selected }: { rows: TrendRow[]; selected: string }) {
  const data = rows.map((r) => ({ ...r, label: r.synthetic ? `${r.date.slice(5)}*` : r.date.slice(5), breach_pct: r.breach_rate == null ? null : +(r.breach_rate * 100).toFixed(1) }));
  return (
    <>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="label" tickLine={false} />
          <YAxis yAxisId="o" tickLine={false} axisLine={false} />
          <YAxis yAxisId="p" orientation="right" unit="%" tickLine={false} axisLine={false} />
          <Tooltip />
          <Legend />
          <Bar yAxisId="o" dataKey="total_orders" name="Orders" radius={[3, 3, 0, 0]}
            shape={(p: any) => <rect x={p.x} y={p.y} width={p.width} height={p.height} rx={3}
              fill={C.series} fillOpacity={p.payload.synthetic ? 0.35 : 1} stroke={p.payload.date === selected ? "var(--fg)" : "none"} />} />
          <Line yAxisId="p" dataKey="breach_pct" name="Late orders %" stroke={C.accent} strokeWidth={2} dot={{ r: 3 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="small muted">Faded bars (*) are simulated demo days.</div>
    </>
  );
}

function OverviewView({ meta, date }: { meta: Meta; date: string }) {
  const [city, setCity] = useState("");
  const ov = useAsync(() => api.overview(date, city || undefined), [date, city]);
  const top = useAsync(() => api.topStores(date, city || undefined, 10), [date, city]);
  const trend = useAsync(() => (meta.dates.length > 1 ? api.dailyTrend(city || undefined) : Promise.resolve([] as TrendRow[])), [city, meta.dates.length]);
  const o: Overview | undefined = ov.data?.overview;
  return (
    <>
      <div className="row">
        <select value={city} onChange={(e) => setCity(e.target.value)}>
          <option value="">All cities</option>
          {meta.cities.map((c) => <option key={c.name} value={c.normalized_name}>{c.name}</option>)}
        </select>
      </div>
      <Status {...ov} />
      {o && (
        <div className="kpis">
          <Kpi label="Orders" value={o.total_orders.toLocaleString()} />
          <Kpi label="Late orders" value={pct(o.breach_rate)} />
          <Kpi label="Avg wait for a rider (min)" value={num(o.weighted_avg_or2a)} />
          <Kpi label="Store-hours with late orders" value={`${o.problem_hours.toLocaleString()} of ${o.store_hours.toLocaleString()}`} />
          <Kpi label="Stores" value={String(o.stores)} />
        </div>
      )}
      {trend.data && trend.data.length > 1 && <div className="card"><h3>Daily trend</h3><TrendChart rows={trend.data} selected={date} /></div>}
      {ov.data && <div className="card"><h3>Orders and late orders by hour of day — {date}</h3><HourChart rows={ov.data.by_hour} /></div>}
      <div className="card"><h3>Stores with the most late orders (at least 50 orders)</h3><Status {...top} />
        {top.data && <StoreTable rows={top.data} />}</div>
    </>
  );
}

function StoreTable({ rows }: { rows: StoreRow[] }) {
  return (
    <table><thead><tr><th>Store</th><th>City</th><th>Orders</th><th>Late orders</th><th>Avg wait for a rider (min)</th><th>Hours with late orders</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.store}><td>{r.store}</td><td>{r.city ?? ""}</td><td>{r.total_orders}</td><td>{pct(r.breach_rate)}</td><td>{num(r.weighted_avg_or2a)}</td><td>{r.problem_hours}</td></tr>)}</tbody>
    </table>
  );
}

function CompareView({ meta, date }: { meta: Meta; date: string }) {
  const cities = useAsync(() => api.cities(date), [date]);
  const [stores, setStores] = useState("STORE_101,STORE_102");
  const [cmp, setCmp] = useState<Awaited<ReturnType<typeof api.compareStores>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = () => api.compareStores(stores.split(",").map((s) => s.trim()).filter(Boolean), date).then(setCmp).catch((e) => setErr(e.message));
  useEffect(() => { run(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [date]);
  const rows: CityRow[] = cities.data ?? [];
  return (
    <>
      <div className="card"><h3>Late orders by city</h3><Status {...cities} />
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={rows.map((r) => ({ city: r.city, breach: r.breach_rate == null ? 0 : +(r.breach_rate * 100).toFixed(1) }))} layout="vertical" margin={{ left: 40 }}>
            <CartesianGrid stroke={C.grid} horizontal={false} />
            <XAxis type="number" unit="%" tickLine={false} />
            <YAxis type="category" dataKey="city" width={110} tickLine={false} />
            <Tooltip /><Bar dataKey="breach" name="Late orders %" fill={C.series} radius={[0, 3, 3, 0]} />
          </BarChart>
        </ResponsiveContainer>
        <table><thead><tr><th>City</th><th>Stores</th><th>Orders</th><th>Late orders</th><th>Avg wait for a rider (min)</th><th>Hours with late orders</th><th>Store-hours with pile-ups</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.city}><td>{r.city}</td><td>{r.stores}</td><td>{r.total_orders}</td><td>{pct(r.breach_rate)}</td><td>{num(r.weighted_avg_or2a)}</td><td>{r.problem_hours}</td><td>{r.pileup_hours}</td></tr>)}</tbody></table>
      </div>
      <div className="card"><h3>Store comparison</h3>
        <div className="row">
          <input list="stores" value={stores} onChange={(e) => setStores(e.target.value)} placeholder="STORE_101,STORE_102" />
          <datalist id="stores">{meta.stores.map((s) => <option key={s.code} value={s.code} />)}</datalist>
          <button onClick={run}>Compare</button>
        </div>
        {err && <div className="error">{err}</div>}
        {cmp && <table><thead><tr><th>Store</th><th>City</th><th>Orders</th><th>Late orders</th><th>Avg wait for a rider (min)</th><th>Hours with late orders</th><th>Warning signs (hours)</th></tr></thead>
          <tbody>{cmp.map((r) => <tr key={r.store}><td>{r.store}</td><td>{r.city}</td><td>{r.total_orders}</td><td>{pct(r.weighted_breach_rate)}</td><td>{num(r.weighted_avg_or2a)}</td><td>{r.problem_hours}</td>
            <td>{Object.entries(r.factor_counts).map(([k, v]) => `${k}: ${v}`).join(", ") || "none"}</td></tr>)}</tbody></table>}
      </div>
    </>
  );
}

function BreachView({ meta, date }: { meta: Meta; date: string }) {
  const [city, setCity] = useState("");
  const [store, setStore] = useState("");
  const storeOptions = useMemo(() => meta.stores.filter((s) => !city || s.city.toLowerCase().replace(/\s+/g, " ") === city), [meta, city]);
  const hours = useAsync(() => api.breachByHour(date, city || undefined, store || undefined), [date, city, store]);
  const stores = useAsync(() => (city ? api.cityStores(city, date).then((r) => r.stores) : api.topStores(date, undefined, 25, 0)), [date, city]);
  return (
    <>
      <div className="row">
        <select value={city} onChange={(e) => { setCity(e.target.value); setStore(""); }}>
          <option value="">All cities</option>
          {meta.cities.map((c) => <option key={c.name} value={c.normalized_name}>{c.name}</option>)}
        </select>
        <select value={store} onChange={(e) => setStore(e.target.value)}>
          <option value="">All stores</option>
          {storeOptions.map((s) => <option key={s.code} value={s.code}>{s.code}</option>)}
        </select>
      </div>
      <div className="card"><h3>Late orders by hour of day{store ? ` — ${store}` : ""}</h3><Status {...hours} />{hours.data && <HourChart rows={hours.data} />}</div>
      <div className="card"><h3>{city ? "Stores in this city (worst first)" : "25 stores with the most late orders"}</h3><Status {...stores} />{stores.data && <StoreTable rows={stores.data} />}</div>
    </>
  );
}

function ReportView({ r }: { r: Report }) {
  const download = async () => {
    const md = await api.reportMarkdown(r.investigation_id);
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url; a.download = `incident-${r.investigation_id}.md`; a.click(); URL.revokeObjectURL(url);
  };
  return (
    <div className="report">
      <div className="row meta">
        <span className="tag">{r.intent}</span><span className="tag">via {r.intent_source}</span>
        <span className={`tag conf-${r.confidence.level}`}>confidence: {r.confidence.level}</span>
        <span className="muted">{r.timings_ms.total ?? ""} ms</span>
        <button className="secondary" onClick={download}>Download report</button>
      </div>
      {r.narrative && <p className="narrative">{r.narrative}</p>}
      <Markdown text={r.answer_markdown} />
      {r.missing_data.length > 0 && <details><summary>Missing data ({r.missing_data.length})</summary><ul>{r.missing_data.map((m) => <li key={m}>{m}</li>)}</ul></details>}
      {r.confidence.limitations.length > 0 && <details><summary>Confidence limitations</summary><ul>{r.confidence.limitations.map((m) => <li key={m}>{m}</li>)}</ul></details>}
      {r.next_steps.length > 0 && <details><summary>Next investigative steps</summary><ul>{r.next_steps.map((m) => <li key={m}>{m}</li>)}</ul></details>}
      {r.corrective_actions.length > 0 && <details><summary>Suggested corrective actions</summary><ul>{r.corrective_actions.map((a) => <li key={a.action}>{a.action} <span className="muted">{a.citation_ids.join(", ")} — {a.source_note}</span></li>)}</ul></details>}
      {r.evidence.length > 0 && <details><summary>Evidence ({r.evidence.length})</summary><ul>{r.evidence.map((e) => <li key={e.id}><b>{e.id}</b> [{e.kind}] {e.summary}</li>)}</ul></details>}
      {r.citations.length > 0 && <details open><summary>Citations</summary><ul>{r.citations.map((c) => <li key={c.id}><b>[{c.id}]</b> {c.source_path} — {c.heading ?? c.title}{c.is_sample && <span className="tag sample">SAMPLE</span>}<div className="small muted">{c.excerpt}</div></li>)}</ul></details>}
      {r.validation.length > 0 && <details><summary>Validation checks</summary><ul>{r.validation.map((v) => <li key={v.name}>{v.passed ? "✅" : "❌"} {v.name}: {v.detail}</li>)}</ul></details>}
      <p className="small muted">{r.disclaimer}</p>
    </div>
  );
}

function ChatView({ onDone }: { onDone: () => void }) {
  const [thread] = useState(() => `ui-${Date.now().toString(36)}`);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<{ q: string; r?: Report; err?: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true); setQuestion("");
    setTurns((t) => [...t, { q: text }]);
    try {
      const r = await api.investigate(text, thread);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, r } : x)));
      onDone();
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, err: (e as Error).message } : x)));
    } finally { setBusy(false); }
  };
  const samples = ["How did Bangalore do on 2026-04-22?", "Why did STORE_101 underperform?", "Walk me through the morning hours.", "What about STORE_102?", "Compare them", "What is OR2A?"];
  return (
    <>
      <div className="row wrap">{samples.map((s) => <button key={s} className="secondary small" onClick={() => ask(s)}>{s}</button>)}</div>
      {turns.map((t, i) => (
        <div key={i} className="card">
          <div className="q">🧑 {t.q}</div>
          {t.err && <div className="error">{t.err}</div>}
          {!t.r && !t.err && <div className="muted">Investigating…</div>}
          {t.r && <ReportView r={t.r} />}
        </div>
      ))}
      <form className="row ask" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask about a store, city, SLA breaches or the playbook…" maxLength={1000} />
        <button disabled={busy}>Ask</button>
      </form>
    </>
  );
}

function HistoryView({ refresh }: { refresh: number }) {
  const hist = useAsync(() => api.history(), [refresh]);
  const [open, setOpen] = useState<Report | null>(null);
  return (
    <>
      <Status {...hist} />
      {hist.data && <table><thead><tr><th>When</th><th>Question</th><th>Intent</th><th>Status</th><th>ms</th><th /></tr></thead>
        <tbody>{hist.data.map((h: HistoryRow) => <tr key={h.id}><td>{new Date(h.created_at).toLocaleString()}</td><td>{h.question}</td><td>{h.intent}</td><td>{h.status}</td><td>{Math.round(h.latency_ms)}</td>
          <td><button className="secondary small" onClick={() => api.investigation(h.id).then((x) => setOpen(x.report))}>Open</button></td></tr>)}</tbody></table>}
      {open && <div className="card"><div className="q">{open.question}</div><ReportView r={open} /></div>}
    </>
  );
}

type Theme = "light" | "dark";

function useTheme(): [Theme, () => void] {
  const system = (): Theme => (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const [theme, setTheme] = useState<Theme>(() => {
    try { const t = localStorage.getItem("qc.theme"); if (t === "light" || t === "dark") return t; } catch { /* ignore */ }
    return system();
  });
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const toggle = () => setTheme((t) => {
    const next: Theme = t === "dark" ? "light" : "dark";
    try { localStorage.setItem("qc.theme", next); } catch { /* ignore */ }
    return next;
  });
  return [theme, toggle];
}

const ALL_TABS = ["Overview", "Compare", "SLA breaches", "Investigate", "History", "Settings"] as const;
type Tab = (typeof ALL_TABS)[number];
const TABS: readonly Tab[] = IS_PROD ? ALL_TABS.filter((t) => t !== "Settings") : ALL_TABS;

function AccessKey({ onSaved }: { onSaved: () => void }) {
  const [key, setKey] = useState("");
  return (
    <form className="card access" onSubmit={(e) => { e.preventDefault(); config.save(config.baseUrl, key.trim()); onSaved(); }}>
      <h3>Access key required</h3>
      <p className="muted small">Enter the access key you were given.</p>
      <div className="row"><input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoFocus /><button>Continue</button></div>
    </form>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [theme, toggleTheme] = useTheme();
  const [nonce, setNonce] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const meta = useAsync(() => api.meta(), [nonce]);
  const [date, setDate] = useState("");
  useEffect(() => {
    if (meta.data && !date) {
      const real = meta.data.date_info.filter((d) => !d.synthetic);
      setDate((real.at(-1) ?? meta.data.date_info.at(-1))?.date ?? "");
    }
  }, [meta.data, date]);
  const isSynthetic = !!meta.data?.date_info.find((d) => d.date === date)?.synthetic;
  const bump = useCallback(() => setRefresh((x) => x + 1), []);
  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Quick-Commerce Ops Intelligence</h1>
        {meta.data && meta.data.dates.length === 1 && (
          <div className="date-pick">Data date<div className="date-value">{date}</div></div>
        )}
        {meta.data && meta.data.dates.length > 1 && (
          <label className="date-pick">Date
            <select value={date} onChange={(e) => setDate(e.target.value)}>
              {[...meta.data.date_info].reverse().map((d) => <option key={d.date} value={d.date}>{d.date}</option>)}
            </select>
          </label>
        )}
        {isSynthetic && <div className="small muted">Simulated demo day</div>}
        <nav>{TABS.map((t) => <button key={t} className={t === tab ? "active" : "tab"} onClick={() => setTab(t)}>{t}</button>)}</nav>
        <button className="secondary theme-toggle" onClick={toggleTheme} aria-label="Toggle colour theme">
          {theme === "dark" ? "☀️ Light mode" : "🌙 Dark mode"}
        </button>
      </aside>
      <main>
        {tab === "Settings" && <Settings onSaved={() => { setNonce((n) => n + 1); setTab("Overview"); }} />}
        {meta.error && meta.status === 401 && tab !== "Settings" && <AccessKey onSaved={() => setNonce((n) => n + 1)} />}
        {meta.error && meta.status !== 401 && tab !== "Settings" && <div className="error">{meta.error}</div>}
        {meta.data && date && tab === "Overview" && <OverviewView meta={meta.data} date={date} />}
        {meta.data && date && tab === "Compare" && <CompareView meta={meta.data} date={date} />}
        {meta.data && date && tab === "SLA breaches" && <BreachView meta={meta.data} date={date} />}
        {tab === "Investigate" && <ChatView onDone={bump} />}
        {tab === "History" && <HistoryView refresh={refresh} />}
      </main>
    </div>
  );
}
