import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { use, init } from "echarts/core";
import { CustomChart, EffectScatterChart, LineChart, ScatterChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { MOCK_DATA } from "./mockData";
import "./style.css";

use([CustomChart, EffectScatterChart, LineChart, ScatterChart, DataZoomComponent, GridComponent, TooltipComponent, CanvasRenderer]);

const colors = ["#80e2c1", "#7bb5ff", "#efb36c", "#e994d8", "#a9c77d", "#f08080", "#b58cff", "#ff9f68", "#62d6e8", "#e6cf63", "#d98cff", "#8dd17e"];
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
const stanceLabel = value => {
  const stance = simplifyStance(value);
  return stance <= -3 ? "Strongly opposed" : stance < 0 ? "Opposed" : stance >= 3 ? "Strongly supportive" : stance > 0 ? "Supportive" : "Neutral";
};
const deviationLabel = value => {
  const deviation = Math.abs(Number(value));
  return !Number.isFinite(deviation) ? "Unavailable" : deviation < .15 ? "Very consistent" : deviation < .35 ? "Mostly consistent" : deviation < .65 ? "Mixed" : "Wide variation";
};
const percentileSliderPosition = value => Number(value);
const percentileFromSliderPosition = value => Math.max(1, Math.min(100, Math.round(Number(value))));
const colorForSpeaker = personUuid => colors[[...String(personUuid)].reduce((hash, character) => hash + character.charCodeAt(0), 0) % colors.length];
const BUBBLE_MIN_SIZE = 12;
const BUBBLE_MAX_SIZE = 50;
// Tune these two values to change the smallest/largest rendered bubbles.
const SIMILARITY_MAGNETS = [.55, .65, .75, .85, .9, .95];
const snapSimilarity = value => {
  const nearest = SIMILARITY_MAGNETS.reduce((best, candidate) => Math.abs(candidate - value) < Math.abs(best - value) ? candidate : best, value);
  return Math.abs(nearest - value) <= .012 ? nearest : value;
};
const clampSimilarity = value => Math.max(.5, Math.min(1, Number(value)));
const percentileThreshold = (citations, percentile, fallback) => {
  const similarities = citations.map(item => Number(item.topic_similarity)).filter(Number.isFinite).sort((a, b) => a - b);
  if (!similarities.length) return clampSimilarity(fallback);
  const index = Math.min(similarities.length - 1, Math.max(0, Math.floor((1 - percentile / 100) * (similarities.length - 1))));
  return clampSimilarity(similarities[index]);
};
const cosineWeight = value => {
  const cosine = Number(value);
  if (!Number.isFinite(cosine)) return 1;
  return Math.max(0, Math.min(1, (cosine + 1) / 2));
};
const effectiveStance = item => {
  const propagated = Number(item.transferred_stance ?? item.transferred_stance_value);
  return Number.isFinite(propagated) ? propagated : Number(item.stance_value);
};
const categoricalLabels = {
  endorsement: ["", "Weak", "Moderate", "Strong", "Absolute"],
  commitment: ["", "Tentative", "Moderate", "Firm", "Definitive"],
  conditionality: ["None", "Limited", "High"],
};
const categoricalLabel = (field, value) => categoricalLabels[field]?.[Number(value)] || "Not available";
const directionIsOpposed = value => /oppose|against|negative/i.test(String(value || ""));

async function loadLive(params) {
  const requestId = globalThis.crypto?.randomUUID?.() || String(Date.now());
  const response = await fetch(`/api/visualization/query?${params}`, { headers: { "x-request-id": requestId } });
  if (!response.ok) throw new Error(`Visualization query failed (request ${requestId})`);
  return response.json();
}

function filterMock(data, phrase, fields, threshold) {
  const citations = data.citations
    .map(item => ({ ...item, topic_similarity: phrase ? (`${item.topic_name} ${item.quote}`.toLowerCase().includes(phrase.toLowerCase()) ? .9 : .42) : null }))
    .filter(item => (!fields.length || fields.includes(item.general_topic_field)) && (threshold == null || !phrase || item.topic_similarity >= threshold));
  const speakers = data.speakers.filter(item => citations.some(citation => citation.person_uuid === item.person_uuid));
  return { ...data, citations, speakers };
}

function topicOptionsFrom(data) {
  return [...new Map((data.citations || []).map(item => [item.topic_uuid, {
    topic_uuid: item.topic_uuid,
    topic_name: item.topic_name,
    topic_description: item.topic_description,
    citation_count: (data.citations || []).filter(citation => citation.topic_uuid === item.topic_uuid).length,
  }])).values()].sort((a, b) => b.citation_count - a.citation_count || a.topic_name.localeCompare(b.topic_name));
}

function decorateTopicRelevance(data, searchMode, phrase, activeTopic, similarityThreshold) {
  const citations = !activeTopic && !phrase.trim() ? data.citations : data.citations.filter(item => {
      const similarity = Number(item.topic_similarity);
      return !Number.isFinite(similarity) || similarity >= similarityThreshold;
  });
  const bySpeaker = new Map();
  const speakerDetails = new Map((data.speakers || []).map(speaker => [speaker.person_uuid, speaker]));
  citations.forEach(item => {
    const stance = effectiveStance(item);
    if (!Number.isFinite(stance)) return;
    const speaker = bySpeaker.get(item.person_uuid) || { ...speakerDetails.get(item.person_uuid), person_uuid: item.person_uuid, name: item.person_name, person_type: item.person_type, citations: 0, stances: [] };
    speaker.citations += 1;
    speaker.stances.push(stance);
    bySpeaker.set(item.person_uuid, speaker);
  });
  const speakers = [...bySpeaker.values()].map(speaker => {
    const average = speaker.stances.reduce((sum, value) => sum + value, 0) / speaker.stances.length;
    return { ...speaker, average_stance: average, std_stance: Math.sqrt(speaker.stances.reduce((sum, value) => sum + (value - average) ** 2, 0) / speaker.stances.length) };
  }).sort((a, b) => b.citations - a.citations);
  const values = citations.map(effectiveStance).filter(Number.isFinite).sort((a, b) => a - b);
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  const middle = Math.floor(values.length / 2);
  const median = values.length ? values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2 : null;
  return {
    ...data,
    citations,
    speakers,
    kpis: { ...data.kpis, average_stance: mean, median_stance: median, std_stance: values.length ? Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length) : null },
  };
}

