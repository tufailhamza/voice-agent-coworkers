import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


class Logger:
    """A simple logger class with rotating file handlers and a global prefix."""

    global_prefix: str = "4595"

    @classmethod
    def set_global_prefix(cls, prefix: str):
        cls.global_prefix = prefix

    def __init__(
        self,
        tag: str = "agent-worker",
        log_level: str = "INFO",
        log_dir: str = "logs",
        max_file_size_mb: int = 1,
        backup_count: int = 3,
        log_format: str = None,
    ):
        self.name = tag
        self.log_level = log_level.upper()
        self.log_dir = Path(log_dir)
        self.max_file_size_mb = max_file_size_mb
        self.backup_count = backup_count

        self.log_format = log_format or (
            "%(asctime)s - %(name)s - %(levelname)s - "
            "%(filename)s:%(lineno)d - %(funcName)s - %(message)s"
        )

        self._setup_logger()

    def _setup_logger(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(getattr(logging, self.log_level))

        # Only add handlers if they don't already exist
        if not self.logger.handlers:
            formatter = logging.Formatter(self.log_format)
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

            general_log_file = self.log_dir / "app.log"
            file_handler = RotatingFileHandler(
                general_log_file,
                maxBytes=self.max_file_size_mb * 1024 * 1024,
                backupCount=self.backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

        # Prevent propagation to root logger to avoid duplicates
        self.logger.propagate = False

        # self.logger.info(f"Logger '{self.name}' configured - Level: {self.log_level}")

    def _prefixed(self, msg: str) -> str:
        return f"{Logger.global_prefix} {msg}"

    def info(self, msg: str, *args, **kwargs):
        return self.logger.info(self._prefixed(msg), *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        return self.logger.debug(self._prefixed(msg), *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        return self.logger.warning(self._prefixed(msg), *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        return self.logger.error(self._prefixed(msg), *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        return self.logger.critical(self._prefixed(msg), *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.logger, name)


if __name__ == "__main__":
    Logger.set_global_prefix("MYGLOBAL")

    logger1 = Logger(tag="logger1")
    logger2 = Logger(tag="logger2")

    logger1.info("First message from logger1")
    logger2.error("Error from logger2")

    Logger.set_global_prefix("NEWPREFIX")
    logger1.info("Debug after prefix changed")
    logger1.info("Debug after prefix changed")
    logger1.info("Debug after prefix changed")
    logger2.info("Warning after prefix changed")
    logger2.warning("Warning after prefix changed")
    logger2.warning("Warning after prefix changed")
