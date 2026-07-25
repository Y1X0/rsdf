"""Structured logging setup.

Every agent and service logs through `get_logger(__name__)` and emits
key-value structured events (never bare strings for anything decision- or
error-related) so that "why did the system do X" is answerable from logs
alone. This directly implements the "no silent failures / every decision
traceable" requirement: agents must log at minimum a start event, a
decision/result event, and — on any exception — an error event before
re-raising. See agents/base.py for the enforcement helper.
"""

import logging
import sys

import structlog

from content_factory.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
