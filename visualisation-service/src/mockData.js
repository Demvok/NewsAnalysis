const people = [
  ["mock-alex", "Alex Morgan", "PERSON", "Energy policy advisor", ["A. Morgan", "Alex J. Morgan"]],
  ["mock-riley", "Riley Chen", "PERSON", "Public health researcher", ["R. Chen"]],
  ["mock-wef", "World Policy Forum", "ORGANIZATION", "International policy organization", ["WPF"]],
  ["mock-source", "Unnamed government source", "ANONYMOUS_SOURCE", null, []],
];

const dates = ["2024-01-15", "2024-01-22", "2024-02-02", "2024-02-09", "2024-06-18", "2024-06-25", "2024-10-04", "2024-10-12", "2025-01-09", "2025-01-16", "2025-05-21", "2025-05-29", "2025-06-06", "2025-06-13", "2025-08-19", "2025-08-26", "2025-10-03", "2025-10-10", "2026-01-08", "2026-01-15", "2026-03-20", "2026-03-27", "2026-06-05", "2026-06-12"];
const stances = [0.65, 0.72, -0.55, -0.4, 0.1, 0.22, 0.75, 0.62, -0.8, -0.65, 0.35, 0.48, 0.18, 0.28, -0.25, -0.12, 0.8, 0.7, -0.7, -0.45, 0.05, 0.2, 0.58, 0.66];

const citations = dates.map((date, index) => {
  const [personId, personName, personType] = people[index % people.length];
  const stanceValue = stances[index];
  const citationUuid = `mock-citation-${index + 1}`;
  const inconsistency = index === 4 || index === 8
    ? { id: "mock-inconsistency-1", classification: "CONTRADICTION", severity: "HIGH", comment: "The later statement reverses the earlier position on the same proposal.", citation_a_uuid: "mock-citation-5", citation_b_uuid: "mock-citation-9" }
    : index === 5 || index === 9
      ? { id: "mock-inconsistency-2", classification: "CONSISTENT", severity: "NONE", comment: "These statements remain compatible after considering their different scopes.", citation_a_uuid: "mock-citation-6", citation_b_uuid: "mock-citation-10" }
      : index === 10 || index === 14
        ? { id: "mock-inconsistency-3", classification: "POSITION_CHANGE", severity: "MEDIUM", comment: "The speaker moved from limited support to a broader implementation after new evidence.", citation_a_uuid: "mock-citation-11", citation_b_uuid: "mock-citation-15" }
        : index === 11 || index === 15
          ? { id: "mock-inconsistency-4", classification: "CONTRADICTION", severity: "CRITICAL", comment: "The two claims make mutually exclusive commitments about the same funding decision.", citation_a_uuid: "mock-citation-12", citation_b_uuid: "mock-citation-16" }
          : index === 18 || index === 22
            ? { id: "mock-inconsistency-5", classification: "CONTRADICTION", severity: "LOW", comment: "The wording is in tension, but the available context leaves a modest contradiction signal.", citation_a_uuid: "mock-citation-19", citation_b_uuid: "mock-citation-23" }
      : null;
  return {
    citation_uuid: citationUuid,
    person_uuid: personId,
    person_name: personName,
    person_type: personType,
    topic_uuid: `mock-topic-${index % 3}`,
    topic_name: ["energy transition", "public health funding", "regional security"][index % 3],
    topic_names: [["energy transition", "climate policy"], ["public health funding", "healthcare access"], ["regional security", "defense policy"]][index % 3],
    general_topic_field: "Mock policy",
    published_at: `${date}T12:00:00Z`,
    stance: stanceValue > 0 ? "SUPPORT" : "OPPOSE",
    stance_value: stanceValue,
    direction: stanceValue > 0 ? "SUPPORT" : "OPPOSE",
    endorsement: 1 + (index % 4),
    commitment: 1 + ((index + 1) % 4),
    conditionality: index % 3,
    relevancy_score: 0.55 + (index % 5) * 0.1,
    stance_confidence: 0.6 + (index % 4) * 0.1,
    quote: `Mock evidence statement ${index + 1} about the policy direction.`,
    summarized_quote: `Summary: statement ${index + 1} expresses a ${stanceValue >= 0 ? "supportive" : "critical"} position on the topic.`,
    article_title: `Mock article ${index + 1}`,
    article_url: "https://example.com/newsanalysis-mock",
    inconsistency,
  };
});

const speakerMap = new Map(people.map(([id, name, person_type, affiliation, aliases]) => [id, { person_uuid: id, name, person_type, affiliation, aliases, citations: 0, stances: [] }]));
citations.forEach(item => {
  const speaker = speakerMap.get(item.person_uuid);
  speaker.citations += 1;
  speaker.stances.push(item.stance_value);
});
const speakers = [...speakerMap.values()].map(speaker => {
  const average = speaker.stances.reduce((sum, value) => sum + value, 0) / speaker.stances.length;
  return { ...speaker, average_stance: average, std_stance: Math.sqrt(speaker.stances.reduce((sum, value) => sum + (value - average) ** 2, 0) / speaker.stances.length) };
}).sort((a, b) => b.citations - a.citations);
const values = citations.map(item => item.stance_value).sort((a, b) => a - b);
export const MOCK_DATA = {
  speakers,
  citations,
  kpis: { average_stance: values.reduce((sum, value) => sum + value, 0) / values.length, median_stance: (values[5] + values[6]) / 2, std_stance: 0.55, inconsistency_frequency: 10 / values.length },
  general_topic_fields: [{ name: "Mock policy", article_count: citations.length }],
  data_quality: { excluded_row_count: 0 },
};
