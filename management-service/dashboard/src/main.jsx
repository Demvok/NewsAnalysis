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
const shortId = value => value || "—";
const compactId = value => value ? `${value.slice(0, 8)}…` : "—";
const formatDuration = value => value === null || value === undefined ? "Unavailable" : value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
const rangeSince = range => {
  const minutes = { "15m": 15, "1h": 60, "24h": 1440, "7d": 10080 }[range] || 1440;
  return new Date(Date.now() - minutes * 60000).toISOString();
};
const pipelineStages = [
  ["article_retrieval", "Article retrieval", false],
  ["citation_extraction", "Citation extraction", false],
  ["citation_refinement", "Citation refinement", true],
  ["extraction_persistence", "Citation persistence", false],
  ["person_resolution", "Person resolution", false],
  ["citation_analysis", "Citation analysis", false],
  ["topic_resolution", "Topic resolution", false],
  ["attitude_persistence", "Attitude persistence", false],
  ["inconsistency_comparison", "Inconsistency analysis", true],
];

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

function RefreshButton({ onClick, loading = false }) {
  return <button className="secondary refresh-button" onClick={onClick} disabled={loading} title="Refresh while keeping current filters">
    {loading ? "Refreshing…" : "↻ Refresh"}
  </button>;
}

function CopyIdentifier({ value, notify }) {
  const copy = async event => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(String(value));
      notify("UUID copied to clipboard");
    } catch {
      notify("Could not copy UUID to clipboard", true);
    }
  };
  return <span className="identifier-copy" title={String(value)}>
    <span className="mono">{compactId(String(value))}</span>
    <button className="copy-button" type="button" aria-label={`Copy UUID ${value}`} onClick={copy}>⧉</button>
  </span>;
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
  return <Page title="Overview" subtitle="Operational view backed by the management service." refresh={load}>
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
    <Panel title="Pipeline stage cost" kicker="Mean duration is calculated from completed stage telemetry"><div className="bar-list">{stageMetrics.length ? stageMetrics.map(stage => <div className="bar-row" key={stage.stage}><span>{stage.stage || "Unspecified"}</span><div><i style={{ width: `${Math.min(100, (stage.total_duration_ms / Math.max(...stageMetrics.map(item => item.total_duration_ms || 0), 1)) * 100)}%` }} /></div><b>{formatDuration(stage.average_duration_ms)} mean · {formatDuration(stage.total_duration_ms)} total</b></div>) : <Empty />}</div></Panel>
    <Panel title="Service health"><div className="health-grid">{health?.dependencies?.map(service => <div className="health-item" key={service.name}><Status value={service.status} /><strong>{service.name}</strong><small>{service.availability} · {service.latency_ms == null ? "latency unavailable" : `${service.latency_ms} ms`}</small></div>) || <Empty>Health data unavailable.</Empty>}</div></Panel>
  </Page>;
}