function Kpis({ kpis }) {
  const stanceCategory = value => value == null ? "Unavailable" : value > .12 ? "Leaning supportive" : value < -.12 ? "Leaning opposed" : "Broadly neutral";
  const spreadCategory = value => value == null ? "Unavailable" : value < .15 ? "Very consistent" : value < .35 ? "Mostly consistent" : value < .65 ? "Mixed positions" : "Wide variation";
  const inconsistencyPercent = (kpis.inconsistency_frequency || 0) * 100;
  const inconsistencyCategory = inconsistencyPercent >= 20 ? "Frequent" : inconsistencyPercent >= 8 ? "Occasional" : "Rare";
  const items = [
    ["Average", stanceCategory(kpis.average_stance), simplifyStance(kpis.average_stance), "stance / 5", stanceTone(kpis.average_stance)],
    ["Median", stanceCategory(kpis.median_stance), simplifyStance(kpis.median_stance), "stance / 5", stanceTone(kpis.median_stance)],
    ["Spread", spreadCategory(kpis.std_stance), simplifyStance(kpis.std_stance), "std / 5", kpis.std_stance >= .65 ? "negative" : kpis.std_stance >= .35 ? "neutral" : "positive"],
    ["Inconsistency", inconsistencyCategory, `${fmt(inconsistencyPercent, 1)}%`, "of citations", inconsistencyPercent >= 20 ? "negative" : inconsistencyPercent >= 8 ? "neutral" : "positive"],
  ];
  return <div className="kpi-strip">{items.map(([label, category, value, detail, tone]) => <article className={`kpi ${tone}`} key={label}><span>{label}</span><strong>{category}</strong><b>{value}</b><small>{detail}</small></article>)}</div>;
}

