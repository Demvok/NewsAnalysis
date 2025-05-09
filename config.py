# Sets LLM policy on dealing with outputs longer than supported. Supported policies are: 'IGNORE' / 'RETRY' / 'REFINE' / 'TRUNCATE'.
# Ignore will skip the error, retry will try again (3) times, refine will call LLM to shrink the length and truncate will cut off extra characters (not recommended).
FIELD_LENGTH_POLICY = 'REFINE'

EVENTS_HOTNESS_THRESHOLD = 0.6  # Threshold for selecting events based on hotness score
OPINION_HOTNESS_THRESHOLD = 0.6  # Threshold for selecting opinions based on hotness score