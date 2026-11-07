import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { createEventDeduper } from "./metrics";

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
const formatTokens = item => {
  if (!item || item.token_source === "none") return "Unavailable";
  const input = item.input_tokens || item.input_tokens_estimated;
  const output = item.output_tokens || item.output_tokens_estimated;
  return `${item.input_tokens ? "" : "~"}${formatNumber(input)} / ${item.output_tokens ? "" : "~"}${formatNumber(output)}`;
};
const formatDate = value => value ? new Date(value).toLocaleString() : "—";
const shortId = value => value || "—";
const compactId = value => value ? `${value.slice(0, 8)}…` : "—";
const formatDuration = value => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "Unavailable";
  const milliseconds = Math.max(0, Number(value));
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainder}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m ${remainder}s`;
};
const rangeSince = range => {
  const minutes = { "15m": 15, "1h": 60, "24h": 1440, "7d": 10080 }[range] || 1440;
  return new Date(Date.now() - minutes * 60000).toISOString();
};
const pipelineStages = [
  ["article_retrieval", "Article retrieval", false],
  ["citation_extraction", "Citation extraction", false],
  ["citation_refinement", "Citation refinement", true],
  ["extraction_persistence", "Citation persistence", false],
  ["person_candidate_search", "Person candidate search", false],
  ["person_resolution_decision", "Person resolution decisions", true],
  ["person_resolution", "Person resolution", false],
  ["citation_analysis", "Citation analysis", false],
  ["speaker_analysis", "Speaker analysis / parallel LLM work", false],
  ["topic_candidate_search", "Topic candidate search", false],
  ["topic_resolution_decision", "Topic resolution decisions", true],
  ["topic_resolution", "Topic resolution", false],
  ["topic_stance_reevaluation", "Topic stance reevaluation", true],
  ["attitude_persistence", "Attitude persistence", false],
  ["historical_precheck", "Historical citation precheck", false],
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

function useTelemetryStream(onEvent) {
  const lastId = useRef(0);
  const acceptsEvent = useRef(createEventDeduper());
  useEffect(() => {
    let source;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/events/stream?last_event_id=${lastId.current}`);
      source.addEventListener("telemetry", event => {
        try {
          const value = JSON.parse(event.data);
          const sequence = Number(event.lastEventId);
          lastId.current = sequence || lastId.current;
          if (acceptsEvent.current({ ...value, sequence })) onEvent(value);
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
  const [error, setError] = useState(null);
  const [tasks, setTasks] = useState([]);
  const load = async () => {
    try {
      setError(null);
      const [h, j, m, s, execution, taskData] = await Promise.all([api("/health"), api("/jobs?limit=100"), api(`/metrics/llm?since=${encodeURIComponent(rangeSince(range))}`), api("/metrics/stages"), api("/metrics/jobs"), api("/tasks?limit=50")]);
      setHealth({ ...h, telemetry_health: execution.telemetry_health }); setJobs(j); setMetrics(m); setStageMetrics(s); setTasks(taskData);
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); }, [range]);
  const running = jobs.filter(job => job.status === "RUNNING").length;
  const waiting = jobs.filter(job => ["PENDING", "RETRYING"].includes(job.status)).length;
  const completed = jobs.filter(job => job.status === "COMPLETED").length;
  const failed = jobs.filter(job => job.status === "FAILED").length;
  if (error) return <ErrorState error={error} retry={load} />;
  return <Page title="Overview" subtitle="Operational view backed by the management service." refresh={load}>
    <div className="toolbar"><span className="live-dot" /> Live telemetry (informational) {health && <><span className="muted">·</span><Status value={health.status} /><span className="muted">· telemetry {health.telemetry_health?.status || "unknown"}, dropped {formatNumber(health.telemetry_health?.dropped_events)}</span></>}<select value={range} onChange={event => setRange(event.target.value)}><option value="15m">Last 15 minutes</option><option value="1h">Last hour</option><option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option></select></div>
    <section className="metric-grid">
      <MetricCard label="Articles processed" value={formatNumber(completed)} detail="Completed management jobs in retained history" />
      <MetricCard label="Jobs running / waiting" value={`${running} / ${waiting}`} detail="Current queue state" />
      <MetricCard label="Successful / failed jobs" value={`${completed} / ${failed}`} detail="Retained job history" />
      <MetricCard label="LLM requests" value={formatNumber(metrics?.call_count)} detail={`${formatNumber(metrics?.active_call_count)} active now`} />
      <MetricCard label="Tokens (measured / estimated)" value={`${formatNumber(metrics?.input_tokens_measured || metrics?.input_tokens_estimated)} / ${formatNumber(metrics?.output_tokens_measured || metrics?.output_tokens_estimated)}`} detail="Input / output; estimates are marked by the detail" />
      <MetricCard label="Average latency" value={formatDuration(metrics?.latency_ms?.average)} detail={`P95 ${formatDuration(metrics?.latency_ms?.p95)}`} />
      <MetricCard label="LLM error rate" value={metrics?.call_count ? `${((metrics.failed_calls / metrics.call_count) * 100).toFixed(1)}%` : "Unavailable"} detail={`${formatNumber(metrics?.failed_calls)} failed calls`} />
    </section>
    <Panel title="Task execution" kicker="Aggregated compute time and remaining estimates for every retained task">
      {tasks.length ? <div className="metric-grid compact">{tasks.map(task => <article className="metric-card task-kpi" key={task.task_id}><span className="eyebrow">{task.name}</span><strong>{formatDuration(task.elapsed_time_ms)}</strong><small>Elapsed · remaining {formatDuration(task.remaining_time_ms)}</small><small>Estimated total {formatDuration(task.estimated_total_time_ms)}</small><small>{task.completed_articles}/{task.total_articles} articles · tokens {formatTokens(task)}</small></article>)}</div> : <Empty>No tasks have been created.</Empty>}
    </Panel>
    <Panel title="Pipeline stage cost" kicker="Mean duration is calculated from completed stage telemetry"><div className="bar-list">{stageMetrics.length ? stageMetrics.map(stage => <div className="bar-row" key={stage.stage}><span>{stage.stage || "Unspecified"}</span><div><i style={{ width: `${Math.min(100, (stage.total_duration_ms / Math.max(...stageMetrics.map(item => item.total_duration_ms || 0), 1)) * 100)}%` }} /></div><b>{formatDuration(stage.average_duration_ms)} mean · {formatDuration(stage.total_duration_ms)} total</b></div>) : <Empty />}</div></Panel>
    <Panel title="Service health"><div className="health-grid">{health?.dependencies?.map(service => <div className="health-item" key={service.name}><Status value={service.status} /><strong>{service.name}</strong><small>{service.availability} · {service.latency_ms == null ? "latency unavailable" : `${service.latency_ms} ms`}</small></div>) || <Empty>Health data unavailable.</Empty>}</div></Panel>
  </Page>;
}

function Page({ title, subtitle, children, refresh }) {
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);
  useEffect(() => {
    const timer = setInterval(() => refreshRef.current?.(), 15000);
    return () => clearInterval(timer);
  }, []);
  return <><header className="page-header"><div><h1>{title}</h1><p>{subtitle}</p></div>{refresh && <RefreshButton onClick={refresh} />}</header><div className="page-body">{children}</div></>;
}

