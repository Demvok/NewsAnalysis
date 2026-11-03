import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { use, init } from "echarts/core";
import { CustomChart, EffectScatterChart, LineChart, ScatterChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { MOCK_DATA } from "./mockData";
import "./style.css";

use([CustomChart, EffectScatterChart, LineChart, ScatterChart, DataZoomComponent, GridComponent, TooltipComponent, CanvasRenderer]);

const colors = ["#80e2c1", "#7bb5ff", "#efb36c", "#e994d8", "#a9c77d", "#f08080"];
const emptyData = { speakers: [], citations: [], kpis: {}, general_topic_fields: [], data_quality: {} };
const fmt = (value, digits = 2) => value == null ? "—" : Number(value).toFixed(digits);
const stamp = value => new Date(value).getTime();
const simplifyStance = value => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  const tenPointValue = Math.abs(numeric) <= 1 ? numeric * 10 : numeric;
  return Math.max(-5, Math.min(5, Math.round(tenPointValue / 2)));
};
const stanceTone = value => simplifyStance(value) > 0 ? "positive" : simplifyStance(value) < 0 ? "negative" : "neutral";
const colorForSpeaker = personUuid => colors[[...String(personUuid)].reduce((hash, character) => hash + character.charCodeAt(0), 0) % colors.length];

async function loadLive(params) {
  const requestId = globalThis.crypto?.randomUUID?.() || String(Date.now());
  const response = await fetch(`/api/visualization/query?${params}`, { headers: { "x-request-id": requestId } });
  if (!response.ok) throw new Error(`Visualization query failed (request ${requestId})`);
  return response.json();
}

function filterMock(data, phrase, field, threshold) {
  const citations = data.citations.filter(item => (!field || item.general_topic_field === field) && (!phrase || `${item.topic_name} ${item.quote}`.toLowerCase().includes(phrase.toLowerCase())) && item.relevancy_score >= threshold);
  const speakers = data.speakers.filter(item => citations.some(citation => citation.person_uuid === item.person_uuid));
  return { ...data, citations, speakers };
}

function Kpis({ kpis }) {
  const items = [["Average", simplifyStance(kpis.average_stance), "stance / 5"], ["Median", simplifyStance(kpis.median_stance), "stance / 5"], ["Std", simplifyStance(kpis.std_stance), "spread / 5"], ["Inconsistency", `${fmt((kpis.inconsistency_frequency || 0) * 100, 1)}%`, "of citations"]];
  return <div className="kpi-strip">{items.map(([label, value, detail]) => <article className="kpi" key={label}><span>{label}</span><strong>{typeof value === "string" ? value : fmt(value)}</strong><small>{detail}</small></article>)}</div>;
}

function Speakers({ speakers, selected, onSelect }) {
  return <div className="speaker-strip">{speakers.slice(0, 8).map(speaker => <button className={`speaker-card ${selected === speaker.person_uuid ? "selected" : ""} ${speaker.person_type === "ANONYMOUS_SOURCE" ? "vague" : ""}`} key={speaker.person_uuid} onClick={() => onSelect(selected === speaker.person_uuid ? null : speaker.person_uuid)}><span className="speaker-avatar" style={{ background: colorForSpeaker(speaker.person_uuid) }}>{speaker.name[0]}</span><span className="speaker-info"><strong>{speaker.name}</strong><small>{speaker.citations} citations</small><small className="person-type">{speaker.person_type || "PERSON"}</small></span><span className={`speaker-metric ${stanceTone(speaker.average_stance)}`} title="Average stance"><i>μ</i>{simplifyStance(speaker.average_stance)}</span><span className="speaker-metric spread" title="Stance deviation"><i>σ</i>{simplifyStance(speaker.std_stance)}</span></button>)}</div>;
}

