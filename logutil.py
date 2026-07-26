"""
File logging for CP-Agent.

The emoji print() output remains the user-facing CLI UI; this module adds a
DEBUG-level file log (untruncated tool results, LLM call metrics, full
tracebacks) for post-mortem debugging.
"""
import logging
from pathlib import Path

_configured = False


def setup(log_file: str | Path = "cp_agent.log") -> None:
    """Attach a DEBUG file handler to the root logger. Idempotent."""
    global _configured
    if _configured:
        return
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