function Panel({ title, kicker, children, actions }) {
  return <section className="panel"><div className="panel-header"><div><h2>{title}</h2>{kicker && <span className="kicker">{kicker}</span>}</div>{actions}</div>{children}</section>;
}

function Jobs({ openJob, notify }) {
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState([]);
  const [status, setStatus] = useState("");
  const [article, setArticle] = useState("");
  const [error, setError] = useState(null);
  const load = () => api(`/jobs?limit=100${status ? `&status=${status}` : ""}${article ? `&article_id=${encodeURIComponent(article)}` : ""}`).then(setJobs).catch(setError);
  useEffect(() => { load(); }, [status]);
  useEffect(() => { setSelected(current => current.filter(id => jobs.some(job => job.job_id === id))); }, [jobs]);
  const action = async (job, operation) => {
    if (!window.confirm(`${operation === "cancel" ? "Request cancellation" : "Retry"} for job ${shortId(job.job_id)}?`)) return;
    try { await api(`/jobs/${job.job_id}/${operation}`, { method: "POST" }); notify(`${operation} requested`); load(); } catch (e) { notify(e.message, true); }
  };
  const remove = async job => {
    if (!window.confirm(`Delete job ${shortId(job.job_id)} and its related telemetry?`)) return;
    try { await api(`/jobs/${job.job_id}`, { method: "DELETE" }); notify("Job deleted"); load(); } catch (e) { notify(e.message, true); }
  };
  const bulk = async operation => {
    if (!selected.length || !window.confirm(`${operation === "retry" ? "Retry" : "Delete"} ${selected.length} selected jobs?`)) return;
    try { await api(`/jobs/bulk/${operation}`, { method: "POST", body: JSON.stringify(selected) }); setSelected([]); notify(`Bulk ${operation} requested`); load(); } catch (e) { notify(e.message, true); }
  };
  const grouped = jobs.reduce((groups, job) => {
    const key = job.metadata?.task_id || "unassigned";
    (groups[key] ||= []).push(job);
    return groups;
  }, {});
  const rows = Object.entries(grouped);
  const create = async () => {
    if (!article) return;
    try { await api("/jobs", { method: "POST", body: JSON.stringify({ article_id: article }), headers: { "Idempotency-Key": crypto.randomUUID() } }); notify("Analysis job queued"); setArticle(""); load(); } catch (e) { notify(e.message, true); }
  };
  return <Page title="Jobs" subtitle="Queue and inspect durable article-analysis jobs." refresh={load}>
    <Panel title="Queue analysis"><div className="form-row"><input value={article} onChange={e => setArticle(e.target.value)} placeholder="Article UUID" /><button onClick={create} disabled={!article}>Queue job</button></div></Panel>
    <Panel title="Job history" actions={<div className="filter-row"><select value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option>{["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "RETRYING"].map(value => <option key={value}>{value}</option>)}</select><input value={article} onChange={e => setArticle(e.target.value)} placeholder="Filter article UUID" /><button className="secondary" onClick={load}>Apply</button><button className="secondary" onClick={() => setSelected(jobs.map(job => job.job_id))}>Select all</button><button className="secondary" onClick={() => setSelected([])}>Clear</button><button onClick={() => bulk("retry")} disabled={!selected.length}>Retry selected</button><button className="danger" onClick={() => bulk("delete")} disabled={!selected.length}>Delete selected</button><button className="danger" onClick={async () => { if (!window.confirm("Request cancellation for all queued and running jobs?")) return; try { const result = await api("/jobs/cancel-all", { method: "POST" }); notify(`Cancellation requested for ${result.queued_cancelled + result.running_cancel_requested} jobs`); load(); } catch (e) { notify(e.message, true); } }}>Stop all processing</button></div>}>
      {error ? <ErrorState error={error} retry={load} /> : jobs.length ? rows.map(([taskId, taskJobs]) => <details className="table-scroll" key={taskId} open><summary><strong>{taskId === "unassigned" ? "Unassigned jobs" : `Task ${taskId}`}</strong><span className="muted"> · {taskJobs.length} jobs</span></summary><table><thead><tr><th></th><th>Job</th><th>Article</th><th>Status</th><th>Stage</th><th>Created</th><th>Duration</th><th>Tokens in / out</th><th>Actions</th></tr></thead><tbody>{taskJobs.map(job => <tr key={job.job_id}><td><input type="checkbox" checked={selected.includes(job.job_id)} onChange={e => setSelected(current => e.target.checked ? [...current, job.job_id] : current.filter(id => id !== job.job_id))} /></td><td><button className="link-button" onClick={() => openJob(job.job_id)}>{shortId(job.job_id)}</button></td><td title={job.article_id}>{shortId(job.article_id)}</td><td><Status value={job.status} /></td><td>{job.current_stage || "—"}</td><td>{formatDate(job.created_at)}</td><td>{job.duration_ms != null ? formatDuration(job.duration_ms) : job.started_at ? formatDuration(Date.now() - new Date(job.started_at)) : "—"}</td><td>{formatTokens(job)}</td><td className="actions">{job.status === "RUNNING" || job.status === "PENDING" ? <button className="danger" onClick={() => action(job, "cancel")}>Cancel</button> : null}{["FAILED", "CANCELLED"].includes(job.status) ? <button onClick={() => action(job, "retry")}>Retry</button> : null}{["COMPLETED", "FAILED", "CANCELLED"].includes(job.status) ? <button className="danger" onClick={() => remove(job)}>Delete</button> : null}</td></tr>)}</tbody></table></details>) : <Empty>{error ? "Unable to load jobs." : "No jobs match the selected filters."}</Empty>}
    </Panel>
  </Page>;
}

function JobDetail({ jobId, back, notify }) {
  const [job, setJob] = useState(null);
  const [stages, setStages] = useState([]);
  const [logs, setLogs] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);
  const load = () => Promise.all([api(`/jobs/${jobId}`), api(`/jobs/${jobId}/stages`), api(`/logs?job_id=${jobId}&limit=100`), api(`/metrics/jobs?job_id=${jobId}`)]).then(([j, s, l, m]) => { setJob(j); setStages(s); setLogs(l); setMetrics(m.execution); }).catch(setError);
  useEffect(() => { load(); }, [jobId]);
  if (error) return <ErrorState error={error} retry={load} />;
  if (!job) return <Loading />;
  const llmCalls = logs.filter(log => log.event_type === "llm_call");
  const outputTokens = (metrics?.tokens?.output_measured || 0) + (metrics?.tokens?.output_estimated || 0);
  const inputTokens = (metrics?.tokens?.input_measured || 0) + (metrics?.tokens?.input_estimated || 0);
  const llmDuration = metrics?.summed_llm_busy_time_ms || 0;
  const createdRecords = logs.reduce((sum, log) => sum + (log.attributes?.created_records || log.attributes?.created_new || 0), 0);
  const retrievedRecords = logs.reduce((sum, log) => sum + (log.attributes?.retrieved_records || log.attributes?.retrieved_from_db || 0), 0);
  const telemetryStages = new Map();
  logs.filter(log => log.event_type === "pipeline_stage" || log.event_type === "llm_call").forEach(log => {
    const key = log.stage || log.operation;
    if (!key) return;
    const current = telemetryStages.get(key) || { stage: key, status: "PENDING", duration_ms: null, summary: {} };
    if (log.status === "started") {
      current.status = "RUNNING";
      current.started_at = log.started_at || log.timestamp;
    }
    if (log.status === "completed") {
      current.status = "COMPLETED";
      current.duration_ms = log.duration_ms;
      current.completed_at = log.completed_at || log.timestamp;
      current.summary = { ...current.summary, ...(log.attributes || {}) };
    }
    if (log.status === "failed") {
      current.status = "FAILED";
      current.duration_ms = log.duration_ms;
      current.completed_at = log.completed_at || log.timestamp;
      current.summary = { ...current.summary, ...(log.attributes || {}) };
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
      started_at: persisted?.started_at || telemetry?.started_at,
      completed_at: persisted?.completed_at || telemetry?.completed_at,
    };
  });
  const graphNodes = [
    ["article_retrieval", 0, 1, 0],
    ["citation_extraction", 1, 1, 0],
    ["citation_refinement", 2, 1, 0],
    ["extraction_persistence", 3, 1, 0],
    ["person_candidate_search", 4, 2, -1],
    ["topic_candidate_search", 4, 2, 1],
    ["person_resolution_decision", 5, 2, -1],
    ["topic_resolution_decision", 5, 2, 1],
    ["person_resolution", 6, 2, -1],
    ["topic_resolution", 6, 2, 1],
    ["citation_analysis", 7, 1, 0],
    ["speaker_analysis", 8, 1, 0],
    ["topic_stance_reevaluation", 9, 1, 0],
    ["attitude_persistence", 10, 1, 0],
    ["historical_precheck", 11, 1, 0],
    ["inconsistency_comparison", 12, 1, 0],
  ];
  const graphEdges = [
    ["article_retrieval", "citation_extraction"], ["citation_extraction", "citation_refinement"],
    ["citation_refinement", "extraction_persistence"], ["extraction_persistence", "person_candidate_search"],
    ["extraction_persistence", "topic_candidate_search"], ["person_candidate_search", "person_resolution_decision"],
    ["topic_candidate_search", "topic_resolution_decision"], ["person_resolution_decision", "person_resolution"],
    ["topic_resolution_decision", "topic_resolution"], ["person_resolution", "citation_analysis"],
    ["topic_resolution", "citation_analysis"], ["citation_analysis", "speaker_analysis"],
    ["speaker_analysis", "topic_stance_reevaluation"], ["topic_stance_reevaluation", "attitude_persistence"],
    ["attitude_persistence", "historical_precheck"], ["historical_precheck", "inconsistency_comparison"],
  ];
  const graphByKey = new Map(stageRows.map(stage => [stage.stage, stage]));
  const graphLabel = key => graphByKey.get(key)?.label || key.replaceAll("_", " ");
  const graphStatus = key => graphByKey.get(key)?.status || "PENDING";
  const graphX = column => 34 + column * 116;
  const graphY = lane => 54 + lane * 82;
  const graphPoint = (key, edge = false) => {
    const node = graphNodes.find(item => item[0] === key);
    return node ? { x: graphX(node[1]) + (edge ? 0 : 92), y: graphY(node[3] + 1) + 25 } : null;
  };
  const stageGraph = <div className="pipeline-graph" role="img" aria-label="Pipeline execution graph">
    <svg viewBox="0 0 1490 220" preserveAspectRatio="xMinYMin meet">
      <defs><marker id="pipeline-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <g className="pipeline-edges">{graphEdges.map(([from, to]) => { const start = graphPoint(from); const end = graphPoint(to, true); return <line key={`${from}-${to}`} x1={start?.x} y1={start?.y} x2={end?.x} y2={end?.y} />; })}</g>
      <g>{graphNodes.map(([key, column, , lane]) => { const stage = graphByKey.get(key); const status = graphStatus(key).toLowerCase().replaceAll("_", "-"); return <g className={`pipeline-node ${status}`} key={key} transform={`translate(${graphX(column)},${graphY(lane + 1)})`}><rect width="92" height="50" rx="6" /><text x="46" y="19">{graphLabel(key).split(" ").slice(0, 2).join(" ")}</text><text x="46" y="35" className="pipeline-node-status">{stage?.status || "PENDING"}</text></g>; })}</g>
    </svg>
    <div className="pipeline-legend"><span><i className="graph-dot completed" />Completed</span><span><i className="graph-dot running" />Running</span><span><i className="graph-dot failed" />Failed</span><span><i className="graph-dot pending" />Pending / skipped</span></div>
  </div>;
  const elapsed = metrics?.elapsed_time_ms ?? metrics?.job_wall_clock_duration_ms;
  return <Page title={`Job ${shortId(job.job_id)}`} subtitle={`Article ${job.article_id}`} refresh={load}><button className="secondary" onClick={back}>← Back to jobs</button>
    <section className="metric-grid compact"><MetricCard label="Elapsed" value={formatDuration(elapsed)} /><MetricCard label="Estimated total" value={formatDuration(metrics?.estimated_total_time_ms)} detail={metrics?.estimate_source || "Unavailable"} /><MetricCard label="Remaining" value={formatDuration(metrics?.remaining_time_ms)} /><MetricCard label="Actual tokens in / out" value={formatTokens({ input_tokens: metrics?.tokens?.input_measured, output_tokens: metrics?.tokens?.output_measured, input_tokens_estimated: metrics?.tokens?.input_estimated, output_tokens_estimated: metrics?.tokens?.output_estimated, token_source: metrics?.tokens?.input_source === "measured" || metrics?.tokens?.output_source === "measured" ? "measured" : metrics?.tokens?.input_source === "estimated" || metrics?.tokens?.output_source === "estimated" ? "estimated" : "none" })} /><MetricCard label="Queue wait" value={formatDuration(metrics?.queue_wait_ms)} /><MetricCard label="Critical path" value={formatDuration(metrics?.critical_path_duration_ms)} /><MetricCard label="LLM busy time" value={formatDuration(llmDuration)} /><MetricCard label="Retries / timeouts" value={`${formatNumber(metrics?.retry_count)} / ${formatNumber(metrics?.timeout_count)}`} /><MetricCard label="DB created / retrieved" value={`${createdRecords} / ${retrievedRecords}`} /></section>
    <div className="detail-grid"><Panel title="Lifecycle"><dl><dt>Status</dt><dd><Status value={job.status} /></dd><dt>Created</dt><dd>{formatDate(job.created_at)}</dd><dt>Started</dt><dd>{formatDate(job.started_at)}</dd><dt>Completed</dt><dd>{formatDate(job.completed_at)}</dd><dt>Correlation</dt><dd className="mono">{job.correlation_id}</dd><dt>Trace</dt><dd className="mono">{job.trace_id}</dd><dt>Error</dt><dd>{job.error_summary || "—"}</dd></dl></Panel>
      <Panel title="Pipeline execution graph" kicker="Branches share a column when stages can execute in parallel; muted nodes are not started">{stageGraph}</Panel></div>
    <Panel title="Related LLM calls" kicker="Model calls correlated to this job"><CallTable calls={llmCalls} /></Panel><Panel title="Related logs" kicker="Structured records filtered by job correlation"><LogTable logs={logs} /></Panel>
  </Page>;
}

function LLMMonitoring() {
  const [metrics, setMetrics] = useState(null);
  const [calls, setCalls] = useState([]);
  const [error, setError] = useState(null);
  const load = () => Promise.all([api("/metrics/llm"), api("/logs?limit=500")]).then(([m, l]) => { setMetrics(m); setCalls(l.filter(event => event.event_type === "llm_call")); }).catch(setError);
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState error={error} retry={load} />;
  return <Page title="LLM Monitoring" subtitle="Measured provider telemetry, with unavailable values left explicit." refresh={load}>
    <section className="metric-grid compact"><MetricCard label="Requests" value={formatNumber(metrics?.call_count)} detail={`${formatNumber(metrics?.active_call_count)} active now`} /><MetricCard label="Input tokens" value={formatNumber(metrics?.input_tokens_measured || metrics?.input_tokens_estimated)} detail={metrics?.token_source?.input || "Unavailable"} /><MetricCard label="Output tokens" value={formatNumber(metrics?.output_tokens_measured || metrics?.output_tokens_estimated)} detail={metrics?.token_source?.output || "Unavailable"} /><MetricCard label="P50 / average" value={`${formatDuration(metrics?.latency_ms?.p50)} / ${formatDuration(metrics?.latency_ms?.average)}`} /><MetricCard label="P95 / P99 latency" value={`${formatDuration(metrics?.latency_ms?.p95)} / ${formatDuration(metrics?.latency_ms?.p99)}`} /><MetricCard label="Retries / timeouts" value={`${formatNumber(metrics?.retry_count)} / ${formatNumber(metrics?.timeout_count)}`} /><MetricCard label="Errors" value={formatNumber(metrics?.failed_calls)} /></section>
    <Panel title="LLM call inspector" kicker={`${calls.length} retained structured events`}><CallTable calls={calls} /><p className="muted">If no calls appear, verify LLM_URL and MANAGEMENT_EVENTS_URL, then run a new job; retained telemetry is not backfilled from plain logs.</p></Panel>
  </Page>;
}

function CallTable({ calls }) {
  const [query, setQuery] = useState("");
  const sorted = mergeTelemetryEvents(calls).filter(call => JSON.stringify(call).toLowerCase().includes(query.toLowerCase())).sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));
  return <><div className="filter-row"><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search model, stage, trace…" /></div><div className="table-scroll"><table><thead><tr><th>Status</th><th>Stage</th><th>Model</th><th>Duration</th><th>Tokens in / out</th><th>Trace</th></tr></thead><tbody>{sorted.slice(0, 200).map(call => <tr key={call.event_id}><td><Status value={call.status} /></td><td>{call.stage || "—"}</td><td>{call.model || "Unavailable"}</td><td>{formatDuration(call.duration_ms)}</td><td>{call.input_tokens == null && call.input_tokens_estimated == null ? "Unavailable" : `${call.input_tokens == null ? "~" : ""}${call.input_tokens ?? call.input_tokens_estimated} / ${call.output_tokens == null ? (call.output_tokens_estimated == null ? "—" : "~" + call.output_tokens_estimated) : call.output_tokens}`}</td><td className="mono">{shortId(call.trace_id)}</td></tr>)}</tbody></table></div>{!sorted.length && <Empty />}</>;
}

