"""The application, and the small object that holds the library in front of it."""

import threading
import time
from dataclasses import replace

from flask import Flask, jsonify

from .abs import Audiobookshelf, AudiobookshelfError
from .config import Config
from .logs import configure, get_logger, request_logging
from .resources import build_resources
from .state import (
    CollectionSeenStore,
    EbookSizeStore,
    ReadingStateStore,
    collection_seen_path,
    size_cache_path,
)
from . import kobo, license

# How long a library listing is reused before the server is asked again. A sync is a burst of
# requests, and one listing serves the whole burst.
CACHE_SECONDS = 30


class Bridge:
    """The library as the device sees it, cached briefly and refreshed on demand."""

    def __init__(
        self, config, client=None, reading_states=None, clock=time.monotonic, unlock=license.unlock,
        collection_seen=None,
    ):
        self.config = config
        self.collection_seen = (
            collection_seen
            if collection_seen is not None
            else CollectionSeenStore(collection_seen_path())
        )
        # The collection actually synced: None means the whole library, which is the free
        # default and what the bridge falls back to whenever the license does not check out.
        self.collection_id = None
        self.license_note = None
        if config.collection_id:
            try:
                found = unlock(config.license_key)
            except license.LicenseError as error:
                self.license_note = "syncing the whole library, collection not applied: {0}".format(error)
                get_logger().warning("%s", self.license_note)
            else:
                self.collection_id = config.collection_id
                self.license_note = "syncing collection {0}, key bought by {1}".format(
                    config.collection_id, found.customer or "the key holder"
                )
                get_logger().info("%s", self.license_note)
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
            if self.collection_id:
                wanted = self.client.collection_item_ids(self.collection_id)
                books = [book for book in books if book.item_id in wanted]
                seen = self.collection_seen.first_seen(
                    self.collection_id, [book.item_id for book in books]
                )
                # A book that joined the collection after the reader's last sync counts as
                # added at that moment, or the cursor would already be past it.
                books = [
                    replace(
                        book,
                        created=max(book.created, seen[book.item_id]),
                        modified=max(book.modified, seen[book.item_id]),
                    )
                    for book in books
                ]
                books.sort(key=lambda book: (book.modified, book.item_id))
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


def create_app(config=None, client=None, reading_states=None, unlock=license.unlock):
    """Build the WSGI application. ``config`` defaults to whatever the environment says."""
    config = config or Config.from_env()
    configure()
    app = Flask(__name__)
    # The device is happier reading the keys in the order they were written.
    if hasattr(app, "json"):
        app.json.sort_keys = False
    app.extensions["kobobridge"] = Bridge(
        config, client=client, reading_states=reading_states, unlock=unlock
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
