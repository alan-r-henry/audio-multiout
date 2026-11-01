"""File logging for a windowless app.

Under pythonw.exe there is no console, so a swallowed exception or a failed
device open leaves no trace at all. Everything worth knowing later goes to
%APPDATA%\\audio-multiout\\app.log (rotated, so it can't grow without bound).
"""

import logging
import logging.handlers
import os
import sys
import threading

import config

LOG_PATH = os.path.join(config.APP_DIR, "app.log")
_NAME = "audio-multiout"
_configured = False

# Library-style default: silent until setup() attaches the file handler, so
# tests and --check don't spray records on stderr.
logging.getLogger(_NAME).addHandler(logging.NullHandler())


def get(name=None):
    return logging.getLogger(_NAME if name is None else f"{_NAME}.{name}")


def setup(path=None):
    """Attach the rotating file handler once; also catches uncaught errors."""
    global _configured
    logger = logging.getLogger(_NAME)
    if _configured:
        return logger
    _configured = True
    logger.setLevel(logging.INFO)
    try:
        target = path or LOG_PATH
        os.makedirs(os.path.dirname(target), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())  # logging must never be fatal

    def _excepthook(exc_type, exc, tb):
        logger.error("uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        logger.error("uncaught exception in thread %s",
                     args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return logger
