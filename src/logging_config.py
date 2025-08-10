import logging
from logging.handlers import TimedRotatingFileHandler
from src import config

class ColorFormatter(logging.Formatter):
    """Custom formatter to colorize log levels in console output."""
    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[41m",  # Red background
    }
    RESET = "\033[0m"

    def format(self, record):
        # Store original level name for file logs
        levelname_orig = record.levelname
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        output = formatter.format(record)

        # Restore original so file logs aren't colorized
        record.levelname = levelname_orig
        return output


def setup_logging(level=logging.INFO):
    """
    Configure logging to output
    - Colorized console logs
    - Rolling file logs
    """
    log_dir = config.PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "folio.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplication in notebooks
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler (colorized)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColorFormatter())
    root_logger.addHandler(console_handler)

    # File handler
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root_logger.addHandler(file_handler)

    logging.info(f"Logging initialized with level: {logging.getLevelName(level)}")