function cleanTags(fields) {
  return [...new Set((fields || []).flatMap(item => String(item.name || item).split(/[\/|,]/).map(tag => tag.trim()).filter(Boolean)))].sort((a, b) => a.localeCompare(b));
}

function Speakers({ speakers, selected, onSelect }) {
  return <div className="speaker-layout">{speakers.slice(0, 8).map(speaker => {
    const stance = simplifyStance(speaker.average_stance);
    const stancePosition = `${((stance + 5) / 10) * 100}%`;
    const typeLabel = speaker.person_type === "ANONYMOUS_SOURCE" ? "Anonymous source" : speaker.person_type || "Person";
    return <button className={`speaker-tile ${selected === speaker.person_uuid ? "is-selected" : ""} ${speaker.person_type === "ANONYMOUS_SOURCE" ? "is-vague" : ""}`} key={speaker.person_uuid} onClick={() => onSelect(selected === speaker.person_uuid ? null : speaker.person_uuid)}>
      <span className="speaker-tile__topline"><span className="speaker-tile__identity"><span className="speaker-tile__avatar" style={{ background: colorForSpeaker(speaker.person_uuid) }}>{speaker.name[0]}</span><span><strong>{speaker.name}</strong><small>{typeLabel}</small></span></span><span className="speaker-tile__citation-count"><b>{speaker.citations}</b><small>citations</small></span></span>
      <span className="speaker-tile__details">{speaker.affiliation && <span className="speaker-tile__affiliation">{typeof speaker.affiliation === "string" ? speaker.affiliation : JSON.stringify(speaker.affiliation)}</span>}{speaker.aliases?.length > 0 && <span className="speaker-tile__aliases">Also known as {speaker.aliases.join(", ")}</span>}</span>
      <span className="speaker-tile__stance-label"><span>Average position</span><strong className={stanceTone(speaker.average_stance)}>{stanceLabel(speaker.average_stance)}</strong><b>{stance > 0 ? "+" : ""}{stance} <small>/ 5</small></b></span>
      <span className="speaker-tile__stance-scale" aria-label={`Average stance: ${stance} out of 5`}><i style={{ left: stancePosition }} /><span className="speaker-tile__scale-zero" /></span>
      <span className="speaker-tile__footer"><span><small>Spread</small><b>{fmt(speaker.std_stance, 2)}</b><em>{deviationLabel(speaker.std_stance)}</em></span><span className="speaker-tile__select-hint">{selected === speaker.person_uuid ? "Showing timeline" : "View timeline"} <b>↗</b></span></span>
    </button>;
  })}</div>;
}

