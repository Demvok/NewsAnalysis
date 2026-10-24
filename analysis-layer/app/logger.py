"""Application logging configuration for the analysis service."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path


_CONFIGURED_SERVICES: set[str] = set()
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def _environment_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def _level(value: str, setting_name: str) -> int:
    normalized = value.strip().upper()
    level = getattr(logging, normalized, None)
    if not isinstance(level, int):
        raise ValueError(
            f"{setting_name} must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL; "
            f"got {value!r}"
        )
    return level


def _configured_level(name: str, fallback: str) -> int:
    setting_name = f"LOG_LEVEL_{_environment_key(name)}"
    return _level(os.getenv(setting_name, fallback), setting_name)


def configure_logging(service_name: str) -> logging.Logger:
    """Configure console and file logging once for a service."""
    service_name = _environment_key(service_name).lower()
    service_logger_name = f"newsanalysis.{service_name}"
    service_logger = logging.getLogger(service_logger_name)

    if service_name in _CONFIGURED_SERVICES:
        return service_logger

    default_level = os.getenv("LOG_LEVEL", "INFO")
    service_level = _configured_level(service_name, default_level)
    log_directory = Path(os.getenv("LOG_DIR", "/app/logs"))
    log_directory.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT)
    formatter.converter = time.gmtime

    file_handler = logging.FileHandler(
        log_directory / f"{service_name}.log",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.NOTSET)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.NOTSET)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.NOTSET)
    for handler in root_logger.handlers[:]:
        if getattr(handler, "_newsanalysis_handler", False):
            root_logger.removeHandler(handler)
            handler.close()
    for handler in (file_handler, console_handler):
        handler._newsanalysis_handler = True
        root_logger.addHandler(handler)

    service_logger.setLevel(service_level)
    service_logger.propagate = True
    _CONFIGURED_SERVICES.add(service_name)
    return service_logger


def get_logger(module_name: str, service_name: str) -> logging.Logger:
    """Return a module logger with an optional ``LOG_LEVEL_<MODULE>`` override."""
    service_name = _environment_key(service_name).lower()
    configure_logging(service_name)
    logger = logging.getLogger(f"newsanalysis.{service_name}.{module_name}")
    service_default = os.getenv(
        f"LOG_LEVEL_{_environment_key(service_name)}",
        os.getenv("LOG_LEVEL", "INFO"),
    )
    logger.setLevel(_configured_level(module_name, service_default))
    return logger