function Page({ title, subtitle, children, refresh }) {
  return <><header className="page-header"><div><h1>{title}</h1><p>{subtitle}</p></div>{refresh && <RefreshButton onClick={refresh} />}</header><div className="page-body">{children}</div></>;
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
  return <Page title="Jobs" subtitle="Queue and inspect durable article-analysis jobs." refresh={load}>
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
  const outputTokens = llmCalls.reduce((sum, call) => sum + (call.output_tokens ?? call.output_tokens_estimated ?? 0), 0);
  const inputTokens = llmCalls.reduce((sum, call) => sum + (call.input_tokens ?? call.input_tokens_estimated ?? 0), 0);
  const llmDuration = llmCalls.reduce((sum, call) => sum + (call.duration_ms || 0), 0);
  const createdRecords = logs.reduce((sum, log) => sum + (log.attributes?.created_records || log.attributes?.created_new || 0), 0);
  const retrievedRecords = logs.reduce((sum, log) => sum + (log.attributes?.retrieved_records || log.attributes?.retrieved_from_db || 0), 0);
  const telemetryStages = new Map();
  logs.filter(log => log.event_type === "pipeline_stage" || log.event_type === "llm_call").forEach(log => {
    const key = log.stage || log.operation;
    if (!key) return;
    const current = telemetryStages.get(key) || { stage: key, status: "PENDING", duration_ms: null, summary: {} };
    if (log.status === "started") current.status = "RUNNING";
    if (log.status === "completed") {
      current.status = "COMPLETED";
      current.duration_ms = log.duration_ms;
      current.summary = log.attributes || {};
    }
    if (log.status === "failed") {
      current.status = "FAILED";
      current.duration_ms = log.duration_ms;
      current.summary = log.attributes || {};
    }
    telemetryStages.set(key, current);
  });
  const persistedStages = new Map(stages.map(stage => [stage.stage, stage]));
  const stageRows = pipelineStages.map(([key, label, optional]) => {
    const persisted = persistedStages.get(key);
    const telemetry = telemetryStages.get(key);
    return {
      stage: key,
      label,
      optional,
      status: persisted?.status || telemetry?.status || (optional && job.status === "COMPLETED" ? "SKIPPED" : "PENDING"),
      duration_ms: persisted?.duration_ms ?? telemetry?.duration_ms,
      summary: { ...(telemetry?.summary || {}), ...(persisted?.summary || {}) },
      error_summary: persisted?.error_summary,
    };
  });
  const elapsed = job.started_at ? Date.now() - new Date(job.started_at).getTime() : 0;
  const estimate = job.status === "COMPLETED" ? 0 : stages.filter(stage => stage.status !== "COMPLETED").reduce((sum, stage) => sum + (stage.duration_ms || 0), 0);
  return <Page title={`Job ${shortId(job.job_id)}`} subtitle={`Article ${job.article_id}`} refresh={load}><button className="secondary" onClick={back}>← Back to jobs</button>
    <section className="metric-grid compact"><MetricCard label="Input / output tokens" value={`${formatNumber(inputTokens)} / ${formatNumber(outputTokens)}`} /><MetricCard label="Tok/s" value={llmDuration ? `${formatNumber(outputTokens / (llmDuration / 1000), 1)}` : "Unavailable"} /><MetricCard label="Elapsed" value={formatDuration(elapsed)} /><MetricCard label="Estimated remaining" value={formatDuration(estimate)} /><MetricCard label="DB created / retrieved" value={`${createdRecords} / ${retrievedRecords}`} /></section>
    <div className="detail-grid"><Panel title="Lifecycle"><dl><dt>Status</dt><dd><Status value={job.status} /></dd><dt>Created</dt><dd>{formatDate(job.created_at)}</dd><dt>Started</dt><dd>{formatDate(job.started_at)}</dd><dt>Completed</dt><dd>{formatDate(job.completed_at)}</dd><dt>Correlation</dt><dd className="mono">{job.correlation_id}</dd><dt>Trace</dt><dd className="mono">{job.trace_id}</dd><dt>Error</dt><dd>{job.error_summary || "—"}</dd></dl></Panel>
      <Panel title="Pipeline stages" kicker="Optional stages are shown when the analysis service executes them"><div className="stage-list">{stageRows.map(stage => <div className={`stage ${stage.optional ? "optional-stage" : ""}`} key={stage.stage}><Status value={stage.status} /><strong>{stage.label}</strong>{stage.optional && <em>optional</em>}<span>{formatDuration(stage.duration_ms)}</span><small>{stage.error_summary || JSON.stringify(stage.summary || {})}</small></div>)}</div></Panel></div>
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
  return <Page title="LLM Monitoring" subtitle="Measured provider telemetry, with unavailable values left explicit." refresh={load}>
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
  return <Page title="Log explorer" subtitle="Bounded structured telemetry with legacy text logs as fallback." refresh={load}><Panel title="Filters"><div className="filter-row"><input placeholder="Text search" value={filters.contains} onChange={e => setFilters({ ...filters, contains: e.target.value })} /><select value={filters.service} onChange={e => setFilters({ ...filters, service: e.target.value })}><option value="">All services</option><option>analysis</option><option>data-storage</option></select><select value={filters.level} onChange={e => setFilters({ ...filters, level: e.target.value })}><option value="">All severity</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select><input placeholder="Job ID" value={filters.job_id} onChange={e => setFilters({ ...filters, job_id: e.target.value })} /><input placeholder="Article ID" value={filters.article_id} onChange={e => setFilters({ ...filters, article_id: e.target.value })} /><input placeholder="Stage" value={filters.stage} onChange={e => setFilters({ ...filters, stage: e.target.value })} /><input placeholder="Trace ID" value={filters.trace_id} onChange={e => setFilters({ ...filters, trace_id: e.target.value })} /><button onClick={load}>Search</button><button className="danger" onClick={clear}>Delete filtered logs</button></div></Panel><Panel title="Records" kicker={`${logs.length} records loaded, bounded to 200`}>{error ? <ErrorState error={error} retry={load} /> : <LogTable logs={logs} />}</Panel></Page>;
}

