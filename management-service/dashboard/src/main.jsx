import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

const api = (path, options) => fetch(`/api${path}`, options).then(async response => {
  if (!response.ok) throw new Error(await response.text());
  return response.json();
});

function Status({ value }) {
  return <span className={`status ${String(value).toLowerCase()}`}>{value}</span>;
}

function Overview() {
  const [services, setServices] = useState([]);
  const [summary, setSummary] = useState({ jobs: {}, events: {} });
  const [jobs, setJobs] = useState([]);
  const [articleId, setArticleId] = useState("");
  const [logs, setLogs] = useState([]);
  const [message, setMessage] = useState("");
  const refresh = () => Promise.all([api("/services"), api("/telemetry/summary"), api("/jobs?limit=20")])
    .then(([s, m, j]) => { setServices(s); setSummary(m); setJobs(j); });
  useEffect(() => { refresh(); const timer = setInterval(refresh, 5000); return () => clearInterval(timer); }, []);
  const submit = async event => {
    event.preventDefault();
    try {
      await api("/jobs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ article_id: articleId }) });
      setArticleId(""); setMessage("Job queued."); refresh();
    } catch (error) { setMessage(error.message); }
  };
  const action = async (id, operation) => { await api(`/jobs/${id}/${operation}`, { method: "POST" }); refresh(); };
  const loadLogs = () => api("/logs?limit=100").then(setLogs).catch(error => setMessage(error.message));
  return <main>
    <h1>NewsAnalysis Management</h1>
    <form className="toolbar" onSubmit={submit}><input required value={articleId} onChange={event => setArticleId(event.target.value)} placeholder="Article UUID" /><button>Queue analysis</button><button type="button" onClick={loadLogs}>Load logs</button>{message && <span>{message}</span>}</form>
    <section className="cards">{services.map(service => <article className="card" key={service.service}>
      <h3>{service.service}</h3><Status value={service.status}/><p>{service.latency_ms} ms</p>
      {service.error && <small>{service.error}</small>}
    </article>)}</section>
    <section className="panel"><h2>Jobs</h2><table><thead><tr><th>Article</th><th>Status</th><th>Requested</th><th>Error</th></tr></thead>
      <tbody>{jobs.map(job => <tr key={job.job_id}><td>{job.article_id}</td><td><Status value={job.status}/></td><td>{new Date(job.requested_at).toLocaleString()}</td><td>{job.error_message || ""}</td><td>{["RUNNING", "QUEUED", "CANCEL_REQUESTED"].includes(job.status) ? <button onClick={() => action(job.job_id, "cancel")}>Cancel</button> : null}{["FAILED", "ABANDONED", "CANCELLED", "CANCELLED_AFTER_STAGE"].includes(job.status) ? <button onClick={() => action(job.job_id, "retry")}>Retry</button> : null}</td></tr>)}</tbody>
    </table></section>
    <section className="panel"><h2>Telemetry summary</h2><pre>{JSON.stringify(summary, null, 2)}</pre></section>
    <section className="panel"><h2>Logs</h2><pre>{logs.map((item, index) => <div key={index}>{item.line}</div>)}</pre></section>
  </main>;
}

function App() {
  return <Overview />;
}

createRoot(document.getElementById("root")).render(<App />);
