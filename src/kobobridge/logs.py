"""Logging, set up so that a container shows what the eReader is doing.

Two things went wrong before this module existed, and both hid every other problem.

The server runs under waitress, which writes nothing per request, so the bridge answered
thousands of calls in silence. There was no way for anyone to tell a device that never asked
for a cover from one that asked and got an error, which is the first question any report about
a missing cover has to answer. Every request is now logged with its status, its size and how
long it took.

The output was also block buffered, because a container has no terminal attached. Lines sat in
the buffer instead of reaching ``docker logs``, so even the startup banner looked missing. The
image now sets ``PYTHONUNBUFFERED``, and this module flushes on every record as well, so the
log is honest whatever the image does.
"""

import logging
import os
import sys
import time

LOGGER_NAME = "kobobridge"

DEFAULT_LEVEL = "info"

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class _FlushingHandler(logging.StreamHandler):
    """A stream handler that does not let a record wait in a buffer."""

    def emit(self, record):
        super().emit(record)
        try:
            self.flush()
        except ValueError:  # the stream went away under us, nothing to do about it
            pass


def level_from_env(env=None):
    env = os.environ if env is None else env
    name = str(env.get("KOBOBRIDGE_LOG_LEVEL", DEFAULT_LEVEL)).strip().lower()
    return LEVELS.get(name, logging.INFO)


def configure(level=None, stream=None):
    """Attach one handler to the bridge logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level if level is not None else level_from_env())
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = _FlushingHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


def get_logger():
    return logging.getLogger(LOGGER_NAME)


def request_logging(app):
    """Log one line per request the device makes, with status, size and duration."""

    logger = get_logger()

    @app.before_request
    def _mark_start():
        from flask import g

        g.kobobridge_started = time.monotonic()

    @app.after_request
    def _log_request(response):
        from flask import g, request

        started = getattr(g, "kobobridge_started", None)
        elapsed = (time.monotonic() - started) * 1000.0 if started else 0.0
        length = response.headers.get("Content-Length") or "-"
        # The device token is a secret and belongs in no log file.
        path = _hide_token(request.full_path if request.query_string else request.path)
        logger.info(
            "%s %s -> %s %s bytes in %.0f ms",
            request.method,
            path,
            response.status_code,
            length,
            elapsed,
        )
        return response

    return app


def _hide_token(path):
    """Replace the device token in a path with a marker, so a log can be pasted in public."""
    parts = path.split("/")
    for index, part in enumerate(parts):
        if part == "kobo" and index + 1 < len(parts) and parts[index + 1]:
            parts[index + 1] = "<token>"
            break
    return "/".join(parts)