function Services() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const load = () => api("/health").then(setHealth).catch(setError);
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, []);
  return <Page title="Services" subtitle="Availability and readiness signals reported by the backend." refresh={load}>{error ? <ErrorState error={error} retry={load} /> : <Panel title="Dependencies">{health?.dependencies?.map(service => <div className="service-row" key={service.name}><Status value={service.status} /><strong>{service.name}</strong><span>{service.availability}</span><span>{service.latency_ms == null ? "Latency unavailable" : `${service.latency_ms} ms`}</span><small>{service.error || JSON.stringify(service.details || {})}</small></div>) || <Loading />}</Panel>}</Page>;
}

function Configuration({ notify }) {
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState({});
  const [error, setError] = useState(null);
  const load = () => api("/settings").then(value => { setSettings(value); setForm({ request_timeout_seconds: value.request_timeout_seconds, analysis_timeout_seconds: value.analysis_timeout_seconds }); }).catch(setError);
  useEffect(load, []);
  const save = async event => { event.preventDefault(); try { const value = await api("/settings", { method: "PATCH", body: JSON.stringify({ request_timeout_seconds: Number(form.request_timeout_seconds), analysis_timeout_seconds: Number(form.analysis_timeout_seconds) }) }); setSettings(value); notify("Runtime settings updated"); } catch (e) { notify(e.message, true); } };
  const runDatabase = async action => {
    if (!window.confirm(action === "recreate" ? "Drop and recreate every analytical table?" : "Create missing database tables?")) return;
    try { const result = await api(`/data/database/${action}`, { method: "POST" }); notify(`${action} completed: ${JSON.stringify(result)}`); }
    catch (e) { notify(e.message, true); }
  };
  const importTemp = async () => { try { const result = await api("/data/import-temp", { method: "POST" }); notify(`Imported ${result.imported}, skipped ${result.skipped}`); } catch (e) { notify(e.message, true); } };
  return <Page title="Configuration" subtitle="Runtime settings and testing database controls." refresh={load}>{error ? <ErrorState error={error} retry={load} /> : <><Panel title="Runtime settings" kicker="Effective values from the management API"><form onSubmit={save} className="settings-form"><label>Request timeout (seconds)<input type="number" min="0.1" max="120" step="0.1" value={form.request_timeout_seconds ?? ""} onChange={e => setForm({ ...form, request_timeout_seconds: e.target.value })} /></label><label>Analysis timeout (seconds)<input type="number" min="0.1" max="7200" step="0.1" value={form.analysis_timeout_seconds ?? ""} onChange={e => setForm({ ...form, analysis_timeout_seconds: e.target.value })} /></label><button type="submit">Save temporary settings</button></form><p className="muted">Secrets, credentials, environment variables, and model endpoints are intentionally not exposed.</p><p className="muted">Supported fields: {settings?.configurable_fields?.join(", ") || "Unavailable"}</p></Panel><Panel title="Testing database controls" kicker="Destructive operations are intended for the testing database"><div className="filter-row"><button onClick={() => runDatabase("create")}>Create missing tables</button><button className="danger" onClick={() => runDatabase("recreate")}>Drop & recreate database</button><button className="secondary" onClick={importTemp}>Import temporary articles</button></div></Panel></>}</Page>;
}