function mergeTelemetryEvents(logs) {
  const groups = new Map();
  logs.forEach(log => {
    const key = log.span_id || log.event_id || `${log.timestamp}-${log.event_type}`;
    const group = groups.get(key) || { ...log, events: [] };
    group.events.push(log);
    const completed = log.status === "completed" || log.status === "failed";
    if (completed || !group.status) Object.assign(group, log);
    if (log.status === "started" && !group.started_at) group.started_at = log.started_at || log.timestamp;
    if (completed) {
      group.completed_at = log.completed_at || log.timestamp;
      group.duration_ms = log.duration_ms ?? group.duration_ms;
    }
    groups.set(key, group);
  });
  return [...groups.values()].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
}

function LogTable({ logs }) {
  const [expanded, setExpanded] = useState(null);
  const rows = mergeTelemetryEvents(logs);
  return rows.length ? <div className="table-scroll"><table><thead><tr><th></th><th>Time</th><th>Severity</th><th>Service</th><th>Event</th><th>Stage</th><th>Duration</th><th>Details</th></tr></thead><tbody>{rows.map(log => <React.Fragment key={log.span_id || log.event_id}><tr><td>{log.events.length > 1 && <button className="table-sort" onClick={() => setExpanded(expanded === (log.span_id || log.event_id) ? null : (log.span_id || log.event_id))}>{expanded === (log.span_id || log.event_id) ? "−" : "+"}</button>}</td><td>{formatDate(log.timestamp)}</td><td><Status value={log.level || log.status} /></td><td>{log.service}</td><td>{log.event_type}</td><td>{log.stage || "—"}</td><td>{formatDuration(log.duration_ms)}</td><td className="log-detail">{log.error_detail || JSON.stringify(log.attributes || {})}</td></tr>{expanded === (log.span_id || log.event_id) && <tr><td colSpan="8"><div className="log-history">{log.events.map(event => <div key={event.event_id || event.status}><Status value={event.status} /> <span>{formatDate(event.timestamp)}</span> <span>{event.status === "completed" ? formatDuration(event.duration_ms) : ""}</span></div>)}</div></td></tr>}</React.Fragment>)}</tbody></table></div> : <Empty />;
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
  useEffect(() => { load(); }, []);
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
  const flush = async () => {
    if (!window.confirm("Delete all management tasks, jobs, telemetry, experiments, and logs?")) return;
    try { await api("/database/flush", { method: "POST" }); notify("Management database flushed"); } catch (e) { notify(e.message, true); }
  };
  return <Page title="Configuration" subtitle="Runtime settings and testing database controls." refresh={load}>{error ? <ErrorState error={error} retry={load} /> : <><Panel title="Runtime settings" kicker="Effective values from the management API"><form onSubmit={save} className="settings-form"><label>Request timeout (seconds)<input type="number" min="0.1" max="120" step="0.1" value={form.request_timeout_seconds ?? ""} onChange={e => setForm({ ...form, request_timeout_seconds: e.target.value })} /></label><label>Analysis timeout (seconds)<input type="number" min="0.1" max="7200" step="0.1" value={form.analysis_timeout_seconds ?? ""} onChange={e => setForm({ ...form, analysis_timeout_seconds: e.target.value })} /></label><button type="submit">Save temporary settings</button></form><p className="muted">Secrets, credentials, environment variables, and model endpoints are intentionally not exposed.</p><p className="muted">Supported fields: {settings?.configurable_fields?.join(", ") || "Unavailable"}</p></Panel><Panel title="Testing database controls" kicker="Destructive operations are intended for the testing database"><div className="filter-row"><button onClick={() => runDatabase("create")}>Create missing tables</button><button className="danger" onClick={() => runDatabase("recreate")}>Drop & recreate database</button><button className="secondary" onClick={importTemp}>Import temporary articles</button><button className="danger" onClick={flush}>Flush management tasks, jobs, statistics, and logs</button></div></Panel></>}</Page>;
}

