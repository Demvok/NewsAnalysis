import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

const api = async (path, options = {}) => {
  const response = await fetch(path === "/health" ? "/health" : `/api${path}`, {
    ...options,
    headers: { "content-type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? null : response.json();
};

const formatNumber = (value, digits = 0) =>
  value === null || value === undefined ? "Unavailable" : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
const formatDate = value => value ? new Date(value).toLocaleString() : "—";
const shortId = value => value ? `${value.slice(0, 8)}…` : "—";
const formatDuration = value => value === null || value === undefined ? "Unavailable" : value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
const rangeSince = range => {
  const minutes = { "15m": 15, "1h": 60, "24h": 1440, "7d": 10080 }[range] || 1440;
  return new Date(Date.now() - minutes * 60000).toISOString();
};

function Status({ value }) {
  const normalized = String(value || "UNKNOWN").toLowerCase().replaceAll("_", "-");
  return <span className={`status ${normalized}`}>{value || "UNKNOWN"}</span>;
}

function MetricCard({ label, value, detail }) {
  return <article className="metric-card"><span className="eyebrow">{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</article>;
}

function Empty({ children = "No data available for this range." }) {
  return <div className="empty">{children}</div>;
}

function ErrorState({ error, retry }) {
  return <div className="error-state"><strong>Could not load this view.</strong><span>{error?.message || "Unknown error"}</span>{retry && <button onClick={retry}>Retry</button>}</div>;
}

function MiniChart({ points, color = "#62d8b5", valueKey = "value", empty = "Awaiting live telemetry…" }) {
  const usable = points.filter(point => Number.isFinite(Number(point[valueKey])));
  if (!usable.length) return <Empty>{empty}</Empty>;
  const max = Math.max(...usable.map(point => Number(point[valueKey])), 1);
  return <div className="chart" aria-label="Live telemetry chart">
    <svg viewBox="0 0 640 150" preserveAspectRatio="none" role="img">
      <path d={`M ${usable.map((point, index) => `${(index / Math.max(usable.length - 1, 1)) * 640},${145 - (Number(point[valueKey]) / max) * 125}`).join(" L ")}`} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" />
      <line x1="0" y1="145" x2="640" y2="145" stroke="#27364d" />
    </svg>
    <div className="chart-caption"><span>{formatNumber(usable[0][valueKey])}</span><span>max {formatNumber(max)}</span><span>{formatNumber(usable[usable.length - 1][valueKey])}</span></div>
  </div>;
}

function useTelemetryStream(onEvent) {
  const lastId = useRef(0);
  useEffect(() => {
    let source;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/events/stream?last_event_id=${lastId.current}`);
      source.addEventListener("telemetry", event => {
        try {
          const value = JSON.parse(event.data);
          lastId.current = Number(event.lastEventId) || lastId.current;
          onEvent(value);
        } catch { /* malformed live events are ignored; the next refresh remains authoritative */ }
      });
      source.onerror = () => {
        source.close();
        if (!stopped) window.setTimeout(connect, 3000);
      };
    };
    connect();
    return () => { stopped = true; source?.close(); };
  }, [onEvent]);
}

function Overview({ notify }) {
  const [health, setHealth] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [stageMetrics, setStageMetrics] = useState([]);
  const [range, setRange] = useState("24h");
  const [live, setLive] = useState([]);
  const [error, setError] = useState(null);
  const load = async () => {
    try {
      setError(null);
      const [h, j, m, s] = await Promise.all([api("/health"), api("/jobs?limit=500"), api(`/metrics/llm?since=${encodeURIComponent(rangeSince(range))}`), api("/metrics/stages")]);
      setHealth(h); setJobs(j); setMetrics(m); setStageMetrics(s);
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, [range]);
  const onEvent = React.useCallback(event => setLive(items => [...items.slice(-59), event]), []);
  useTelemetryStream(onEvent);
  const running = jobs.filter(job => job.status === "RUNNING").length;
  const waiting = jobs.filter(job => ["PENDING", "RETRYING"].includes(job.status)).length;
  const completed = jobs.filter(job => job.status === "COMPLETED").length;
  const failed = jobs.filter(job => job.status === "FAILED").length;
  const liveCalls = live.filter(event => event.event_type === "llm_call");
  const liveTokens = liveCalls.reduce((sum, event) => sum + (event.input_tokens || 0) + (event.output_tokens || 0), 0);
  const liveSeries = liveCalls.map((event, index) => ({ value: event.output_tokens || 0, index }));
  if (error) return <ErrorState error={error} retry={load} />;
  return <Page title="Overview" subtitle="Operational view backed by the management service.">
    <div className="toolbar"><span className="live-dot" /> Live telemetry stream {health && <><span className="muted">·</span><Status value={health.status} /></>}<select value={range} onChange={event => setRange(event.target.value)}><option value="15m">Last 15 minutes</option><option value="1h">Last hour</option><option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option></select></div>
    <section className="metric-grid">
      <MetricCard label="Articles processed" value={formatNumber(completed)} detail="Completed management jobs in retained history" />
      <MetricCard label="Jobs running / waiting" value={`${running} / ${waiting}`} detail="Current queue state" />
      <MetricCard label="Successful / failed jobs" value={`${completed} / ${failed}`} detail="Retained job history" />
      <MetricCard label="LLM requests" value={formatNumber(metrics?.call_count)} detail={`${formatNumber(metrics?.active_call_count)} active now`} />
      <MetricCard label="Tokens (measured / estimated)" value={`${formatNumber(metrics?.input_tokens_measured || metrics?.input_tokens_estimated)} / ${formatNumber(metrics?.output_tokens_measured || metrics?.output_tokens_estimated)}`} detail="Input / output; estimates are marked by the detail" />
      <MetricCard label="Average latency" value={formatDuration(metrics?.latency_ms?.average)} detail={`P95 ${formatDuration(metrics?.latency_ms?.p95)}`} />
      <MetricCard label="Generation throughput" value={metrics?.throughput_tokens_per_second_average == null ? "Unavailable" : `${formatNumber(metrics.throughput_tokens_per_second_average, 1)} tok/s`} detail="Measured output tokens only" />
      <MetricCard label="LLM error rate" value={metrics?.call_count ? `${((metrics.failed_calls / metrics.call_count) * 100).toFixed(1)}%` : "Unavailable"} detail={`${formatNumber(metrics?.failed_calls)} failed calls`} />
    </section>
    <div className="two-column">
      <Panel title="Live output tokens" kicker={`${liveCalls.length} calls in browser window`}><MiniChart points={liveSeries} color="#f0b35b" /></Panel>
      <Panel title="Live event volume" kicker="Bounded to the latest 60 events"><MiniChart points={live.map((_, index) => ({ value: index + 1 }))} /></Panel>
    </div>
    <Panel title="Pipeline stage cost" kicker="Chronological pipeline order; bar length is total time"><div className="bar-list">{stageMetrics.length ? stageMetrics.map(stage => <div className="bar-row" key={stage.stage}><span>{stage.stage || "Unspecified"}</span><div><i style={{ width: `${Math.min(100, (stage.total_duration_ms / Math.max(...stageMetrics.map(item => item.total_duration_ms || 0), 1)) * 100)}%` }} /></div><b>{formatDuration(stage.total_duration_ms)}</b></div>) : <Empty />}</div></Panel>
    <Panel title="Service health"><div className="health-grid">{health?.dependencies?.map(service => <div className="health-item" key={service.name}><Status value={service.status} /><strong>{service.name}</strong><small>{service.availability} · {service.latency_ms == null ? "latency unavailable" : `${service.latency_ms} ms`}</small></div>) || <Empty>Health data unavailable.</Empty>}</div></Panel>
  </Page>;
}

function Page({ title, subtitle, children }) {
  return <><header className="page-header"><div><h1>{title}</h1><p>{subtitle}</p></div></header><div className="page-body">{children}</div></>;
}

function Panel({ title, kicker, children, actions }) {
  return <section className="panel"><div className="panel-header"><div><h2>{title}</h2>{kicker && <span className="kicker">{kicker}</span>}</div>{actions}</div>{children}</section>;
}

function Jobs({ openJob, notify }) {
  const [jobs, setJobs] = useState([]);
  const [status, setStatus] = useState("");
  const [article, setArticle] = useState("");
  const [error, setError] = useState(null);
  const load = () => api(`/jobs?limit=500${status ? `&status=${status}` : ""}${article ? `&article_id=${encodeURIComponent(article)}` : ""}`).then(setJobs).catch(setError);
  useEffect(() => { load(); }, [status]);
  const action = async (job, operation) => {
    if (!window.confirm(`${operation === "cancel" ? "Request cancellation" : "Retry"} for job ${shortId(job.job_id)}?`)) return;
    try { await api(`/jobs/${job.job_id}/${operation}`, { method: "POST" }); notify(`${operation} requested`); load(); } catch (e) { notify(e.message, true); }
  };
  const remove = async job => {
    if (!window.confirm(`Delete job ${shortId(job.job_id)} and its related telemetry?`)) return;
    try { await api(`/jobs/${job.job_id}`, { method: "DELETE" }); notify("Job deleted"); load(); } catch (e) { notify(e.message, true); }
  };
  const create = async () => {
    if (!article) return;
    try { await api("/jobs", { method: "POST", body: JSON.stringify({ article_id: article }), headers: { "Idempotency-Key": crypto.randomUUID() } }); notify("Analysis job queued"); setArticle(""); load(); } catch (e) { notify(e.message, true); }
  };
  return <Page title="Jobs" subtitle="Queue and inspect durable article-analysis jobs.">
    <Panel title="Queue analysis"><div className="form-row"><input value={article} onChange={e => setArticle(e.target.value)} placeholder="Article UUID" /><button onClick={create} disabled={!article}>Queue job</button></div></Panel>
    <Panel title="Job history" actions={<div className="filter-row"><select value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option>{["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "RETRYING"].map(value => <option key={value}>{value}</option>)}</select><input value={article} onChange={e => setArticle(e.target.value)} placeholder="Filter article UUID" /><button className="secondary" onClick={load}>Apply</button></div>}>
      {error ? <ErrorState error={error} retry={load} /> : jobs.length ? <div className="table-scroll"><table><thead><tr><th>Job</th><th>Article</th><th>Status</th><th>Stage</th><th>Created</th><th>Duration</th><th>Actions</th></tr></thead><tbody>{jobs.map(job => <tr key={job.job_id}><td><button className="link-button" onClick={() => openJob(job.job_id)}>{shortId(job.job_id)}</button></td><td title={job.article_id}>{shortId(job.article_id)}</td><td><Status value={job.status} /></td><td>{job.current_stage || "—"}</td><td>{formatDate(job.created_at)}</td><td>{job.started_at && job.completed_at ? formatDuration(new Date(job.completed_at) - new Date(job.started_at)) : "—"}</td><td className="actions">{job.status === "RUNNING" || job.status === "PENDING" ? <button className="danger" onClick={() => action(job, "cancel")}>Cancel</button> : null}{["FAILED", "CANCELLED"].includes(job.status) ? <button onClick={() => action(job, "retry")}>Retry</button> : null}{["COMPLETED", "FAILED", "CANCELLED"].includes(job.status) ? <button className="danger" onClick={() => remove(job)}>Delete</button> : null}</td></tr>)}</tbody></table></div> : <Empty>{error ? "Unable to load jobs." : "No jobs match the selected filters."}</Empty>}
    </Panel>
  </Page>;
}

function JobDetail({ jobId, back, notify }) {
  const [job, setJob] = useState(null);
  const [stages, setStages] = useState([]);
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState(null);
  const load = () => Promise.all([api(`/jobs/${jobId}`), api(`/jobs/${jobId}/stages`), api(`/logs?job_id=${jobId}&limit=100`)]).then(([j, s, l]) => { setJob(j); setStages(s); setLogs(l); }).catch(setError);
  useEffect(() => { load(); }, [jobId]);
  if (error) return <ErrorState error={error} retry={load} />;
  if (!job) return <Loading />;
  const llmCalls = logs.filter(log => log.event_type === "llm_call");
  return <Page title={`Job ${shortId(job.job_id)}`} subtitle={`Article ${job.article_id}`}><button className="secondary" onClick={back}>← Back to jobs</button>
    <div className="detail-grid"><Panel title="Lifecycle"><dl><dt>Status</dt><dd><Status value={job.status} /></dd><dt>Created</dt><dd>{formatDate(job.created_at)}</dd><dt>Started</dt><dd>{formatDate(job.started_at)}</dd><dt>Completed</dt><dd>{formatDate(job.completed_at)}</dd><dt>Correlation</dt><dd className="mono">{job.correlation_id}</dd><dt>Trace</dt><dd className="mono">{job.trace_id}</dd><dt>Error</dt><dd>{job.error_summary || "—"}</dd></dl></Panel>
      <Panel title="Pipeline stages"><div className="stage-list">{stages.length ? stages.map(stage => <div className="stage" key={stage.stage_id}><Status value={stage.status} /><strong>{stage.stage}</strong><span>{formatDuration(stage.duration_ms)}</span><small>{stage.error_summary || JSON.stringify(stage.summary || {})}</small></div>) : <Empty />}</div></Panel></div>
    <Panel title="Related LLM calls" kicker="Model calls correlated to this job"><CallTable calls={llmCalls} /></Panel><Panel title="Related logs" kicker="Structured records filtered by job correlation"><LogTable logs={logs} /></Panel>
  </Page>;
}

function LLMMonitoring() {
  const [metrics, setMetrics] = useState(null);
  const [calls, setCalls] = useState([]);
  const [live, setLive] = useState([]);
  const [error, setError] = useState(null);
  const load = () => Promise.all([api("/metrics/llm"), api("/logs?limit=500")]).then(([m, l]) => { setMetrics(m); setCalls(l.filter(event => event.event_type === "llm_call")); }).catch(setError);
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, []);
  const onEvent = React.useCallback(event => { if (event.event_type === "llm_call") setLive(items => [...items.slice(-59), event]); }, []);
  useTelemetryStream(onEvent);
  if (error) return <ErrorState error={error} retry={load} />;
  return <Page title="LLM Monitoring" subtitle="Measured provider telemetry, with unavailable values left explicit.">
    <section className="metric-grid compact"><MetricCard label="Requests" value={formatNumber(metrics?.call_count)} detail={`${formatNumber(metrics?.active_call_count)} active now`} /><MetricCard label="Input tokens" value={formatNumber(metrics?.input_tokens_measured || metrics?.input_tokens_estimated)} detail={metrics?.input_tokens_measured ? "Measured" : "Estimated"} /><MetricCard label="Output tokens" value={formatNumber(metrics?.output_tokens_measured || metrics?.output_tokens_estimated)} detail={metrics?.output_tokens_measured ? "Measured" : "Estimated"} /><MetricCard label="P95 latency" value={formatDuration(metrics?.latency_ms?.p95)} /><MetricCard label="Average TTFT" value={formatDuration(metrics?.time_to_first_token_ms?.average)} /><MetricCard label="Errors" value={formatNumber(metrics?.failed_calls)} /></section>
    <div className="two-column"><Panel title="Live request volume"><MiniChart points={live.map((_, index) => ({ value: index + 1 }))} /></Panel><Panel title="Live output tokens"><MiniChart points={live.map(event => ({ value: event.output_tokens ?? event.output_tokens_estimated }))} color="#f0b35b" /></Panel></div>
    <Panel title="LLM call inspector" kicker={`${calls.length} retained structured events`}><CallTable calls={calls} /><p className="muted">Output tokens and throughput are shown only when LM Studio reports usage. If no calls appear, verify LLM_URL and MANAGEMENT_EVENTS_URL, then run a new job; retained telemetry is not backfilled from plain logs.</p></Panel>
  </Page>;
}

function CallTable({ calls }) {
  const [query, setQuery] = useState("");
  const sorted = [...calls].filter(call => JSON.stringify(call).toLowerCase().includes(query.toLowerCase())).sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));
  return <><div className="filter-row"><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search model, stage, trace…" /></div><div className="table-scroll"><table><thead><tr><th>Status</th><th>Stage</th><th>Model</th><th>Duration</th><th>Tokens in / out</th><th>Throughput</th><th>Trace</th></tr></thead><tbody>{sorted.slice(0, 200).map(call => <tr key={call.event_id}><td><Status value={call.status} /></td><td>{call.stage || "—"}</td><td>{call.model || "Unavailable"}</td><td>{formatDuration(call.duration_ms)}</td><td>{call.input_tokens == null && call.input_tokens_estimated == null ? "Unavailable" : `${call.input_tokens == null ? "~" : ""}${call.input_tokens ?? call.input_tokens_estimated} / ${call.output_tokens == null ? (call.output_tokens_estimated == null ? "—" : "~" + call.output_tokens_estimated) : call.output_tokens}`}</td><td>{call.throughput_tokens_per_second == null ? "Unavailable" : `${formatNumber(call.throughput_tokens_per_second, 1)} tok/s`}</td><td className="mono">{shortId(call.trace_id)}</td></tr>)}</tbody></table></div>{!sorted.length && <Empty />}</>;
}

function LogTable({ logs }) {
  return logs.length ? <div className="table-scroll"><table><thead><tr><th>Time</th><th>Severity</th><th>Service</th><th>Event</th><th>Stage</th><th>Details</th></tr></thead><tbody>{logs.map(log => <tr key={log.event_id || `${log.timestamp}-${log.event_type}`}><td>{formatDate(log.timestamp)}</td><td><Status value={log.level || log.status} /></td><td>{log.service}</td><td>{log.event_type}</td><td>{log.stage || "—"}</td><td className="log-detail">{log.error_detail || JSON.stringify(log.attributes || {})}</td></tr>)}</tbody></table></div> : <Empty />;
}

function Logs({ notify }) {
  const [filters, setFilters] = useState({ contains: "", service: "", level: "", job_id: "", article_id: "", stage: "", trace_id: "" });
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState(null);
  const load = () => api(`/logs?limit=200${Object.entries(filters).filter(([, value]) => value).map(([key, value]) => `&${key}=${encodeURIComponent(value)}`).join("")}`).then(setLogs).catch(setError);
  useEffect(() => { load(); }, []);
  const clear = async () => {
    if (!window.confirm("Delete the currently filtered structured logs?")) return;
    const query = Object.entries(filters).filter(([key, value]) => value && key !== "contains").map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join("&");
    try { await api(`/logs${query ? `?${query}` : ""}`, { method: "DELETE" }); notify("Logs deleted"); load(); } catch (e) { notify(e.message, true); }
  };
  return <Page title="Log explorer" subtitle="Bounded structured telemetry with legacy text logs as fallback."><Panel title="Filters"><div className="filter-row"><input placeholder="Text search" value={filters.contains} onChange={e => setFilters({ ...filters, contains: e.target.value })} /><select value={filters.service} onChange={e => setFilters({ ...filters, service: e.target.value })}><option value="">All services</option><option>analysis</option><option>data-storage</option></select><select value={filters.level} onChange={e => setFilters({ ...filters, level: e.target.value })}><option value="">All severity</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select><input placeholder="Job ID" value={filters.job_id} onChange={e => setFilters({ ...filters, job_id: e.target.value })} /><input placeholder="Article ID" value={filters.article_id} onChange={e => setFilters({ ...filters, article_id: e.target.value })} /><input placeholder="Stage" value={filters.stage} onChange={e => setFilters({ ...filters, stage: e.target.value })} /><input placeholder="Trace ID" value={filters.trace_id} onChange={e => setFilters({ ...filters, trace_id: e.target.value })} /><button onClick={load}>Search</button><button className="danger" onClick={clear}>Delete filtered logs</button></div></Panel><Panel title="Records" kicker={`${logs.length} records loaded, bounded to 200`}>{error ? <ErrorState error={error} retry={load} /> : <LogTable logs={logs} />}</Panel></Page>;
}

function Services() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const load = () => api("/health").then(setHealth).catch(setError);
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, []);
  return <Page title="Services" subtitle="Availability and readiness signals reported by the backend.">{error ? <ErrorState error={error} retry={load} /> : <Panel title="Dependencies">{health?.dependencies?.map(service => <div className="service-row" key={service.name}><Status value={service.status} /><strong>{service.name}</strong><span>{service.availability}</span><span>{service.latency_ms == null ? "Latency unavailable" : `${service.latency_ms} ms`}</span><small>{service.error || JSON.stringify(service.details || {})}</small></div>) || <Loading />}</Panel>}</Page>;
}

function Configuration({ notify }) {
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState({});
  const [error, setError] = useState(null);
  const load = () => api("/settings").then(value => { setSettings(value); setForm({ request_timeout_seconds: value.request_timeout_seconds, analysis_timeout_seconds: value.analysis_timeout_seconds }); }).catch(setError);
  useEffect(load, []);
  const save = async event => { event.preventDefault(); try { const value = await api("/settings", { method: "PATCH", body: JSON.stringify({ request_timeout_seconds: Number(form.request_timeout_seconds), analysis_timeout_seconds: Number(form.analysis_timeout_seconds) }) }); setSettings(value); notify("Runtime settings updated"); } catch (e) { notify(e.message, true); } };
  return <Page title="Configuration" subtitle="Only explicitly supported, temporary runtime settings are editable.">{error ? <ErrorState error={error} retry={load} /> : <Panel title="Runtime settings" kicker="Effective values from the management API"><form onSubmit={save} className="settings-form"><label>Request timeout (seconds)<input type="number" min="0.1" max="120" step="0.1" value={form.request_timeout_seconds ?? ""} onChange={e => setForm({ ...form, request_timeout_seconds: e.target.value })} /></label><label>Analysis timeout (seconds)<input type="number" min="0.1" max="7200" step="0.1" value={form.analysis_timeout_seconds ?? ""} onChange={e => setForm({ ...form, analysis_timeout_seconds: e.target.value })} /></label><button type="submit">Save temporary settings</button></form><p className="muted">Secrets, credentials, environment variables, and model endpoints are intentionally not exposed.</p><p className="muted">Supported fields: {settings?.configurable_fields?.join(", ") || "Unavailable"}</p></Panel>}</Page>;
}

function Tasks({ notify }) {
  const [tasks, setTasks] = useState([]);
  const [articles, setArticles] = useState([]);
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState("Fresh articles");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const load = async () => {
    try {
      const [taskData, articleData] = await Promise.all([api("/tasks?limit=50"), api("/articles?limit=100")]);
      setTasks(taskData); setArticles(articleData);
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, []);
  const create = async ids => {
    try {
      const task = await api("/tasks", { method: "POST", body: JSON.stringify({ name, article_ids: ids }) });
      notify(`Task queued: ${ids.length} articles`); setSelected([]); setDetail(task); load();
    } catch (e) { notify(e.message, true); }
  };
  const fresh = async () => {
    try { const task = await api("/tasks/fresh?limit=20", { method: "POST" }); notify("Fresh articles task queued"); setDetail(task); load(); }
    catch (e) { notify(e.message, true); }
  };
  useTelemetryStream(React.useCallback(event => {
    if (event.job_id && detail?.job_ids?.includes(event.job_id)) api(`/tasks/${detail.task_id}`).then(setDetail).catch(() => {});
  }, [detail]));
  return <Page title="Tasks" subtitle="Control multi-article processing and watch aggregate analytical KPIs.">
    <Panel title="Start processing" actions={<button onClick={fresh}>Process fresh articles</button>}>
      <div className="form-row"><input value={name} onChange={e => setName(e.target.value)} placeholder="Task name" /><button disabled={!selected.length} onClick={() => create(selected)}>Process selected ({selected.length})</button></div>
      <div className="table-scroll"><table><thead><tr><th></th><th>Article</th><th>Title</th><th>Status</th></tr></thead><tbody>{articles.map(article => <tr key={article.article_id}><td><input type="checkbox" checked={selected.includes(article.article_id)} onChange={e => setSelected(e.target.checked ? [...selected, article.article_id] : selected.filter(id => id !== article.article_id))} /></td><td className="mono">{shortId(article.article_id)}</td><td>{article.article_title || "Untitled"}</td><td>{article.status || "NEW"}</td></tr>)}</tbody></table></div>
    </Panel>
    <Panel title="Task history">{error ? <ErrorState error={error} retry={load} /> : tasks.length ? <div className="table-scroll"><table><thead><tr><th>Task</th><th>Status</th><th>Progress</th><th>KPIs</th><th>Created</th></tr></thead><tbody>{tasks.map(task => <tr key={task.task_id}><td><button className="link-button" onClick={() => setDetail(task)}>{task.name}</button><small className="mono">{shortId(task.task_id)}</small></td><td><Status value={task.status} /></td><td>{task.completed_articles}/{task.total_articles} complete · {task.failed_articles} failed</td><td>{task.citations} citations · {task.topics} topics · {task.attitudes} attitudes</td><td>{formatDate(task.created_at)}</td></tr>)}</tbody></table></div> : <Empty>No tasks have been created.</Empty>}</Panel>
    {detail && <Panel title={detail.name} actions={<div className="filter-row">{["PENDING", "RUNNING"].includes(detail.status) && <button className="danger" onClick={async () => { if (!window.confirm("Request cancellation for this task?")) return; try { setDetail(await api(`/tasks/${detail.task_id}/cancel`, { method: "POST" })); notify("Task cancellation requested"); } catch (e) { notify(e.message, true); } }}>Cancel task</button>}<button className="secondary" onClick={() => setDetail(null)}>Close</button></div>}><div className="metric-grid compact"><MetricCard label="Status" value={detail.status} /><MetricCard label="Articles" value={`${detail.completed_articles}/${detail.total_articles}`} /><MetricCard label="Citations" value={formatNumber(detail.citations)} /><MetricCard label="Speakers" value={formatNumber(detail.speakers)} /><MetricCard label="Topics" value={formatNumber(detail.topics)} /><MetricCard label="Attitudes" value={formatNumber(detail.attitudes)} /></div><p className="muted">Task progress refreshes every five seconds and is also refreshed when correlated telemetry arrives.</p><p className="mono">Jobs: {detail.job_ids.join(", ")}</p></Panel>}
  </Page>;
}

function Experiments({ notify }) {
  const [experiments, setExperiments] = useState([]);
  const [selected, setSelected] = useState([]);
  const [form, setForm] = useState({ description: "", article_ids: "", dataset_name: "", model: "", prompt_version: "", inconsistency_window_days: "365" });
  const [detail, setDetail] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [error, setError] = useState(null);
  const load = () => api("/experiments?limit=100").then(setExperiments).catch(setError);
  useEffect(() => { load(); }, []);
  const create = async event => {
    event.preventDefault();
    const articleIds = form.article_ids.split(/[\s,]+/).map(value => value.trim()).filter(Boolean);
    if (!articleIds.length) return notify("Enter at least one article UUID", true);
    try {
      await api("/experiments", { method: "POST", body: JSON.stringify({
        description: form.description,
        article_ids: articleIds,
        dataset_name: form.dataset_name || null,
        model_config_snapshot: form.model ? { model: form.model } : {},
        prompt_versions: form.prompt_version ? { pipeline: form.prompt_version } : {},
        pipeline_parameters: { inconsistency_window_days: Number(form.inconsistency_window_days) },
      }) });
      notify("Experiment created"); setForm({ ...form, description: "", article_ids: "" }); load();
    } catch (e) { notify(e.message, true); }
  };
  const start = async experiment => {
    try { await api(`/experiments/${experiment.experiment_id}/start`, { method: "POST" }); notify("Experiment jobs queued"); load(); } catch (e) { notify(e.message, true); }
  };
  const compare = async () => {
    try { setComparison(await api("/experiments/compare", { method: "POST", body: JSON.stringify({ experiment_ids: selected }) })); } catch (e) { notify(e.message, true); }
  };
  return <Page title="Experiments" subtitle="Reproducible pipeline runs with configuration snapshots and operational comparisons.">
    <Panel title="Create experiment" kicker="Secrets are removed before snapshots are persisted"><form className="experiment-form" onSubmit={create}><label>Description<input required value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></label><label>Article UUIDs<input required value={form.article_ids} onChange={e => setForm({ ...form, article_ids: e.target.value })} placeholder="Comma or whitespace separated" /></label><label>Dataset name<input value={form.dataset_name} onChange={e => setForm({ ...form, dataset_name: e.target.value })} /></label><label>Model identifier<input value={form.model} onChange={e => setForm({ ...form, model: e.target.value })} placeholder="Optional snapshot" /></label><label>Prompt/template version<input value={form.prompt_version} onChange={e => setForm({ ...form, prompt_version: e.target.value })} /></label><label>Inconsistency window (days)<input type="number" min="1" max="3650" value={form.inconsistency_window_days} onChange={e => setForm({ ...form, inconsistency_window_days: e.target.value })} /></label><button type="submit">Create experiment</button></form></Panel>
    <Panel title="Experiment history" actions={<div className="filter-row"><button disabled={selected.length < 2} onClick={compare}>Compare selected ({selected.length})</button></div>}>{error ? <ErrorState error={error} retry={load} /> : experiments.length ? <div className="table-scroll"><table><thead><tr><th></th><th>Experiment</th><th>Status</th><th>Articles</th><th>Created</th><th>Jobs</th><th>Actions</th></tr></thead><tbody>{experiments.map(experiment => <tr key={experiment.experiment_id}><td><input type="checkbox" checked={selected.includes(experiment.experiment_id)} onChange={e => setSelected(e.target.checked ? [...selected, experiment.experiment_id] : selected.filter(id => id !== experiment.experiment_id))} /></td><td><button className="link-button" onClick={() => setDetail(experiment)}>{experiment.description}</button><small className="mono">{shortId(experiment.experiment_id)}</small></td><td><Status value={experiment.status} /></td><td>{experiment.article_ids.length}</td><td>{formatDate(experiment.created_at)}</td><td>{experiment.job_ids.length}</td><td>{experiment.status === "CREATED" || experiment.status === "FAILED" ? <button onClick={() => start(experiment)}>Start</button> : <button className="secondary" onClick={() => setDetail(experiment)}>Inspect</button>}</td></tr>)}</tbody></table></div> : <Empty>No experiments have been created.</Empty>}</Panel>
    {comparison && <Panel title="Comparison" kicker="Operational metrics only; quality appears only with evaluation data"><div className="comparison-grid">{comparison.experiments.map(item => <div className="comparison-card" key={item.experiment_id}><strong>{item.description}</strong><span>Articles: {item.metrics.article_count}</span><span>Time/article: {formatDuration(item.metrics.processing_time_ms_per_article)}</span><span>LLM calls/article: {formatNumber(item.metrics.llm_calls_per_article, 2)}</span><span>Tokens/article: {item.metrics.input_tokens_per_article == null ? "Unavailable" : `${formatNumber(item.metrics.input_tokens_per_article)} / ${formatNumber(item.metrics.output_tokens_per_article)}`}</span><span>Failure rate: {item.metrics.failure_rate == null ? "Unavailable" : `${(item.metrics.failure_rate * 100).toFixed(1)}%`}</span><span>Quality: {item.metrics.quality ? `F1 ${formatNumber(item.metrics.quality.f1, 3)}` : "Unavailable — no ground truth"}</span></div>)}</div></Panel>}
    {detail && <Panel title={detail.description} actions={<div className="filter-row"><a className="button-link" href={`/api/experiments/${detail.experiment_id}/export?format=json`} target="_blank" rel="noreferrer">JSON export</a><a className="button-link" href={`/api/experiments/${detail.experiment_id}/export?format=csv`}>CSV export</a><button className="secondary" onClick={() => setDetail(null)}>Close</button></div>}><div className="experiment-detail"><div><span className="eyebrow">Configuration snapshot</span><pre>{JSON.stringify({ dataset_name: detail.dataset_name, model_config_snapshot: detail.model_config_snapshot, prompt_versions: detail.prompt_versions, pipeline_parameters: detail.pipeline_parameters }, null, 2)}</pre></div><div><span className="eyebrow">Metrics</span><pre>{JSON.stringify(detail.metrics, null, 2)}</pre></div><div><span className="eyebrow">Job references</span><p className="mono">{detail.job_ids.join("\n") || "No jobs started"}</p></div></div></Panel>}
  </Page>;
}

function Loading() { return <div className="loading">Loading management data…</div>; }

function App() {
  const [view, setView] = useState("overview");
  const [selectedJob, setSelectedJob] = useState(null);
  const [notice, setNotice] = useState(null);
  const notify = (message, isError = false) => { setNotice({ message, isError }); window.setTimeout(() => setNotice(null), 4500); };
  const navigate = name => { setSelectedJob(null); setView(name); };
  const content = selectedJob ? <JobDetail jobId={selectedJob} back={() => setSelectedJob(null)} notify={notify} /> : view === "overview" ? <Overview notify={notify} /> : view === "jobs" ? <Jobs openJob={setSelectedJob} notify={notify} /> : view === "tasks" ? <Tasks notify={notify} /> : view === "llm" ? <LLMMonitoring /> : view === "logs" ? <Logs notify={notify} /> : view === "services" ? <Services /> : view === "configuration" ? <Configuration notify={notify} /> : <Experiments notify={notify} />;
  return <div className="shell"><aside className="sidebar"><div className="brand"><span className="brand-mark">N</span><div><strong>NewsAnalysis</strong><small>Management</small></div></div><nav>{[["overview", "Overview"], ["tasks", "Tasks"], ["jobs", "Jobs"], ["llm", "LLM Monitoring"], ["logs", "Logs"], ["services", "Services"], ["configuration", "Configuration"], ["experiments", "Experiments"]].map(([key, label]) => <button className={view === key && !selectedJob ? "active" : ""} key={key} onClick={() => navigate(key)}><span className={`nav-icon icon-${key}`} />{label}</button>)}</nav><div className="sidebar-footer">Local operational console<br /><span>Data from management API</span></div></aside><main className="content">{content}</main>{notice && <div className={`toast ${notice.isError ? "toast-error" : ""}`}>{notice.message}</div>}</div>;
}

createRoot(document.getElementById("root")).render(<App />);