function Timeline({ citations, selectedSpeaker, topicSearchActive, similarityThreshold }) {
  const chartRef = useRef(null);
  const visible = useMemo(() => citations.filter(item => (!selectedSpeaker || item.person_uuid === selectedSpeaker) && Number.isFinite(effectiveStance(item)) && Number.isFinite(stamp(item.published_at))), [citations, selectedSpeaker]);

  useEffect(() => {
    const chart = init(chartRef.current, null, { renderer: "canvas" });
    const timelineCitations = citations.filter(item => Number.isFinite(effectiveStance(item)) && Number.isFinite(stamp(item.published_at)));
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
      data: items.map(item => [positionFor(item), simplifyStance(effectiveStance(item))]),
      showSymbol: false,
      smooth: .35,
      lineStyle: { color: colorForSpeaker(items[0]?.person_uuid), width: 2, opacity: .7 },
      silent: true,
      z: 2,
    }));
    const timelineByCitation = new Map(timelineCitations.map(item => [item.citation_uuid, item]));
    const inconsistencySource = selectedSpeaker
      ? timelineCitations.filter(item => item.person_uuid === selectedSpeaker)
      : timelineCitations;
    const links = [];
    inconsistencySource.forEach(item => {
      if (!item.inconsistency) return;
      const otherId = item.inconsistency.citation_a_uuid === item.citation_uuid ? item.inconsistency.citation_b_uuid : item.inconsistency.citation_a_uuid;
      const other = timelineByCitation.get(otherId);
      if (other && other.person_uuid === item.person_uuid && item.citation_uuid < other.citation_uuid) {
        links.push([item, other, item.inconsistency]);
      }
    });
    links.forEach(([first, second, inconsistency]) => {
      const resolved = inconsistency.classification === "CONSISTENT";
      const severity = String(inconsistency.severity || "LOW").toLowerCase();
      lineData.push({
        type: "line",
        name: `Evidence link: ${inconsistency.classification}`,
        data: [[positionFor(first), simplifyStance(effectiveStance(first))], [positionFor(second), simplifyStance(effectiveStance(second))]],
        smooth: .2,
        showSymbol: false,
        lineStyle: { color: resolved ? "#70889a" : "#ef8d99", width: 2, type: "dashed", opacity: resolved ? .5 : .9 },
        tooltip: { show: true },
        effect: resolved ? { show: false } : { show: true, period: severity === "critical" ? 0.65 : severity === "high" ? 1.15 : 1.8, symbol: "circle", symbolSize: 8, trailLength: .2 },
        animation: true,
        z: 4,
        linkMeta: { ...inconsistency, person_name: first.person_name, topic_name: first.topic_name },
      });
    });
    const severityRank = { low: 1, medium: 2, high: 3, critical: 4 };
    const inconsistencyMarkers = links.filter(([, , inconsistency]) => inconsistency.classification !== "CONSISTENT").map(([first, second, inconsistency]) => {
      const severity = String(inconsistency.severity || "LOW").toLowerCase();
      return {
        value: [(positionFor(first) + positionFor(second)) / 2, (simplifyStance(effectiveStance(first)) + simplifyStance(effectiveStance(second))) / 2],
        symbolSize: 9 + (severityRank[severity] || 1) * 4,
        itemStyle: { color: "#ef8d99", borderColor: "#fff1b0", borderWidth: 2 },
        inconsistency: { ...inconsistency, person_name: first.person_name, topic_name: first.topic_name },
      };
    });
    const weightedDrift = items => {
      let stanceSum = 0;
      let stanceWeight = 0;
      return [...items].sort((a, b) => stamp(a.published_at) - stamp(b.published_at)).map(item => {
        const weight = cosineWeight(item.topic_similarity);
        stanceSum += simplifyStance(effectiveStance(item)) * weight;
        stanceWeight += weight;
        return [positionFor(item), stanceWeight ? stanceSum / stanceWeight : 0];
      });
    };
    const averageLine = {
      type: "line",
      name: selectedSpeaker ? "Selected speaker average" : "Average stance drift",
      data: weightedDrift(visible),
      smooth: .35,
      showSymbol: false,
      lineStyle: { color: "#e6c77b", width: 2, type: "dashed", opacity: .95 },
      silent: true,
      z: 3,
    };
    lineData.push(averageLine);
    if (selectedSpeaker) {
      lineData.push({
        ...averageLine,
        name: "All speakers average",
        data: weightedDrift(timelineCitations),
        lineStyle: { ...averageLine.lineStyle, color: "#91a8ff", type: "dashed", opacity: .7 },
        z: 2,
      });
    }
    const similarities = visible.map(item => Number(item.topic_similarity)).filter(Number.isFinite);
    const minimumSimilarity = similarities.length ? Math.min(...similarities) : 0;
    const maximumSimilarity = similarities.length ? Math.max(...similarities) : 1;
    const similarityRange = Math.max(maximumSimilarity - minimumSimilarity, .0001);
    const citationData = visible.map(item => {
      const similarity = Number(item.topic_similarity);
      const similarityPercentage = Number.isFinite(similarity)
        ? Math.max(0, Math.min(1, (similarity - minimumSimilarity) / similarityRange))
        : 1;
      return {
      id: item.citation_uuid,
      value: [positionFor(item), simplifyStance(effectiveStance(item))],
      name: item.person_name,
      symbol: item.person_type === "ORGANIZATION" ? "diamond" : "circle",
      symbolSize: BUBBLE_MIN_SIZE + (BUBBLE_MAX_SIZE - BUBBLE_MIN_SIZE) * similarityPercentage,
      itemStyle: {
        color: colorForSpeaker(item.person_uuid),
        opacity: item.person_type === "ANONYMOUS_SOURCE" ? .42 : .3 + similarityPercentage * .7,
        borderColor: item.inconsistency?.classification && item.inconsistency.classification !== "CONSISTENT" ? "#fff1b0" : "#0b1725",
        borderWidth: item.inconsistency?.classification && item.inconsistency.classification !== "CONSISTENT" ? 3 : 2,
      },
      emphasis: { scale: 1.25, itemStyle: { shadowBlur: 18, shadowColor: "#72dbc2", borderColor: "#e8fff8", borderWidth: 3 } },
      citation: item,
      };
    });
    const gapData = sortedDates.slice(1).flatMap((date, index) => date - sortedDates[index] > 3888000000 ? [[(positionFor({ published_at: sortedDates[index] }) + positionFor({ published_at: date })) / 2, 0]] : []);
    const dateLabelData = sortedDates.map(date => ({
      value: [positionFor({ published_at: date }), 0],
      date: new Date(date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }),
    }));
    lineData.forEach(series => { series.animation = false; });
    const option = {
      animation: true,
      animationDuration: 520,
      animationEasing: "linear",
      animationDurationUpdate: 260,
      animationEasingUpdate: "elasticOut",
      backgroundColor: "transparent",
      grid: { left: 18, right: 18, top: 24, bottom: 86, containLabel: false },
      tooltip: {
        trigger: "item",
        enterable: true,
        showDelay: 320,
        hideDelay: 550,
        transitionDuration: .15,
        confine: true,
        backgroundColor: "#172b40",
        borderColor: "#72dbc2",
        textStyle: { color: "#dceaf2", fontSize: 11 },
        position: (point, params, dom, rect, size) => [Math.min(point[0] + 14, size.viewSize[0] - size.contentSize[0] - 8), Math.max(12, point[1] - size.contentSize[1] - 12)],
        formatter: params => {
          const item = params.data?.citation;
          if (item) {
            const topicMentions = item.topic_names?.length ? item.topic_names : [item.topic_name];
            const direction = item.direction || item.stance || "UNKNOWN";
            return `<div class="evidence-card"><div class="evidence-card__header"><div><strong>${item.person_name}</strong><span>${item.topic_name}</span></div><b class="evidence-card__direction ${directionIsOpposed(direction) ? "is-opposed" : ""}">${direction}</b></div><div class="evidence-card__metrics"><div><small>Endorsement</small><b>${categoricalLabel("endorsement", item.endorsement)}</b></div><div><small>Commitment</small><b>${categoricalLabel("commitment", item.commitment)}</b></div><div><small>Conditionality</small><b>${categoricalLabel("conditionality", item.conditionality)}</b></div><div><small>Stance</small><b>${simplifyStance(effectiveStance(item))}/5</b></div></div><div class="evidence-card__quote"><small>Original evidence</small><p>${item.quote || "No original quote available."}</p></div><div class="evidence-card__summary"><small>Summary</small><p>${item.summarized_quote || "No summary available."}</p></div><div class="evidence-card__topics"><small>Topics in this citation</small><div>${topicMentions.map(topic => `<span>${topic}</span>`).join("")}</div></div><div class="evidence-card__footer"><span>${new Date(item.published_at).toLocaleDateString()}${item.topic_similarity != null ? ` · similarity ${Number(item.topic_similarity).toFixed(2)}` : ""}</span>${item.article_url ? `<a href="${item.article_url}" target="_blank" rel="noreferrer">Open article →</a>` : ""}</div></div>`;
          }
          const inconsistency = params.data?.inconsistency || params.series?.linkMeta;
          return inconsistency ? `<div class="inconsistency-card"><strong>${inconsistency.classification}</strong><span>${inconsistency.person_name || "Unknown speaker"}</span><span>${inconsistency.topic_name || "Unknown topic"}</span><p>${inconsistency.comment || "No inconsistency comment available."}</p></div>` : params.seriesName;
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
        { id: "citation-series", type: "scatter", name: "Citations", data: citationData, z: 6 },
      ],
    };
    chart.setOption(option, true);
    const citationSeriesIndex = option.series.length - 1;
    let restoreTimer;
    const renderCitationData = nextData => chart.setOption({
      animationDurationUpdate: 720,
      animationEasingUpdate: "cubicInOut",
      series: [{ id: "citation-series", data: nextData }],
    }, { lazyUpdate: false });
    const restoreCitationOffsets = () => {
      renderCitationData(citationData.map(point => ({ ...point, value: [...point.value] })));
    };
    const updateCitationOffsets = hovered => {
      clearTimeout(restoreTimer);
      const hoveredPosition = chart.convertToPixel({ seriesIndex: citationSeriesIndex }, [positionFor(hovered), simplifyStance(effectiveStance(hovered))]);
      const nearby = citationData
        .map(point => ({ point, position: chart.convertToPixel({ seriesIndex: citationSeriesIndex }, point.value) }))
        .filter(({ point, position }) => {
          const distance = Math.hypot(position[0] - hoveredPosition[0], position[1] - hoveredPosition[1]);
          return distance <= 52;
        });
      const arranged = nearby.filter(({ point }) => point.citation.citation_uuid !== hovered.citation_uuid);
      const ringRadius = Math.max(58, arranged.reduce((radius, { point }) => Math.max(radius, (Number(point.symbolSize) || BUBBLE_MIN_SIZE) / 2 + 18), 0));
      const positions = new Map();
      arranged.forEach(({ point }, index) => {
        const angle = -Math.PI / 2 + (index * Math.PI * 2) / arranged.length;
        positions.set(point, chart.convertFromPixel(
          { seriesIndex: citationSeriesIndex },
          [hoveredPosition[0] + Math.cos(angle) * ringRadius, hoveredPosition[1] + Math.sin(angle) * ringRadius],
        ));
      });
      const arrangedData = citationData.map(point => {
        return { ...point, value: positions.get(point) || [...point.value] };
      });
      renderCitationData(arrangedData);
      restoreTimer = setTimeout(restoreCitationOffsets, 2400);
    };
    chart.on("mouseover", params => {
      if (params.seriesId === "citation-series" && params.data?.citation) updateCitationOffsets(params.data.citation);
    });
    chart.on("click", () => chart.dispatchAction({ type: "hideTip" }));
    chart.on("mouseout", params => {
      if (params.seriesId === "citation-series") {
        clearTimeout(restoreTimer);
        restoreTimer = setTimeout(restoreCitationOffsets, 2400);
      }
    });
    chart.on("globalout", () => {
      clearTimeout(restoreTimer);
      restoreTimer = setTimeout(restoreCitationOffsets, 2400);
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { clearTimeout(restoreTimer); chart.off("mouseover"); chart.off("mouseout"); chart.off("globalout"); window.removeEventListener("resize", resize); chart.dispose(); };
  }, [visible]);

  return <div className="timeline-wrap"><div className="timeline-viewport echarts-viewport" ref={chartRef} role="img" aria-label="Speaker stance timeline" /></div>;
}