function Tasks({ notify, openJob }) {
  const [tasks, setTasks] = useState([]);
  const [articles, setArticles] = useState([]);
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState("Fresh articles");
  const [detail, setDetail] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [tab, setTab] = useState("create");
  const [error, setError] = useState(null);
  const load = async () => {
    try {
      const [taskData, articleData] = await Promise.all([api("/tasks?limit=50"), api("/articles?limit=100")]);
      setTasks(taskData); setArticles(articleData);
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    if (!detail?.task_id) { setAnalytics(null); return undefined; }
    const loadAnalytics = () => api(`/tasks/${detail.task_id}/analytics`).then(setAnalytics).catch(() => setAnalytics(null));
    loadAnalytics();
    const timer = setInterval(loadAnalytics, 5000);
    return () => clearInterval(timer);
  }, [detail?.task_id]);
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
  return <Page title="Tasks" subtitle="Control multi-article processing and watch aggregate analytical KPIs." refresh={load}>
    <div className="browser-tabs"><button className={tab === "create" ? "active" : "secondary"} onClick={() => setTab("create")}>Create task</button><button className={tab === "history" ? "active" : "secondary"} onClick={() => setTab("history")}>Existing tasks</button></div>
    {tab === "create" && <Panel title="Start processing" actions={<button onClick={fresh}>Process fresh articles</button>}>
      <div className="form-row"><input value={name} onChange={e => setName(e.target.value)} placeholder="Task name" /><button disabled={!selected.length} onClick={() => create(selected)}>Process selected ({selected.length})</button></div>
      <div className="table-scroll"><table><thead><tr><th></th><th>Article</th><th>Title</th><th>Status</th></tr></thead><tbody>{articles.map(article => <tr key={article.article_id}><td><input type="checkbox" checked={selected.includes(article.article_id)} onChange={e => setSelected(e.target.checked ? [...selected, article.article_id] : selected.filter(id => id !== article.article_id))} /></td><td className="mono">{shortId(article.article_id)}</td><td>{article.article_title || "Untitled"}</td><td>{article.status || "NEW"}</td></tr>)}</tbody></table></div>
    </Panel>}
    {tab === "history" && <Panel title="Task history">{error ? <ErrorState error={error} retry={load} /> : tasks.length ? <div className="table-scroll"><table><thead><tr><th>Task</th><th>Status</th><th>Progress</th><th>KPIs</th><th>Created</th></tr></thead><tbody>{tasks.map(task => <tr key={task.task_id}><td><button className="link-button" onClick={() => setDetail(task)}>{task.name}</button><small className="mono">{shortId(task.task_id)}</small></td><td><Status value={task.status} /></td><td>{task.completed_articles}/{task.total_articles} complete · {task.failed_articles} failed</td><td>{task.citations} citations · {task.topics} topics · {task.attitudes} attitudes</td><td>{formatDate(task.created_at)}</td></tr>)}</tbody></table></div> : <Empty>No tasks have been created.</Empty>}</Panel>}
    {detail && <Panel title={detail.name} actions={<div className="filter-row">{["PENDING", "RUNNING"].includes(detail.status) && <button className="danger" onClick={async () => { if (!window.confirm("Request cancellation for this task?")) return; try { setDetail(await api(`/tasks/${detail.task_id}/cancel`, { method: "POST" })); notify("Task cancellation requested"); } catch (e) { notify(e.message, true); } }}>Cancel task</button>}{!["PENDING", "RUNNING", "RETRYING"].includes(detail.status) && <button className="danger" onClick={async () => { if (!window.confirm("Delete this task and its job history?")) return; try { await api(`/tasks/${detail.task_id}`, { method: "DELETE" }); notify("Task deleted"); setDetail(null); load(); } catch (e) { notify(e.message, true); } }}>Delete task</button>}<button className="secondary" onClick={() => setDetail(null)}>Close</button></div>}><div className="metric-grid compact"><MetricCard label="Status" value={detail.status} /><MetricCard label="Articles" value={`${detail.completed_articles}/${detail.total_articles}`} /><MetricCard label="Citations" value={formatNumber(detail.citations)} /><MetricCard label="Speakers" value={formatNumber(detail.speakers)} /><MetricCard label="Topics" value={formatNumber(detail.topics)} /><MetricCard label="Attitudes" value={formatNumber(detail.attitudes)} /><MetricCard label="LLM tok/s" value={formatNumber(analytics?.llm?.tok_per_second, 1)} /><MetricCard label="DB created / retrieved" value={`${formatNumber(analytics?.totals?.created_records)} / ${formatNumber(analytics?.totals?.retrieved_records)}`} /></div><p className="muted">Live estimates update every five seconds from completed stage duration and observed analytical counts.</p><div className="timeline">{analytics?.stages?.map(stage => <div className="timeline-row" key={stage.stage}><strong>{stage.stage}</strong><span>{formatDuration(stage.duration_ms)}</span><span>{stage.events} events · {stage.citations} citations · {stage.topics} topics · {stage.people} people · {stage.attitudes} attitudes</span><small>{stage.created_records} created / {stage.retrieved_records} retrieved</small></div>) || <Empty>Stage analytics will appear when telemetry arrives.</Empty>}</div><p className="mono">Jobs: {detail.job_ids.map(jobId => <button className="link-button" key={jobId} onClick={() => openJob(jobId)}>{jobId}</button>)}</p></Panel>}
  </Page>;
}

const dataResources = [
  ["articles", "Articles", "article_id"],
  ["citations", "Citations", "citation_uuid"],
  ["topics", "Topics", "topic_uuid"],
  ["people", "People", "person_uuid"],
  ["attitudes", "Attitudes", "citation_uuid"],
  ["inconsistencies", "Inconsistencies", "inconsistency_uuid"],
];

