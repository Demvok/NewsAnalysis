# Sets LLM policy on dealing with outputs longer than supported. Supported policies are: 'IGNORE' / 'RETRY' / 'REFINE' / 'TRUNCATE'.
# Ignore will skip the error, retry will try again (3) times, refine will call LLM to shrink the length and truncate will cut off extra characters (not recommended).
FIELD_LENGTH_POLICY = 'REFINE'

EVENTS_HOTNESS_THRESHOLD = 0.6  # Threshold for selecting events based on hotness score
OPINION_HOTNESS_THRESHOLD = 0.6  # Threshold for selecting opinions based on hotness score

INCONSISTENCY_TOLERANCE = 0.4 # Used for filtering off consistent opinions, possibly can be from 0 to 2
OPINION_FRESHNESS_THRESHOLD = 365  # Filters off opinions older than n days from given date of event, basically sets the time for person to safely change their opinion

