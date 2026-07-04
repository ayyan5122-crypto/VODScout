from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from gui import VODScoutApp
from settings import TEMP_DIR, ensure_project_directories


def configure_logging() -> None:
    ensure_project_directories()
    log_file = TEMP_DIR / "vod_scout.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def main() -> None:
    configure_logging()
    app = VODScoutApp()
    app.mainloop()


if __name__ == "__main__":
    main()