const statusOptions = {
  articles: ["NEW", "INGESTED", "CITATIONS_EXTRACTED", "ANALYZED", "COMPLETED", "FAILED"],
  citations: ["NEW", "EXTRACTED", "VALIDATED", "ANALYZED", "FAILED"],
  topics: ["NEW", "RESOLVED", "ANALYZED", "FAILED"],
  people: ["NEW", "RESOLVED", "FAILED"],
  attitudes: ["NEW", "ANALYZED", "FAILED"],
  inconsistencies: ["CONSISTENT", "POSITION_CHANGE", "CONTRADICTION", "INSUFFICIENT_EVIDENCE"],
};

const resourceLabel = resource => dataResources.find(item => item[0] === resource)?.[1] || resource;
const dataId = (resource, record) => resource === "attitudes"
  ? `${record.topic_uuid}/${record.citation_uuid}`
  : record[resource === "articles" ? "article_id" : resource === "citations" ? "citation_uuid" : resource === "topics" ? "topic_uuid" : resource === "people" ? "person_uuid" : "inconsistency_uuid"];

function DataDetail({ resource, detail, onClose, onSaved, notify }) {
  const [form, setForm] = useState({});
  useEffect(() => {
    const editable = ["article_title", "article_content", "url", "author", "status", "exact_quote", "context", "citation_type", "summarized_quote", "person_uuid", "topic_name", "topic_description", "general_topic_field", "name", "affiliation", "aliases", "stance", "stance_summary", "classification", "severity", "relevancy_score", "confidence_level", "extraction_confidence", "stance_confidence"];
    setForm(Object.fromEntries(editable.filter(key => detail?.[key] !== undefined).map(key => [key, typeof detail[key] === "object" ? JSON.stringify(detail[key]) : detail[key] ?? ""])));
  }, [detail]);
  const save = async event => {
    event.preventDefault();
    const payload = { ...form };
    ["relevancy_score", "confidence_level", "extraction_confidence", "stance_confidence"].forEach(key => { if (payload[key] !== undefined && payload[key] !== "") payload[key] = Number(payload[key]); });
    try {
      ["aliases", "affiliation"].forEach(key => {
        if (payload[key] !== undefined) payload[key] = JSON.parse(payload[key]);
      });
    } catch {
      notify("Aliases and affiliation must contain valid JSON", true);
      return;
    }
    try { await api(`/data/${resource}/${dataId(resource, detail)}`, { method: "PUT", body: JSON.stringify(payload) }); notify(`${resourceLabel(resource)} updated`); onSaved(); }
    catch (e) { notify(e.message, true); }
  };
  const fields = Object.keys(form);
  return <Panel title={`${resourceLabel(resource)} detail`} actions={<button className="secondary" onClick={onClose}>Close</button>}>
    <div className="identity-grid">{["inconsistency_uuid", "topic_uuid", "citation_uuid", "citation_a_uuid", "citation_b_uuid", "person_uuid", "origin_article_id"].filter(key => detail?.[key]).map(key => <div key={key}><span className="eyebrow">{key.replaceAll("_", " ")}</span><div className="mono">{detail[key]}</div></div>)}</div>
    {(detail.distance !== undefined || detail.cosine_similarity !== undefined) && <div className="identity-grid"><div><span className="eyebrow">Cosine similarity</span><div>{formatNumber(detail.cosine_similarity, 5)}</div></div><div><span className="eyebrow">Distance</span><div>{formatNumber(detail.distance, 5)}</div></div></div>}
    {detail.article && <p className="muted">Article: {detail.article.article_title || shortId(detail.origin_article_id)}</p>}
    {detail.person && <p className="muted">Person: {detail.person.name}</p>}
    {detail.topic && <p className="muted">Topic: {detail.topic.topic_name}</p>}
    <form className="edit-grid" onSubmit={save}>{fields.map(key => <label key={key}>{key.replaceAll("_", " ")}{["article_content", "exact_quote", "context", "summarized_quote", "topic_description", "stance_summary"].includes(key) ? <textarea rows="4" value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} /> : <input value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} />}</label>)}<button type="submit">Save changes</button></form>
    {resource === "articles" && detail.citations?.length > 0 && <Related title="Citations" items={detail.citations} labelKey="exact_quote" />}
    {resource === "topics" && <><Related title="Linked people" items={detail.people || []} labelKey="name" /><Related title="Linked articles" items={detail.articles || []} labelKey="article_title" /></>}
    {resource === "citations" && detail.attitudes?.length > 0 && <Related title="Attitudes" items={detail.attitudes} labelKey="stance" />}
    {resource === "attitudes" && detail.citation && <Related title="Evidence citation" items={[detail.citation]} labelKey="exact_quote" />}
    {resource === "inconsistencies" && <><Related title="Citation A" items={detail.citation_a ? [detail.citation_a] : []} labelKey="exact_quote" /><Related title="Citation B" items={detail.citation_b ? [detail.citation_b] : []} labelKey="exact_quote" /></>}
  </Panel>;
}