function Timeline({ citations, selectedSpeaker }) {
  const chartRef = useRef(null);
  const visible = useMemo(() => citations.filter(item => (!selectedSpeaker || item.person_uuid === selectedSpeaker) && Number.isFinite(Number(item.stance_value)) && Number.isFinite(stamp(item.published_at))), [citations, selectedSpeaker]);

  useEffect(() => {
    const chart = init(chartRef.current, null, { renderer: "canvas" });
    const timelineCitations = citations.filter(item => Number.isFinite(Number(item.stance_value)) && Number.isFinite(stamp(item.published_at)));
    const sortedDates = [...new Set(timelineCitations.map(item => stamp(item.published_at)))].sort((a, b) => a - b);
    const start = sortedDates[0] || Date.now() - 86400000;
    const end = sortedDates.at(-1) || start + 86400000;
    const dateToPosition = new Map();
    let position = 0;
    sortedDates.forEach((date, index) => {
      if (index) position += Math.min((date - sortedDates[index - 1]) / 86400000, 45);
      dateToPosition.set(date, position);
    });
    const maxPosition = Math.max(position, 1);
    const positionFor = item => dateToPosition.get(stamp(item.published_at)) ?? 0;
    const dateForPosition = value => {
      let nearest = sortedDates[0] || start;
      sortedDates.forEach(date => {
        if (Math.abs(positionFor({ published_at: date }) - value) < Math.abs(positionFor({ published_at: nearest }) - value)) nearest = date;
      });
      return new Date(nearest).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    };
    const people = [...new Set(visible.map(item => item.person_uuid))];
    const counts = new Map(people.map(id => [id, visible.filter(item => item.person_uuid === id).length]));
    const byPerson = people.filter(id => visible.some(item => item.person_uuid === id && item.person_type !== "ANONYMOUS_SOURCE")).map(id => visible.filter(item => item.person_uuid === id).sort((a, b) => stamp(a.published_at) - stamp(b.published_at)));
    const consecutive = new Set(byPerson.flatMap(items => items.flatMap((item, index) => index && stamp(item.published_at) - stamp(items[index - 1].published_at) <= 1209600000 ? [item.citation_uuid, items[index - 1].citation_uuid] : [])));
    const lineData = [];
    byPerson.forEach((items, index) => lineData.push({
      type: "line",
      name: items[0]?.person_name,
      data: items.map(item => [positionFor(item), simplifyStance(item.stance_value)]),
      showSymbol: false,
      smooth: .35,
      lineStyle: { color: colorForSpeaker(items[0]?.person_uuid), width: 2, opacity: .7 },
      silent: true,
      z: 2,
    }));
    const links = [];
    visible.forEach(item => {
      if (!item.inconsistency) return;
      const otherId = item.inconsistency.citation_a_uuid === item.citation_uuid ? item.inconsistency.citation_b_uuid : item.inconsistency.citation_a_uuid;
      const other = visible.find(candidate => candidate.citation_uuid === otherId);
      if (other && item.citation_uuid < other.citation_uuid) links.push([item, other, item.inconsistency]);
    });
    links.forEach(([first, second, inconsistency]) => {
      const resolved = inconsistency.classification === "CONSISTENT";
      const severity = String(inconsistency.severity || "LOW").toLowerCase();
      lineData.push({
        type: "line",
        name: `Evidence link: ${inconsistency.classification}`,
        data: [[positionFor(first), simplifyStance(first.stance_value)], [positionFor(second), simplifyStance(second.stance_value)]],
        smooth: .2,
        showSymbol: false,
        lineStyle: { color: resolved ? "#70889a" : "#ef8d99", width: 2, type: "dashed", opacity: resolved ? .5 : .9 },
        tooltip: { show: true },
        effect: resolved ? { show: false } : { show: true, period: severity === "critical" ? 0.65 : severity === "high" ? 1.15 : 1.8, symbol: "circle", symbolSize: 8, trailLength: .2 },
        animation: true,
        z: 4,
        linkMeta: inconsistency,
      });
    });
    const severityRank = { low: 1, medium: 2, high: 3, critical: 4 };
    const inconsistencyMarkers = links.filter(([, , inconsistency]) => inconsistency.classification !== "CONSISTENT").map(([first, second, inconsistency]) => {
      const severity = String(inconsistency.severity || "LOW").toLowerCase();
      return {
        value: [(positionFor(first) + positionFor(second)) / 2, (simplifyStance(first.stance_value) + simplifyStance(second.stance_value)) / 2],
        symbolSize: 9 + (severityRank[severity] || 1) * 4,
        itemStyle: { color: "#ef8d99", borderColor: "#fff1b0", borderWidth: 2 },
        inconsistency,
      };
    });
    let stanceSum = 0;
    const drift = [...timelineCitations].sort((a, b) => stamp(a.published_at) - stamp(b.published_at));
    lineData.push({
      type: "line",
      name: "Average stance drift",
      data: drift.map((item, index) => [positionFor(item), (() => {
        stanceSum += simplifyStance(item.stance_value);
        return stanceSum / (index + 1);
      })()]),
      smooth: .35,
      showSymbol: false,
      lineStyle: { color: "#e6c77b", width: 2, type: "dashed", opacity: .9 },
      silent: true,
      z: 3,
    });
    const citationData = visible.map(item => ({
      value: [positionFor(item), simplifyStance(item.stance_value)],
      name: item.person_name,
      symbol: item.person_type === "ORGANIZATION" ? "diamond" : "circle",
      symbolSize: 6 + Math.min(34, (item.relevancy_score || .5) * 18 + (item.stance_confidence || .5) * 14 + (counts.get(item.person_uuid) || 0) * 2.5 + (consecutive.has(item.citation_uuid) ? 4 : 0)),
      itemStyle: {
        color: colorForSpeaker(item.person_uuid),
        opacity: item.person_type === "ANONYMOUS_SOURCE" ? .42 : 1,
        borderColor: item.inconsistency?.classification && item.inconsistency.classification !== "CONSISTENT" ? "#fff1b0" : "#0b1725",
        borderWidth: item.inconsistency?.classification && item.inconsistency.classification !== "CONSISTENT" ? 3 : 2,
      },
      citation: item,
    }));
    const gapData = sortedDates.slice(1).flatMap((date, index) => date - sortedDates[index] > 3888000000 ? [[(positionFor({ published_at: sortedDates[index] }) + positionFor({ published_at: date })) / 2, 0]] : []);
    const dateLabelData = sortedDates.map(date => ({
      value: [positionFor({ published_at: date }), 0],
      date: new Date(date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }),
    }));
    const option = {
      animation: false,
      backgroundColor: "transparent",
      grid: { left: 18, right: 18, top: 24, bottom: 86, containLabel: false },
      tooltip: {
        trigger: "item",
        enterable: true,
        hideDelay: 4500,
        confine: true,
        backgroundColor: "#172b40",
        borderColor: "#72dbc2",
        textStyle: { color: "#dceaf2", fontSize: 11 },
        position: (point, params, dom, rect, size) => [Math.min(point[0] + 14, size.viewSize[0] - size.contentSize[0] - 8), Math.max(12, point[1] - size.contentSize[1] - 12)],
        formatter: params => {
          const item = params.data?.citation;
          if (item) return `<strong>${item.person_name}</strong><br><span>${item.topic_name} · stance ${simplifyStance(item.stance_value)}/5</span><br><span>${item.quote}</span><br><small>${new Date(item.published_at).toLocaleDateString()}</small>${item.article_url ? `<br><a href="${item.article_url}" target="_blank" rel="noreferrer">Open original article →</a>` : ""}`;
          const inconsistency = params.data?.inconsistency || params.series?.linkMeta;
          return inconsistency ? `<strong>${inconsistency.classification}</strong><br>${inconsistency.comment || "No inconsistency comment available."}` : params.seriesName;
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        max: maxPosition,
        axisLine: { lineStyle: { color: "#466276" } },
        axisTick: { show: false },
        axisLabel: { show: false, formatter: dateForPosition },
        splitLine: { show: false },
      },
      yAxis: { type: "value", min: -5, max: 5, interval: 1, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { show: false }, splitLine: { show: false } },
      dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none", zoomOnMouseWheel: true, moveOnMouseMove: true }, { type: "slider", xAxisIndex: 0, filterMode: "none", height: 42, bottom: 18, borderColor: "#29445b", backgroundColor: "#0d1b2b", fillerColor: "rgba(114,219,194,.18)", handleStyle: { color: "#72dbc2" }, textStyle: { color: "#7890a5", fontSize: 10 } }],
      series: [
        {
          type: "custom",
          renderItem: (params, api) => {
            const top = api.coord([0, 5]);
            const bottom = api.coord([maxPosition, -5]);
            return {
              type: "rect",
              shape: { x: top[0], y: top[1], width: bottom[0] - top[0], height: bottom[1] - top[1] },
              style: {
                fill: {
                  type: "linear",
                  x: 0,
                  y: 0,
                  x2: 0,
                  y2: 1,
                  colorStops: [
                    { offset: 0, color: "rgba(43,155,112,.34)" },
                    { offset: .5, color: "rgba(22,53,41,.08)" },
                    { offset: 1, color: "rgba(185,74,87,.34)" },
                  ],
                },
              },
            };
          },
          data: [0],
          silent: true,
          z: 0,
        },
        ...Array.from({ length: 11 }, (_, index) => ({ type: "line", data: [[0, index - 5], [maxPosition, index - 5]], showSymbol: false, silent: true, lineStyle: { color: index === 5 ? "#e6f1e8" : "#9eb0ae", width: index === 5 ? 2 : 1, opacity: index === 5 ? .76 : .34 }, z: 1 })),
        { type: "line", data: gapData.length ? gapData : [[0, 0]], showSymbol: false, silent: true, lineStyle: { color: "#9eb0b6", width: 2, type: "dashed", opacity: .85 }, z: 2 },
        ...lineData,
        { type: "scatter", name: "Dates", data: dateLabelData, symbolSize: 1, silent: true, label: { show: true, position: "bottom", distance: 8, color: "#9eb0b6", fontSize: 10, formatter: params => params.data.date, hideOverlap: true }, itemStyle: { color: "transparent" }, z: 5 },
        { type: "effectScatter", name: "Inconsistency comments", data: inconsistencyMarkers, rippleEffect: { scale: 2.6, brushType: "stroke", period: 2.8 }, tooltip: { show: true }, z: 7 },
        { type: "scatter", name: "Citations", data: citationData, z: 6 },
      ],
    };
    chart.setOption(option, true);
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, [visible]);

  return <div className="timeline-wrap"><div className="timeline-viewport echarts-viewport" ref={chartRef} role="img" aria-label="Speaker stance timeline" /></div>;
}

function App() {
  const [phrase, setPhrase] = useState(""); const [field, setField] = useState(""); const [threshold, setThreshold] = useState(.55); const [data, setData] = useState(MOCK_DATA); const [selectedSpeaker, setSelectedSpeaker] = useState(null); const [useMock, setUseMock] = useState(true); const [loading, setLoading] = useState(false); const [error, setError] = useState("");
  const fields = useMemo(() => data.general_topic_fields || [], [data.general_topic_fields]);
  useEffect(() => {
    if (useMock) { setData(filterMock(MOCK_DATA, phrase, field, threshold)); return undefined; }
    const timer = setTimeout(async () => { setLoading(true); try { setData(await loadLive(new URLSearchParams({ ...(phrase ? { phrase } : {}), ...(field ? { general_topic_field: field } : {}), min_similarity: threshold }))); } catch (value) { setError(value.message); } finally { setLoading(false); } }, 350);
    return () => clearTimeout(timer);
  }, [phrase, field, threshold, useMock]);
  return <main className="page"><header><div><span className="overline">NEWSANALYSIS / EVIDENCE GRAPH</span><h1>Position landscape</h1></div><div className="controls"><input value={phrase} onChange={event => setPhrase(event.target.value)} placeholder="Search any topic phrase…" /><select value={field} onChange={event => setField(event.target.value)}><option value="">All topic fields</option>{fields.map(item => <option value={item.name} key={item.name}>{item.name}</option>)}</select><label>similarity <input type="range" min="-1" max="1" step=".05" value={threshold} onChange={event => setThreshold(Number(event.target.value))} /><b>{threshold.toFixed(2)}</b></label><label className="mode-toggle"><input type="checkbox" checked={!useMock} onChange={event => setUseMock(!event.target.checked)} /> live data</label><span className="loading-indicator">{loading ? "Updating…" : useMock ? "Mock" : "Live"}</span></div></header>{error && <div className="error">{error}</div>}<Kpis kpis={data.kpis} /><section className="panel timeline-panel"><div className="section-heading"><span className="overline">TIMELINE</span><span className="muted">window navigator · {data.citations.length} citations</span></div><Timeline citations={data.citations} selectedSpeaker={selectedSpeaker} /></section><section><div className="section-heading"><span className="overline">SPEAKERS</span></div><Speakers speakers={data.speakers} selected={selectedSpeaker} onSelect={setSelectedSpeaker} /></section></main>;
}

createRoot(document.getElementById("root")).render(<App />);
