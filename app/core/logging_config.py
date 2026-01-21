"""
Centralized logging configuration for the Industrial RAG application.

Logging Levels:
- INFO: Critical business milestones (startup, request completion, key decisions)
- WARNING: Abnormal but operable conditions (degraded operation, missing data, retries)
- DEBUG: Complex algorithm troubleshooting (disabled by default in production)
- ERROR: Failures requiring attention
"""
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# Create logs directory if it doesn't exist
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Log file path with timestamp
LOG_FILE = LOGS_DIR / f"app_{datetime.now().strftime('%Y%m%d')}.log"

# Define log format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_level: str = None) -> logging.Logger:
    """
    Configure logging for the application.

    Args:
        log_level: Logging level. If None, uses LOG_LEVEL env var.
                   Production default: INFO, Debug: DEBUG

    Returns:
        Configured logger instance
    """
    # Unified entry-level configuration
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    level = getattr(logging, log_level.upper(), logging.INFO)

    # Get the root logger
    logger = logging.getLogger("industrial_rag")
    logger.setLevel(level)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # File handler - follows configured level
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Console handler - follows configured level
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.

    Args:
        name: Logger name (typically __name__ of the module)

    Returns:
        Logger instance
    """
    return logging.getLogger(f"industrial_rag.{name}")