function Tasks({ notify, openJob }) {
  const [tasks, setTasks] = useState([]);
  const [articles, setArticles] = useState([]);
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState("Fresh articles");
  const [detail, setDetail] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [tab, setTab] = useState("history");
  const [articleStatus, setArticleStatus] = useState("");
  const [error, setError] = useState(null);
  const load = async () => {
    try {
      const articleQuery = articleStatus ? `&status=${encodeURIComponent(articleStatus)}` : "";
      const [taskData, articleData] = await Promise.all([api("/tasks?limit=50"), api(`/articles?limit=100${articleQuery}`)]);
      setTasks(taskData); setArticles(articleData);
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); }, [articleStatus]);
  useEffect(() => {
    if (!detail?.task_id) { setAnalytics(null); return undefined; }
    const loadAnalytics = () => api(`/tasks/${detail.task_id}/analytics`).then(setAnalytics).catch(() => setAnalytics(null));
    loadAnalytics();
    const timer = setInterval(loadAnalytics, 5000);
    return () => clearInterval(timer);
  }, [detail?.task_id]);
  const create = async (ids, redo = false) => {
    try {
      const task = await api("/tasks", { method: "POST", body: JSON.stringify({ name, article_ids: ids, redo }) });
      notify(`Task queued: ${ids.length} articles`); setSelected([]); setDetail(task); load();
    } catch (e) { notify(e.message, true); }
  };
  const fresh = async () => {
    try { const task = await api(`/tasks/fresh?status=${encodeURIComponent(articleStatus || "NEW")}&limit=500`, { method: "POST" }); notify("Fresh articles task queued"); setDetail(task); load(); }
    catch (e) { notify(e.message, true); }
  };
  useTelemetryStream(React.useCallback(event => {
    if (event.job_id && detail?.job_ids?.includes(event.job_id)) api(`/tasks/${detail.task_id}`).then(setDetail).catch(() => {});
  }, [detail]));
  const toggleAll = checked => setSelected(checked ? articles.map(article => article.article_id) : []);
  return <Page title="Tasks" subtitle="Control multi-article processing and watch aggregate analytical KPIs." refresh={load}>
    <div className="browser-tabs"><button className={tab === "history" ? "active" : "secondary"} onClick={() => setTab("history")}>Existing tasks</button><button className={tab === "create" ? "active" : "secondary"} onClick={() => setTab("create")}>Create task</button></div>
    {tab === "create" && <Panel title="Start processing" actions={<div className="filter-row"><select value={articleStatus} onChange={e => setArticleStatus(e.target.value)}><option value="">All statuses</option>{["NEW", "INGESTED", "CITATIONS_EXTRACTED", "ANALYZED", "COMPLETED", "FAILED", "CANCELLED"].map(value => <option key={value}>{value}</option>)}</select><button onClick={fresh}>Process all {articleStatus || "new"} articles</button></div>}>
      <div className="form-row"><input value={name} onChange={e => setName(e.target.value)} placeholder="Task name" /><button className="secondary" onClick={() => toggleAll(true)}>Select all visible</button><button className="secondary" onClick={() => toggleAll(false)}>Clear selection</button><button disabled={!selected.length} onClick={() => create(selected)}>Process selected ({selected.length})</button><button className="secondary" disabled={!selected.length} onClick={() => create(selected, true)}>Redo selected ({selected.length})</button></div>
      <div className="table-scroll"><table><thead><tr><th><input type="checkbox" checked={articles.length > 0 && selected.length === articles.length} onChange={e => toggleAll(e.target.checked)} /></th><th>Article</th><th>Title</th><th>Status</th></tr></thead><tbody>{articles.map(article => <tr key={article.article_id}><td><input type="checkbox" checked={selected.includes(article.article_id)} onChange={e => setSelected(e.target.checked ? [...selected, article.article_id] : selected.filter(id => id !== article.article_id))} /></td><td className="mono">{shortId(article.article_id)}</td><td>{article.article_title || "Untitled"}</td><td>{article.status || "NEW"}</td></tr>)}</tbody></table></div>
    </Panel>}
    {tab === "history" && <Panel title="Task history">{error ? <ErrorState error={error} retry={load} /> : tasks.length ? <div className="table-scroll"><table><thead><tr><th>Task</th><th>Status</th><th>Progress</th><th>Elapsed / remaining</th><th>Tokens in / out</th><th>KPIs</th><th>Created</th></tr></thead><tbody>{tasks.map(task => <tr key={task.task_id}><td><button className="link-button" onClick={() => setDetail(task)}>{task.name}</button><small className="mono">{shortId(task.task_id)}</small></td><td><Status value={task.status} /></td><td>{task.completed_articles}/{task.total_articles} complete · {task.failed_articles} failed</td><td>{formatDuration(task.elapsed_time_ms)} / {formatDuration(task.remaining_time_ms)}</td><td>{formatTokens(task)}</td><td>{task.citations} citations · {task.topics} topics · {task.attitudes} attitudes</td><td>{formatDate(task.created_at)}</td></tr>)}</tbody></table></div> : <Empty>No tasks have been created.</Empty>}</Panel>}
    {detail && <Panel title={detail.name} actions={<div className="filter-row">{["PENDING", "RUNNING"].includes(detail.status) && <button className="danger" onClick={async () => { if (!window.confirm("Request cancellation for this task?")) return; try { setDetail(await api(`/tasks/${detail.task_id}/cancel`, { method: "POST" })); notify("Task cancellation requested"); } catch (e) { notify(e.message, true); } }}>Cancel task</button>}{!["PENDING", "RUNNING", "RETRYING"].includes(detail.status) && <button className="danger" onClick={async () => { if (!window.confirm("Delete this task and all related job attempts?")) return; try { await api(`/tasks/${detail.task_id}`, { method: "DELETE" }); notify("Task and related jobs deleted"); setDetail(null); load(); } catch (e) { notify(e.message, true); } }}>Delete task</button>}<button className="secondary" onClick={() => setDetail(null)}>Close</button></div>}><div className="metric-grid compact"><MetricCard label="Status" value={detail.status} /><MetricCard label="Articles" value={`${detail.completed_articles}/${detail.total_articles}`} /><MetricCard label="Elapsed" value={formatDuration(detail.elapsed_time_ms)} /><MetricCard label="Estimated total" value={formatDuration(detail.estimated_total_time_ms)} detail={detail.estimate_source || "Unavailable"} /><MetricCard label="Remaining" value={formatDuration(detail.remaining_time_ms)} /><MetricCard label="Actual tokens in / out" value={formatTokens(detail)} detail={detail.token_source} /><MetricCard label="Retry rate" value={`${((detail.retry_rate || 0) * 100).toFixed(1)}%`} /><MetricCard label="Citations" value={formatNumber(detail.citations)} /><MetricCard label="Topics" value={formatNumber(detail.topics)} /><MetricCard label="Attitudes" value={formatNumber(detail.attitudes)} /></div><p className="muted">Estimates use the observed task completion rate or a weighted moving average of recent jobs.</p><div className="timeline">{analytics?.stages?.map(stage => <div className="timeline-row" key={stage.stage}><strong>{stage.stage}</strong><span>{formatDuration(stage.duration_ms)}</span><span>{stage.events} events · {stage.citations} citations · {stage.topics} topics · {stage.people} people · {stage.attitudes} attitudes</span><small>{stage.created_records} created / {stage.retrieved_records} retrieved</small></div>) || <Empty>Stage analytics will appear when telemetry arrives.</Empty>}</div><TaskJobsTable taskId={detail.task_id} openJob={openJob} notify={notify} /></Panel>}
  </Page>;
}

