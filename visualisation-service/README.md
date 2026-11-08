# NewsAnalysis Visualisation

The visualisation service is available at `http://localhost:3002` after
`docker compose up --build visualisation-service`.

The page starts in mock-data mode so visual behavior can be reviewed without
waiting for production data. The `live data` switch enables
`data-storage` through `GET /visualization/query`.

- free-form topic phrases matched against canonical topic embeddings;
- optional `general_topic_field` filtering;
- configurable `min_similarity`;
- speaker prominence, stance distribution, citation timelines, and evidence links;
- a fixed-height Apache ECharts Canvas timeline with native horizontal zoom,
  compressed empty periods, confidence-weighted bubbles, and inconsistency
  severity highlighting.

The timeline preserves source traceability: hovering a bubble shows the
original quote, article date, and source link when available. Mock mode
contains long gaps, consecutive citations, vague sources, an organization,
and resolved/unresolved inconsistency examples for UI review.
