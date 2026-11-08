export function createEventDeduper() {
  const seen = new Set();
  return event => {
    const key = event.event_id || event.sequence;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  };
}
