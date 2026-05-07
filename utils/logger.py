import logging
import os
from logging.handlers import RotatingFileHandler

_root_initialized = False


def _init_root_logger() -> None:
    """Configure the root logger once with a single shared file/console handler.

    Previous design instantiated a RotatingFileHandler per module-level logger.
    With 30+ modules each holding its own fd to logs/app.log, the first rotation
    only swapped one fd; the rest kept writing to the renamed (deleted) file,
    accumulating dozens of zombie fds and silently losing log lines after
    rotation. Putting the handler on the root logger and propagating from
    module loggers eliminates that race.
    """
    global _root_initialized
    if _root_initialized:
        return

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Drop any pre-existing handlers (e.g. from --reload re-import) so we
    # don't double-register and re-introduce the duplicate-fd problem.
    for h in list(root.handlers):
        root.removeHandler(h)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 외부 라이브러리 노이즈 제거 (DEBUG로 매 프레임 찍히는 것들)
    for noisy in ("websockets", "websockets.client", "websockets.protocol",
                  "urllib3", "urllib3.connectionpool",
                  "asyncio", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _root_initialized = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-specific logger that propagates to the shared root handler."""
    _init_root_logger()
    logger = logging.getLogger(name)
    # Wipe legacy per-module handlers on hot-reload, force propagation to root.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.propagate = True
    return logger
