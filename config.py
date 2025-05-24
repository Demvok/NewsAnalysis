# Sets LLM policy on dealing with outputs longer than supported. Supported policies are: 'IGNORE' / 'RETRY' / 'REFINE' / 'TRUNCATE'.
# Ignore will skip the error, retry will try again (3) times, refine will call LLM to shrink the length and truncate will cut off extra characters (not recommended).
FIELD_LENGTH_POLICY = 'REFINE'
MAX_RETRIES = 3
N_THREADS = 4 # Number of threads for parallel processing (only for the main_parallelised.py file)

MARK_PROCESSED = True # If True, mark the chunk as processed in the database
WRITE_TO_DB = True # If True, write the results to the database

CONSOLE_LOG = True # If True, log to console
LOGGING_LEVEL = 'INFO' # Logging level for the logger. Options are: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'

EVENTS_HOTNESS_THRESHOLD = 0.8  # Threshold for selecting events based on hotness score
OPINION_HOTNESS_THRESHOLD = 0.6  # Threshold for selecting opinions based on hotness score

INCONSISTENCY_TOLERANCE = 0.4 # Used for filtering off consistent opinions, possibly can be from 0 to 2
OPINION_FRESHNESS_THRESHOLD = 365  # Filters off opinions older than n days from given date of event, basically sets the time for person to safely change their opinion

MIXED_INTERVAL = 0.3 # Interval (+- from 0) for telling if opinion is mixed

def get_weighted_sentiment(df):  # For inconsistency analysis
    df['index'] = df.index

    def calculate_valuability(index, size, method=3, minimal_value=0.2, slope=0.66):
        """
        Calculate the valuability of a chunk based on its index in the list of chunks.
        :param index: The index of the chunk in the list.
        :param size: The total number of chunks.
        :param method: The method to use for calculating valuability (1-linear, 2-exponential, or 3-degrading constant).
        :param minimal_value: The minimum valuability value.
        :param slope: The slope for the exponential method.
        :return: The valuability of the chunk.
        """
        if method == 1: # Linear case
            return 1 - (index - 1) * (1-minimal_value)/(size - 1)
        elif method == 2: # Exponential case
            return minimal_value + (1-minimal_value) * (2.71828182846)**(-slope * (index - 1))
        elif method == 3: # Degrading linear case
            return 1 - slope ** (size - index) + minimal_value
    
    df['valuability'] = df['index'].apply(lambda x: calculate_valuability(x, df.shape[0], method=3))
    df['weighted_sentiment'] = df['valuability'] * df['sentiment_score']
    
    return df.drop(['index', 'valuability'], axis=1)

def get_stance(stance_int: int):
    """
    Get the stance based on the stance intensity.
    :param stance_int: The stance intensity.
    :return: The stance.
    """
    if stance_int > MIXED_INTERVAL:
        return 'pro'
    elif stance_int < -MIXED_INTERVAL:
        return 'against'
    else:
        return 'mixed'

def get_deviation_score(deviation: float):
    """
    Get the deviation score based on the deviation value.
    :param deviation: The deviation value.
    :return: The deviation score.
    """
    if deviation < 0.1:
        return 'low'
    elif 0.1 <= deviation < 0.3:
        return 'medium'
    else:
        return 'high'
    

def calculate_opinion_hotness(relevancy, contribution, controversy):
    """Calculate opinion hotness as a weighted sum of scores."""
    return 0.25*relevancy + 0.35*contribution + 0.4*controversy

def calculate_event_hotness(relevance, influence, novelty):
    """Calculate event hotness as a weighted sum of scores."""
    return 0.15*relevance + 0.5*influence + 0.35*novelty