function App() {
  const [searchMode, setSearchMode] = useState("list");
  const [phrase, setPhrase] = useState("");
  const [fields, setFields] = useState([]);
  const [similarityMode, setSimilarityMode] = useState("percentile");
  const [percentile, setPercentile] = useState(80);
  const [threshold, setThreshold] = useState(.82);
  const [data, setData] = useState(MOCK_DATA);
  const [topics, setTopics] = useState(topicOptionsFrom(MOCK_DATA));
  const [selectedTopicId, setSelectedTopicId] = useState(null);
  const [selectedSpeaker, setSelectedSpeaker] = useState(null);
  const [useMock, setUseMock] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const availableFields = useMemo(() => cleanTags(data.general_topic_fields), [data.general_topic_fields]);
  const activeTopic = useMemo(() => {
    if (searchMode === "semantic" && phrase.trim()) return null;
    return topics.find(topic => topic.topic_uuid === selectedTopicId) || topics[0] || null;
  }, [phrase, searchMode, selectedTopicId, topics]);
  const appliedThreshold = useMemo(() => similarityMode === "manual"
    ? clampSimilarity(threshold)
    : percentileThreshold(data.citations || [], percentile, threshold), [data.citations, percentile, similarityMode, threshold]);
  const displayData = useMemo(() => decorateTopicRelevance(data, searchMode, phrase, activeTopic, appliedThreshold), [activeTopic, appliedThreshold, data, phrase, searchMode]);
  useEffect(() => {
    if (useMock) {
      setTopics(topicOptionsFrom(MOCK_DATA));
      return;
    }
    let cancelled = false;
    fetch("/api/topics/browse?limit=500&sort_by=citation_count&sort_dir=desc")
      .then(response => { if (!response.ok) throw new Error("Unable to load canonical topics"); return response.json(); })
      .then(value => { if (!cancelled) setTopics(value.items || value); })
      .catch(value => { if (!cancelled) setError(value.message); });
    return () => { cancelled = true; };
  }, [useMock]);
  useEffect(() => {
    if (topics.length && (!selectedTopicId || !topics.some(topic => topic.topic_uuid === selectedTopicId))) {
      setSelectedTopicId(topics[Math.floor(Math.random() * topics.length)].topic_uuid);
    }
  }, [selectedTopicId, topics]);
  useEffect(() => {
    const queryPhrase = phrase.trim() || activeTopic?.topic_name || "";
    const queryThreshold = similarityMode === "manual" ? clampSimilarity(threshold) : .5;
    if (useMock) {
      setData(filterMock(MOCK_DATA, queryPhrase, fields, similarityMode === "manual" ? queryThreshold : null));
      return undefined;
    }
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          ...(queryPhrase ? { phrase: queryPhrase } : {}),
          ...(fields.length ? { general_topic_field: fields.join(",") } : {}),
          min_similarity: queryThreshold.toFixed(3),
        });
        setData(await loadLive(params));
      } catch (value) {
        setError(value.message);
      } finally {
        setLoading(false);
      }
    }, searchMode === "semantic" ? 800 : 350);
    return () => clearTimeout(timer);
  }, [activeTopic, fields, phrase, searchMode, similarityMode, threshold, useMock]);
  const topicSearchActive = Boolean(activeTopic || (searchMode === "semantic" && phrase.trim()));
  const selectTopic = event => {
    setPhrase("");
    setSearchMode("list");
    setSelectedTopicId(event.target.value);
  };
  return <main className="page"><header><div><span className="overline">NEWSANALYSIS / EVIDENCE GRAPH</span><h1>Position landscape</h1></div><div className="controls"><div className="search-mode" role="group" aria-label="Topic search mode"><button className={searchMode === "list" ? "active" : ""} onClick={() => { setPhrase(""); setSearchMode("list"); }}>Topics</button><button className={searchMode === "semantic" ? "active" : ""} onClick={() => setSearchMode("semantic")}>Semantic</button></div>{searchMode === "list" ? <select value={selectedTopicId || ""} onChange={selectTopic} aria-label="Select canonical topic"><option value="" disabled>Select a topic</option>{topics.map(topic => <option value={topic.topic_uuid} key={topic.topic_uuid}>{topic.topic_name}</option>)}</select> : <input value={phrase} onChange={event => setPhrase(event.target.value)} placeholder="Search any topic phrase…" aria-label="Semantic topic search" />}<label className="field-filter"><span>Tags</span><select value="" onChange={event => event.target.value && !fields.includes(event.target.value) && setFields([...fields, event.target.value])} aria-label="Add tag filter"><option value="">Add tag…</option>{availableFields.filter(tag => !fields.includes(tag)).map(tag => <option value={tag} key={tag}>{tag}</option>)}</select></label><div className="selected-tags">{fields.map(tag => <button type="button" className="tag-chip" key={tag} onClick={() => setFields(fields.filter(item => item !== tag))}>{tag} ×</button>)}</div><div className="similarity-control"><span>Similarity retention</span><div className="similarity-mode"><button type="button" className={similarityMode === "percentile" ? "active" : ""} onClick={() => setSimilarityMode("percentile")}>Percentile</button><button type="button" className={similarityMode === "manual" ? "active" : ""} onClick={() => setSimilarityMode("manual")}>Manual (advanced)</button></div>{similarityMode === "percentile" ? <><input type="range" min="0" max="100" step="1" list="similarity-percentile-marks" value={percentileSliderPosition(percentile)} onChange={event => setPercentile(percentileFromSliderPosition(Number(event.target.value)))} /><div className="range-marks"><span>1%</span><span>5%</span><span>10%</span><span>50%</span><span>100%</span></div><b>{percentile}% retained</b></> : <><input type="range" min=".5" max="1" step=".005" list="similarity-absolute-marks" value={threshold} onChange={event => setThreshold(snapSimilarity(Number(event.target.value)))} /><div className="range-marks"><span>0.50</span><span>0.75</span><span>0.90</span><span>0.95</span><span>1.00</span></div><b>{threshold.toFixed(2)} manual</b></>}<datalist id="similarity-percentile-marks"><option value={percentileSliderPosition(1)} label="1%" /><option value={percentileSliderPosition(5)} label="5%" /><option value={percentileSliderPosition(10)} label="10%" /><option value={percentileSliderPosition(50)} label="50%" /><option value="100" label="100%" /></datalist><datalist id="similarity-absolute-marks"><option value=".5" label="0.50" /><option value=".75" label="0.75" /><option value=".9" label="0.90" /><option value=".95" label="0.95" /><option value="1" label="1.00" /></datalist><small className="applied-threshold">Minimum similarity: {appliedThreshold.toFixed(2)}</small></div><label className="mode-toggle"><input type="checkbox" checked={!useMock} onChange={event => setUseMock(!event.target.checked)} /> live data</label><span className="loading-indicator">{loading ? <><i className="loading-spinner" /> Updating…</> : <><i className="loaded-dot" /> {useMock ? "Mock" : "Loaded"}</>}</span></div></header>{error && <div className="error">{error}</div>}<Kpis kpis={displayData.kpis} /><section className="panel timeline-panel"><div className="section-heading"><span className="overline">TIMELINE</span><span className="muted">window navigator · {displayData.citations.length} citations</span></div><Timeline citations={displayData.citations} selectedSpeaker={selectedSpeaker} topicSearchActive={topicSearchActive} similarityThreshold={appliedThreshold} /></section><section><div className="section-heading"><span className="overline">SPEAKERS</span></div><Speakers speakers={displayData.speakers} selected={selectedSpeaker} onSelect={setSelectedSpeaker} /></section></main>;
}

createRoot(document.getElementById("root")).render(<App />);
