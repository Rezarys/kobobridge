"""The application, and the small object that holds the library in front of it."""

import threading
import time

from flask import Flask, jsonify

from .abs import Audiobookshelf, AudiobookshelfError
from .config import Config
from .logs import configure, get_logger, request_logging
from .resources import build_resources
from .state import EbookSizeStore, ReadingStateStore, size_cache_path
from . import kobo

# How long a library listing is reused before the server is asked again. A sync is a burst of
# requests, and one listing serves the whole burst.
CACHE_SECONDS = 30


class Bridge:
    """The library as the device sees it, cached briefly and refreshed on demand."""

    def __init__(self, config, client=None, reading_states=None, clock=time.monotonic):
        self.config = config
        self.client = client or Audiobookshelf(
            config.abs_url,
            config.abs_token,
            timeout=config.timeout,
            sizes=EbookSizeStore(size_cache_path()),
        )
        self.reading_states = (
            reading_states if reading_states is not None else ReadingStateStore()
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._books = []
        self._by_uuid = {}
        self._fetched_at = None
        # True between the moment the bridge tells the reader there is more to come and the
        # moment it tells it there is not. See :meth:`listing`.
        self.sync_unfinished = False

    def resources(self, bridge_prefix, bridge_root):
        return build_resources(bridge_prefix, bridge_root)

    def refresh(self, force=False):
        with self._lock:
            fresh = (
                self._fetched_at is not None
                and self._clock() - self._fetched_at < CACHE_SECONDS
            )
            if fresh and not force:
                return self._books
            logger = get_logger()
            started = time.monotonic()
            books = self.client.all_books(self.config.library_id)
            self._books = books
            self._by_uuid = {book.uuid: book for book in books}
            self._fetched_at = self._clock()
            # On a large library this is the slow step, and it holds the lock, so a cover
            # request that arrives meanwhile waits here. Saying so makes that visible.
            logger.info(
                "library listed: %s ebooks in %.1f s",
                len(books),
                time.monotonic() - started,
            )
            return self._books

    def listing(self):
        """The listing as it already stands, without asking the server again.

        A library too large to send in one answer is sent over several rounds, and the reader
        comes straight back for the next one. Those rounds are the reader walking a cursor
        through a list the bridge has already promised it, so re-listing between them is both
        slow and wrong: on two thousand books it turned one listing into twenty, and books
        could shift under the cursor between rounds. Only a library that has never been listed
        at all is fetched here.
        """
        with self._lock:
            if self._fetched_at is not None:
                return self._books
        return self.refresh(force=True)

    def book_by_uuid(self, book_uuid):
        if self._fetched_at is None:
            self.refresh()
        return self._by_uuid.get(book_uuid)


def create_app(config=None, client=None, reading_states=None):
    """Build the WSGI application. ``config`` defaults to whatever the environment says."""
    config = config or Config.from_env()
    configure()
    app = Flask(__name__)
    # The device is happier reading the keys in the order they were written.
    if hasattr(app, "json"):
        app.json.sort_keys = False
    app.extensions["kobobridge"] = Bridge(
        config, client=client, reading_states=reading_states
    )
    app.register_blueprint(kobo.bp)

    @app.get("/health")
    def health():
        """Enough to tell a container orchestrator whether the upstream answers."""
        try:
            count = app.extensions["kobobridge"].client.ping()
        except AudiobookshelfError as error:
            return jsonify({"status": "unreachable", "detail": str(error)}), 503
        return jsonify({"status": "ok", "libraries": count})

    @app.get("/")
    def index():
        return jsonify(
            {
                "name": "kobobridge",
                "setup": "point your eReader at the address printed by 'kobobridge run'",
            }
        )

    return request_logging(app)
