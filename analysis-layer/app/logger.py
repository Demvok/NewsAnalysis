import logging, time, sys, os
from config import FIELD_LENGTH_POLICY, LOGGING_LEVEL, CONSOLE_LOG

if LOGGING_LEVEL == "DEBUG":
    LEVEL = logging.DEBUG
elif LOGGING_LEVEL == "INFO":
    LEVEL = logging.INFO
elif LOGGING_LEVEL == "WARNING":
    LEVEL = logging.WARNING
elif LOGGING_LEVEL == "ERROR":
    LEVEL = logging.ERROR
elif LOGGING_LEVEL == "CRITICAL":
    LEVEL = logging.CRITICAL
else:
    raise ValueError(f"Unknown logging level: {LOGGING_LEVEL}")

SAVE_TO = './logs/'
if not os.path.exists(SAVE_TO):
    os.makedirs(SAVE_TO)

def timeUsed(start_time, end_time) -> str:
    duration = end_time - start_time
    return f"{duration:.3f}s"

class CustomFormatter(logging.Formatter):
    def format(self, record):
        # Set default value for execution_time if not provided
        if not hasattr(record, 'execution_time'):
            record.execution_time = 'N/A'
        return super().format(record)

# Remove all existing handlers to avoid conflicts
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

def setup_logger(name: str, log_file: str) -> logging.Logger:
    """
    Sets up a logger with individual file logging and console log.
    
    :param name: Name of the logger.
    :param log_file: Path to the log file for this logger.
    :return: Configured Logger object.
    """
    name = name.ljust(25, ' ')
    # FORCE RESET: Remove any existing logger with this name
    if name in logging.Logger.manager.loggerDict:
        del logging.Logger.manager.loggerDict[name]
    
    # Create a fresh logger instance
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.setLevel(LOGGING_LEVEL)
    
    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # File handler for individual file logging
    file_path = os.path.join(SAVE_TO, log_file)
    file_handler = logging.FileHandler(file_path)
    formatter = CustomFormatter('%(asctime)s - %(name)s - %(levelname)s - %(execution_time)s - %(message)s')
    formatter.converter = lambda *args: time.localtime(*args)
    formatter.default_time_format = '%Y-%m-%d %H:%M:%S'
    formatter.default_msec_format = ''
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Add console handler
    if not CONSOLE_LOG:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    if FIELD_LENGTH_POLICY == "IGNORE":
        logger.debug("Ignoring field length policy")
    elif FIELD_LENGTH_POLICY == "RETRY":
        logger.debug("Retrying field length policy")
    elif FIELD_LENGTH_POLICY == "REFINE":
        logger.debug("Refining field length policy")
    elif FIELD_LENGTH_POLICY == "TRUNCATE":
        logger.debug("Truncating field length policy")
    else:
        logger.error(f"Unknown field length policy: {FIELD_LENGTH_POLICY}")
        raise ValueError(f"Unknown field length policy: {FIELD_LENGTH_POLICY}")


    return logger