function TaskJobsTable({ taskId, openJob, notify }) {
  const [jobs, setJobs] = useState([]);
  const load = () => api(`/jobs?task_id=${taskId}&limit=500`).then(setJobs).catch(error => notify(error.message, true));
  useEffect(() => { load(); }, [taskId]);
  const action = async (job, operation) => {
    try { await api(`/jobs/${job.job_id}/${operation}`, { method: "POST" }); notify(`${operation} requested`); load(); } catch (e) { notify(e.message, true); }
  };
  return <div className="table-scroll"><h3>Jobs and retry attempts</h3><table><thead><tr><th>Job</th><th>Article</th><th>Status</th><th>Retries</th><th>Created</th><th>Actions</th></tr></thead><tbody>{jobs.map(job => <tr key={job.job_id}><td><button className="link-button" onClick={() => openJob(job.job_id)}>{shortId(job.job_id)}</button></td><td className="mono">{shortId(job.article_id)}</td><td><Status value={job.status} /></td><td>{job.retry_count}</td><td>{formatDate(job.created_at)}</td><td className="actions">{["FAILED", "CANCELLED"].includes(job.status) && <button onClick={() => action(job, "retry")}>Retry</button>}{["PENDING", "RUNNING"].includes(job.status) && <button className="danger" onClick={() => action(job, "cancel")}>Cancel</button>}</td></tr>)}</tbody></table>{!jobs.length && <Empty>No jobs are linked to this task.</Empty>}</div>;
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
  articles: ["NEW", "INGESTED", "CITATIONS_EXTRACTED", "ANALYZED", "COMPLETED", "FAILED", "CANCELLED"],
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
  const [queryField, setQueryField] = useState("all");
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
      if (queryField !== "all") params.set("query_field", queryField);
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
  const searchFields = {
    articles: [["all", "All fields"], ["article_id", "Article UUID"], ["article_title", "Title"], ["url", "URL"]],
    people: [["all", "All fields"], ["person_uuid", "Person UUID"], ["name", "Name"]],
    citations: [["all", "All fields"], ["citation_uuid", "Citation UUID"], ["person_uuid", "Person UUID"], ["origin_article_id", "Article UUID"], ["exact_quote", "Exact quote"]],
    topics: [["all", "All fields"], ["topic_uuid", "Topic UUID"], ["topic_name", "Topic name"]],
    attitudes: [["all", "All fields"], ["topic_uuid", "Topic UUID"], ["citation_uuid", "Citation UUID"], ["person_uuid", "Person UUID"], ["origin_article_id", "Article UUID"], ["person_name", "Person name"], ["topic_name", "Topic name"]],
    inconsistencies: [["all", "All fields"], ["inconsistency_uuid", "Inconsistency UUID"], ["person_uuid", "Person UUID"], ["topic_uuid", "Topic UUID"], ["citation_a_uuid", "Citation A UUID"], ["citation_b_uuid", "Citation B UUID"], ["person_name", "Person name"], ["topic_name", "Topic name"]],
  };
  return <Page title="Data management" subtitle="Browse, inspect, and edit the analytical data." refresh={() => load(page)}>
    <section className="metric-grid compact">{dataResources.map(([key, label]) => <MetricCard key={key} label={label} value={formatNumber(summary?.entities?.[key])} detail="Stored records" />)}</section>
    <Panel title="Use case" kicker="Choose the entity you want to inspect"><div className="resource-tabs">{dataResources.map(([key, label]) => <button className={resource === key ? "active" : "secondary"} key={key} onClick={() => { loadSequence.current += 1; setResource(key); setDetail(null); setStatus(""); setCategory(""); setCategoryValue(""); setPersonFilter(""); setTopicFilter(""); setQuery(""); setQueryField("all"); setSemantic(current => ({ ...current, query: "" })); }}>{label}</button>)}</div></Panel>
    {["attitudes", "inconsistencies"].includes(resource) && <div className="filter-row relation-filters"><input value={personFilter} onChange={e => setPersonFilter(e.target.value)} placeholder="Search person…" /><input value={topicFilter} onChange={e => setTopicFilter(e.target.value)} placeholder="Search topic…" /><button className="secondary" onClick={() => { setPersonFilter(""); setTopicFilter(""); load(1); }}>Clear relation filters</button></div>}
    <Panel title={`${resourceLabel(resource)} browser`} actions={<div className="filter-row"><select value={queryField} onChange={e => setQueryField(e.target.value)} aria-label="Search field">{searchFields[resource].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><input value={query} onChange={e => setQuery(e.target.value)} placeholder={`Search ${resourceLabel(resource).toLowerCase()}`} /><button onClick={() => load(1)}>Search</button>{["citations", "topics"].includes(resource) && <><input value={semantic.query} onChange={e => setSemantic(current => ({ ...current, query: e.target.value }))} onKeyDown={e => e.key === "Enter" && runSemanticSearch()} placeholder={`Semantic ${resource === "citations" ? "citation" : "topic"} search`} /><input type="number" min="-1" max="1" step="0.01" value={minSimilarity} onChange={e => setMinSimilarity(e.target.value)} title="Minimum cosine similarity" /><button onClick={runSemanticSearch} disabled={semantic.query.trim().length < 2}>Search meaning</button></>}</div>}>
      {error ? <ErrorState error={error} retry={() => load(page)} /> : data.items.length ? <><div className="table-scroll"><table><thead><tr>{columns.map(column => <th key={column}>{categoryFields[resource].includes(column) ? (category === column ? <select value={categoryValue} onChange={e => setCategoryValue(e.target.value)} onClick={e => e.stopPropagation()}><option value="">All {column.replaceAll("_", " ")}</option>{categoryValues.map(value => <option key={value} value={value}>{value}</option>)}</select> : <button className="table-sort" onClick={() => selectCategory(column)}>{column.replaceAll("_", " ")}{category === column && categoryValue ? `: ${categoryValue}` : ""}</button>) : <button className="table-sort" onClick={() => toggleSort(column)}>{column.replaceAll("_", " ")}{sortIndicator(column)}</button>}</th>)}<th>Action</th></tr></thead><tbody>{sortedItems.map((record, index) => <tr key={dataId(resource, record) || index}>{columns.map(column => { const numericStance = ["stance_average", "stance_std", "stance_median"].includes(column); const value = record[column]; const scoreClass = column === "stance_std" ? "variability" : Number(value) < 0 ? "negative" : Number(value) > 0 ? "positive" : "neutral"; return <td key={column} title={String(value ?? "")} className={numericStance && value != null ? `stance-score ${scoreClass}` : ""}>{resource === "inconsistencies" && column.endsWith("_uuid") && value ? <CopyIdentifier value={value} notify={notify} /> : numericStance ? formatNumber(value, 2) : String(value ?? "—").slice(0, 180)}</td>; })}<td><button className="link-button" onClick={() => open(record)}>View / edit</button></td></tr>)}</tbody></table></div><div className="pagination"><button disabled={page <= 1} onClick={() => load(page - 1)}>Previous</button><span>Page {page} of {Math.max(data.pages, 1)} · {data.total} records</span><button disabled={page >= data.pages} onClick={() => load(page + 1)}>Next</button></div></> : <Empty>No {resource} records found.</Empty>}
    </Panel>
    {resource === "attitudes" && <Panel title="Attitude stance KPIs"><section className="metric-grid compact">{Object.entries(data.stance_counts || {}).map(([stance, count]) => <MetricCard key={stance} label={stance} value={formatNumber(count)} detail="Matching attitudes" />)}</section></Panel>}
    {detail && <DataDetail resource={resource} detail={detail} onClose={() => setDetail(null)} onSaved={() => { setDetail(null); load(page); }} notify={notify} />}
  </Page>;
}

function Experiments({ notify }) {
  const [runs, setRuns] = useState([]);
  const [models, setModels] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [form, setForm] = useState({ experiment_type: "EXP-01", dataset: "synthetic", model_ids: [], repetitions: "1", alternative_temperature: "0.7" });
  const [error, setError] = useState(null);
  const [selectedRun, setSelectedRun] = useState(null);
  const load = async () => {
    try {
      const [runData, modelData, datasetData] = await Promise.all([api("/benchmarks/runs"), api("/benchmarks/models"), api("/benchmarks/datasets")]);
      setRuns(runData); setModels(modelData); setDatasets(datasetData);
      if (!form.model_ids.length && modelData.length) setForm(current => ({ ...current, model_ids: modelData.map(item => item.id) }));
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!selectedRun || !["RUNNING", "CREATED"].includes(selectedRun.status)) return undefined;
    let active = true;
    const poll = async () => {
      try {
        const detail = await api(`/benchmarks/runs/${selectedRun.run_id}`);
        if (active) { setSelectedRun(detail); setRuns(current => current.map(item => item.run_id === detail.run_id ? { ...item, ...detail, results: [] } : item)); }
      } catch (e) { if (active) notify(e.message, true); }
    };
    const timer = window.setInterval(poll, 2000);
    poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedRun?.run_id, selectedRun?.status]);
  const create = async event => {
    event.preventDefault();
    try {
      const configurations = form.experiment_type === "EXP-02" && form.model_ids[0]
        ? [{ model_id: models.find(item => item.id === form.model_ids[0])?.model, temperature: 0 }, { model_id: models.find(item => item.id === form.model_ids[0])?.model, temperature: Number(form.alternative_temperature) }]
        : [];
      await api("/benchmarks/runs", { method: "POST", body: JSON.stringify({ experiment_type: form.experiment_type, dataset: form.dataset, model_ids: form.model_ids, repetitions: Number(form.repetitions), configurations }) });
      notify("Benchmark run created"); load();
    } catch (e) { notify(e.message, true); }
  };
  const start = async run => { try { const started = await api(`/benchmarks/runs/${run.run_id}/start`, { method: "POST" }); setSelectedRun(started); notify("Benchmark started"); load(); } catch (e) { notify(e.message, true); } };
  const inspect = async run => { try { setSelectedRun(await api(`/benchmarks/runs/${run.run_id}`)); } catch (e) { notify(e.message, true); } };
  const percent = selectedRun?.progress?.percent || 0;
  const detailResults = selectedRun?.results || [];
  const metric = (name, digits = 3) => selectedRun?.summary?.[name] == null ? "—" : formatNumber(selectedRun.summary[name], digits);
  const resultRows = detailResults.slice().reverse();
  return <Page title="Benchmarks" subtitle="Isolated model, configuration, and stability evaluation." refresh={load}>
    <Panel title="Create benchmark run" kicker="Synthetic labels are predefined; real-news results are exploratory"><form className="experiment-form" onSubmit={create}><label>Experiment type<select value={form.experiment_type} onChange={e => setForm({ ...form, experiment_type: e.target.value })}><option value="EXP-01">EXP-01 — model comparison</option><option value="EXP-02">EXP-02 — configuration comparison</option><option value="EXP-03">EXP-03 — stability testing</option></select></label><label>Dataset<select value={form.dataset} onChange={e => setForm({ ...form, dataset: e.target.value })}>{datasets.map(item => <option key={item.id} value={item.id}>{item.id} ({item.case_count})</option>)}</select></label><label>Repetitions<input type="number" min="1" max="3" value={form.repetitions} onChange={e => setForm({ ...form, repetitions: e.target.value })} /></label>{form.experiment_type === "EXP-02" && <label>Alternative temperature<input type="number" min="0" max="2" step="0.1" value={form.alternative_temperature} onChange={e => setForm({ ...form, alternative_temperature: e.target.value })} /></label>}<label>Models<select multiple value={form.model_ids} onChange={e => setForm({ ...form, model_ids: [...e.target.selectedOptions].map(option => option.value) })}>{models.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}</select><small className="field-help">Ctrl/Cmd-click to select multiple models.</small></label><button type="submit" disabled={!form.model_ids.length}>Create run</button></form></Panel>
    <Panel title="Benchmark runs" actions={<span className="muted">{runs.length} saved runs</span>}>{error ? <ErrorState error={error} retry={load} /> : runs.length ? <div className="table-scroll"><table><thead><tr><th>Run</th><th>Type</th><th>Dataset</th><th>Status</th><th>Progress</th><th>Summary</th><th>Actions</th></tr></thead><tbody>{runs.map(run => <tr key={run.run_id}><td><button className="link-button" onClick={() => inspect(run)}>{run.run_id}</button><small className="mono">{formatDate(run.created_at)}</small></td><td>{run.experiment_type}</td><td>{run.dataset}</td><td><Status value={run.status} /></td><td>{run.progress?.total ? `${run.progress.completed}/${run.progress.total} (${run.progress.percent}%)` : "—"}</td><td>{run.summary?.cases ? `${run.summary.cases} cases · ${formatDuration(run.summary.average_execution_time_ms)}` : "—"}</td><td>{run.status === "CREATED" || run.status === "FAILED" ? <button onClick={() => start(run)}>Start</button> : <button className="secondary" onClick={() => inspect(run)}>View</button>}</td></tr>)}</tbody></table></div> : <Empty>No benchmark runs have been created.</Empty>}</Panel>
    {selectedRun && <Panel title={`Run ${selectedRun.run_id}`} kicker={`${selectedRun.experiment_type} · ${selectedRun.dataset}`} actions={<div className="filter-row"><button className="secondary" onClick={() => window.open(`/api/benchmarks/runs/${selectedRun.run_id}`, "_blank")}>Open JSON</button><button className="secondary" onClick={() => setSelectedRun(null)}>Close</button></div>}>
      <div className="benchmark-progress"><div className="progress-header"><Status value={selectedRun.status} /><strong>{selectedRun.progress?.completed || 0} / {selectedRun.progress?.total || "?"} cases</strong><span>{percent}%</span></div><div className="progress-track"><i style={{ width: `${percent}%` }} /></div>{selectedRun.progress?.current_test_id && <small>Latest: {selectedRun.progress.current_test_id} · {selectedRun.progress.current_model_id}</small>}{selectedRun.error && <div className="error-state">{selectedRun.error}</div>}</div>
      <section className="metric-grid compact"><MetricCard label="Average time" value={formatDuration(selectedRun.summary?.average_execution_time_ms)} detail="Measured model calls" /><MetricCard label="JSON validity" value={`${Math.round((selectedRun.summary?.json_validity_rate || 0) * 100)}%`} detail="Valid JSON responses" /><MetricCard label="Schema compliance" value={`${Math.round((selectedRun.summary?.schema_compliance_rate || 0) * 100)}%`} detail="Required output fields" /><MetricCard label="Failures" value={`${Math.round((selectedRun.summary?.failure_rate || 0) * 100)}%`} detail="Invalid or failed outputs" /><MetricCard label="Median time" value={formatDuration(selectedRun.summary?.median_execution_time_ms)} detail="Per case" /><MetricCard label="Stability" value={selectedRun.summary?.stability?.agreement_rate == null ? "—" : `${Math.round(selectedRun.summary.stability.agreement_rate * 100)}%`} detail="Repeated output agreement" /></section>
      <div className="benchmark-detail-grid"><div><h3>Quality overview</h3>{[["Citation F1","citation_extraction","f1"],["Person F1","person_identification","f1"],["Topic F1","topic_identification","f1"],["Stance MAE","stance_estimation","mae"]].map(([label, group, key]) => { const values = resultRows.map(item => item.evaluation?.[group]?.[key]).filter(value => typeof value === "number"); const value = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null; const width = key === "mae" ? Math.max(0, 100 - (value || 0) * 100) : (value || 0) * 100; return <div className="benchmark-bar" key={label}><span>{label}</span><div><i style={{ width: `${Math.min(100, width)}%` }} /></div><b>{value == null ? "—" : formatNumber(value, 3)}</b></div>; })}</div><div><h3>Configuration</h3><pre className="json-preview">{JSON.stringify({ model_ids: selectedRun.model_ids, repetitions: selectedRun.repetitions, prompt_version: selectedRun.prompt_version, configurations: selectedRun.configurations }, null, 2)}</pre></div></div>
      {resultRows.length > 0 && <><h3>Case results and responses</h3><div className="table-scroll benchmark-results"><table><thead><tr><th>Case</th><th>Model</th><th>Rep.</th><th>Time</th><th>JSON</th><th>Citation F1</th><th>Person F1</th><th>Topic F1</th><th>Raw / parsed</th></tr></thead><tbody>{resultRows.map((item, index) => <tr key={`${item.test_id}-${index}`}><td>{item.test_id}</td><td className="mono">{item.model_id}</td><td>{item.repetition || 1}</td><td>{formatDuration(item.evaluation?.performance?.execution_time_ms)}</td><td><Status value={item.evaluation?.structured_output?.schema_compliant ? "VALID" : "INVALID"} /></td><td>{formatNumber(item.evaluation?.citation_extraction?.f1, 3)}</td><td>{formatNumber(item.evaluation?.person_identification?.f1, 3)}</td><td>{formatNumber(item.evaluation?.topic_identification?.f1, 3)}</td><td><details><summary>Inspect</summary><div className="response-grid"><div><b>Input</b><pre className="json-preview">{JSON.stringify(item.input, null, 2)}</pre></div><div><b>Parsed response</b><pre className="json-preview">{JSON.stringify(item.parsed_response, null, 2)}</pre></div><div><b>Raw response</b><pre className="json-preview">{JSON.stringify(item.raw_response, null, 2)}</pre></div></div></details></td></tr>)}</tbody></table></div></>}
    </Panel>}
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
    ["experiments", "Benchmarks"],
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
