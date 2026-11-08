import test from "node:test";
import assert from "node:assert/strict";
import { createEventDeduper } from "../src/metrics.js";

test("telemetry events are accepted once by event id", () => {
  const accepts = createEventDeduper();
  assert.equal(accepts({ event_id: "event-1" }), true);
  assert.equal(accepts({ event_id: "event-1" }), false);
  assert.equal(accepts({ event_id: "event-2" }), true);
});

test("SSE sequence is used when event id is absent", () => {
  const accepts = createEventDeduper();
  assert.equal(accepts({ sequence: 4 }), true);
  assert.equal(accepts({ sequence: 4 }), false);
});
