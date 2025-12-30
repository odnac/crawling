import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime


def setup_logger(side: str, ticker: str) -> logging.Logger:
    side_name = "buy" if side.lower() == "bid" else "sell"
    ticker_name = ticker.upper()

    logger_name = f"FollowMM_{ticker_name}_{side_name}"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    base_filename = os.path.join(log_dir, f"{side_name}_{ticker_name}.log")

    handler = TimedRotatingFileHandler(
        filename=base_filename,
        when="midnight",
        interval=1,
        backupCount=0,
        encoding="utf-8",
    )

    handler.suffix = "%Y-%m-%d"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.addHandler(console_handler)

    return logger