function Related({ title, items, labelKey }) {
  return <div className="related"><h3>{title}</h3>{items.map((item, index) => <div className="related-item" key={item.article_id || item.person_uuid || item.citation_uuid || index}><span>{item[labelKey] || "—"}</span><small>{shortId(item.article_id || item.person_uuid || item.citation_uuid)}</small></div>)}</div>;
}

function DataManager({ notify }) {
  const [resource, setResource] = useState("articles");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [categoryValue, setCategoryValue] = useState("");
  const [categoryOptions, setCategoryOptions] = useState([]);
  const [personFilter, setPersonFilter] = useState("");
  const [topicFilter, setTopicFilter] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ items: [], total: 0, pages: 0 });
  const [sort, setSort] = useState({ key: "", direction: "asc" });
  const [summary, setSummary] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [semantic, setSemantic] = useState({ resource: "citations", query: "", results: [], loading: false, error: null });
  const [minSimilarity, setMinSimilarity] = useState(0);
  const loadSequence = useRef(0);
  const load = async (targetPage = page) => {
    const sequence = ++loadSequence.current;
    try {
      setError(null);
      const params = new URLSearchParams({ page: targetPage, page_size: 20 });
      if (query.trim()) params.set("query", query.trim());
      if (semantic.query.trim() && ["citations", "topics"].includes(resource)) params.set("semantic_query", semantic.query.trim());
      if (semantic.query.trim() && ["citations", "topics"].includes(resource)) params.set("min_similarity", minSimilarity);
      if (status) params.set("status", status);
      if (category && categoryValue) { params.set("category", category); params.set("category_value", categoryValue); }
      if (personFilter && ["attitudes", "inconsistencies"].includes(resource)) params.set("person_query", personFilter);
      if (topicFilter && ["attitudes", "inconsistencies"].includes(resource)) params.set("topic_query", topicFilter);
      if (sort.key && ["topics", "attitudes", "inconsistencies"].includes(resource)) { params.set("sort_by", sort.key); params.set("sort_dir", sort.direction); }
      const [records, counts] = await Promise.all([api(`/data/${resource}?${params}`), api("/knowledge/summary")]);
      if (sequence !== loadSequence.current) return;
      setData(records); setSummary(counts); setPage(targetPage);
    } catch (e) {
      if (sequence === loadSequence.current) {
        setData({ items: [], total: 0, pages: 0 });
        setError(e);
      }
    }
  };
  useEffect(() => {
    setData({ items: [], total: 0, pages: 0 });
    setError(null);
    load(1);
  }, [resource]);
  useEffect(() => {
    if (category && categoryValue) load(1);
  }, [categoryValue]);
  useEffect(() => {
    if (!category) { setCategoryOptions([]); return; }
    api(`/data-options/${resource}/${category}`).then(setCategoryOptions).catch(() => setCategoryOptions([]));
  }, [resource, category]);
  useEffect(() => {
    if (personFilter || topicFilter) load(1);
  }, [personFilter, topicFilter]);
  useEffect(() => {
    if (sort.key && ["topics", "attitudes", "inconsistencies"].includes(resource)) load(page);
  }, [sort.key, sort.direction]);
  const open = async record => {
    try { setDetail({ ...record, ...(await api(`/data/${resource}/${dataId(resource, record)}`)) }); } catch (e) { notify(e.message, true); }
  };
  const runDatabase = async action => {
    if (!window.confirm(action === "recreate" ? "Drop and recreate every analytical table?" : "Create missing database tables?")) return;
    try { const result = await api(`/data/database/${action}`, { method: "POST" }); notify(`${action} completed: ${JSON.stringify(result)}`); load(1); } catch (e) { notify(e.message, true); }
  };
  const importTemp = async () => { try { const result = await api("/data/import-temp", { method: "POST" }); notify(`Imported ${result.imported}, skipped ${result.skipped}`); load(1); } catch (e) { notify(e.message, true); } };
  const columns = resource === "articles" ? ["article_id", "article_title", "url", "published_at", "source", "status"] : resource === "citations" ? ["citation_uuid", "origin_article_id", "person_uuid", "exact_quote", "citation_type", "confidence_level", "extraction_confidence", "status", "cosine_similarity", "distance"] : resource === "topics" ? ["topic_uuid", "topic_name", "citation_count", "people_count", "stance_average", "stance_std", "stance_median", "topic_description", "general_topic_field", "status"] : resource === "people" ? ["person_uuid", "name", "person_type", "aliases", "status"] : resource === "attitudes" ? ["topic_uuid", "citation_uuid", "person_uuid", "origin_article_id", "person_name", "topic_name", "citation_exact_quote", "citation_summarized_quote", "stance", "relevancy_score", "stance_confidence", "status"] : ["inconsistency_uuid", "person_uuid", "person_name", "topic_uuid", "topic_name", "citation_a_uuid", "citation_a_exact_quote", "citation_b_uuid", "citation_b_exact_quote", "classification", "severity", "confidence"];
  const sortedItems = resource === "topics" ? data.items : [...data.items].sort((left, right) => {
    if (!sort.key) return 0;
    const a = String(left[sort.key] ?? "").toLowerCase();
    const b = String(right[sort.key] ?? "").toLowerCase();
    return (a < b ? -1 : a > b ? 1 : 0) * (sort.direction === "asc" ? 1 : -1);
  });
  const toggleSort = key => setSort(current => current.key === key ? { key, direction: current.direction === "asc" ? "desc" : "asc" } : { key, direction: "asc" });
  const sortIndicator = key => sort.key === key ? (sort.direction === "asc" ? " ↑" : " ↓") : "";
  const categoryFields = {
    articles: ["status", "source"],
    citations: ["status", "citation_type"],
    topics: ["status", "general_topic_field"],
    people: ["status", "person_type"],
    attitudes: ["status", "stance"],
    inconsistencies: ["status", "classification", "severity"],
  };
  const categoryValues = categoryOptions;
  const people = [...new Map(data.items.filter(item => item.person_uuid).map(item => [item.person_uuid, item.person_name || item.person_uuid])).entries()];
  const topics = [...new Map(data.items.filter(item => item.topic_uuid).map(item => [item.topic_uuid, item.topic_name || item.topic_uuid])).entries()];
  const selectCategory = value => { setCategory(value); setCategoryValue(""); setPage(1); };
  const runSemanticSearch = () => { if (semantic.query.trim().length >= 2 && ["citations", "topics"].includes(resource)) load(1); };
  return <Page title="Data management" subtitle="Browse, inspect, and edit the analytical data." refresh={() => load(page)}>
    <section className="metric-grid compact">{dataResources.map(([key, label]) => <MetricCard key={key} label={label} value={formatNumber(summary?.entities?.[key])} detail="Stored records" />)}</section>
    <Panel title="Use case" kicker="Choose the entity you want to inspect"><div className="resource-tabs">{dataResources.map(([key, label]) => <button className={resource === key ? "active" : "secondary"} key={key} onClick={() => { loadSequence.current += 1; setResource(key); setDetail(null); setStatus(""); setCategory(""); setCategoryValue(""); setPersonFilter(""); setTopicFilter(""); setQuery(""); setSemantic(current => ({ ...current, query: "" })); }}>{label}</button>)}</div></Panel>
    {["attitudes", "inconsistencies"].includes(resource) && <div className="filter-row relation-filters"><input value={personFilter} onChange={e => setPersonFilter(e.target.value)} placeholder="Search person…" /><input value={topicFilter} onChange={e => setTopicFilter(e.target.value)} placeholder="Search topic…" /><button className="secondary" onClick={() => { setPersonFilter(""); setTopicFilter(""); load(1); }}>Clear relation filters</button></div>}
    <Panel title={`${resourceLabel(resource)} browser`} actions={<div className="filter-row"><input value={query} onChange={e => setQuery(e.target.value)} placeholder={`Search ${resourceLabel(resource).toLowerCase()}`} /><button onClick={() => load(1)}>Search</button>{["citations", "topics"].includes(resource) && <><input value={semantic.query} onChange={e => setSemantic(current => ({ ...current, query: e.target.value }))} onKeyDown={e => e.key === "Enter" && runSemanticSearch()} placeholder={`Semantic ${resource === "citations" ? "citation" : "topic"} search`} /><input type="number" min="-1" max="1" step="0.01" value={minSimilarity} onChange={e => setMinSimilarity(e.target.value)} title="Minimum cosine similarity" /><button onClick={runSemanticSearch} disabled={semantic.query.trim().length < 2}>Search meaning</button></>}</div>}>
      {error ? <ErrorState error={error} retry={() => load(page)} /> : data.items.length ? <><div className="table-scroll"><table><thead><tr>{columns.map(column => <th key={column}>{categoryFields[resource].includes(column) ? (category === column ? <select value={categoryValue} onChange={e => setCategoryValue(e.target.value)} onClick={e => e.stopPropagation()}><option value="">All {column.replaceAll("_", " ")}</option>{categoryValues.map(value => <option key={value} value={value}>{value}</option>)}</select> : <button className="table-sort" onClick={() => selectCategory(column)}>{column.replaceAll("_", " ")}{category === column && categoryValue ? `: ${categoryValue}` : ""}</button>) : <button className="table-sort" onClick={() => toggleSort(column)}>{column.replaceAll("_", " ")}{sortIndicator(column)}</button>}</th>)}<th>Action</th></tr></thead><tbody>{sortedItems.map((record, index) => <tr key={dataId(resource, record) || index}>{columns.map(column => { const numericStance = ["stance_average", "stance_std", "stance_median"].includes(column); const value = record[column]; const scoreClass = column === "stance_std" ? "variability" : Number(value) < 0 ? "negative" : Number(value) > 0 ? "positive" : "neutral"; return <td key={column} title={String(value ?? "")} className={numericStance && value != null ? `stance-score ${scoreClass}` : ""}>{resource === "inconsistencies" && column.endsWith("_uuid") && value ? <CopyIdentifier value={value} notify={notify} /> : numericStance ? formatNumber(value, 2) : String(value ?? "—").slice(0, 180)}</td>; })}<td><button className="link-button" onClick={() => open(record)}>View / edit</button></td></tr>)}</tbody></table></div><div className="pagination"><button disabled={page <= 1} onClick={() => load(page - 1)}>Previous</button><span>Page {page} of {Math.max(data.pages, 1)} · {data.total} records</span><button disabled={page >= data.pages} onClick={() => load(page + 1)}>Next</button></div></> : <Empty>No {resource} records found.</Empty>}
    </Panel>
    {resource === "attitudes" && <Panel title="Attitude stance KPIs"><section className="metric-grid compact">{Object.entries(data.stance_counts || {}).map(([stance, count]) => <MetricCard key={stance} label={stance} value={formatNumber(count)} detail="Matching attitudes" />)}</section></Panel>}
    {detail && <DataDetail resource={resource} detail={detail} onClose={() => setDetail(null)} onSaved={() => { setDetail(null); load(page); }} notify={notify} />}
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
  return <Page title="Experiments" subtitle="Reproducible pipeline runs with configuration snapshots and operational comparisons." refresh={load}>
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
  const content = selectedJob ? <JobDetail jobId={selectedJob} back={() => setSelectedJob(null)} notify={notify} /> : view === "overview" ? <Overview notify={notify} /> : view === "data" ? <DataManager notify={notify} /> : view === "jobs" ? <Jobs openJob={setSelectedJob} notify={notify} /> : view === "tasks" ? <Tasks openJob={setSelectedJob} notify={notify} /> : view === "llm" ? <LLMMonitoring /> : view === "logs" ? <Logs notify={notify} /> : view === "services" ? <Services /> : view === "configuration" ? <Configuration notify={notify} /> : <Experiments notify={notify} />;
  const navigation = [
    ["group", "Data"],
    ["data", "Browse & edit data"],
    ["group", "Processing"],
    ["tasks", "Tasks"],
    ["jobs", "Jobs"],
    ["experiments", "Experiments"],
    ["group", "Operations"],
    ["overview", "Overview"],
    ["llm", "LLM Monitoring"],
    ["logs", "Logs"],
    ["services", "Services"],
    ["configuration", "Configuration"],
  ];
  return <div className="shell"><aside className="sidebar"><div className="brand"><span className="brand-mark">N</span><div><strong>NewsAnalysis</strong><small>Management</small></div></div><nav>{navigation.map(([key, label], index) => key === "group" ? <div className="nav-group" key={`${key}-${index}`}>{label}</div> : <button className={view === key && !selectedJob ? "active" : ""} key={key} onClick={() => navigate(key)}><span className={`nav-icon icon-${key}`} />{label}</button>)}</nav><div className="sidebar-footer">Local operational console<br /><span>Data from management API</span></div></aside><main className="content">{content}</main>{notice && <div className={`toast ${notice.isError ? "toast-error" : ""}`}>{notice.message}</div>}</div>;
}

createRoot(document.getElementById("root")).render(<App />);
