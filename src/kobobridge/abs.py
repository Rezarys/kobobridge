"""A read only client for the Audiobookshelf API.

Every call in this module is a GET. The bridge never writes to your library: no metadata
update, no progress push, no deletion. :meth:`Audiobookshelf._get` is the single door out, and
a test asserts that no other HTTP verb appears in this file.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import requests

# Formats the eReader reads natively. Anything else in the library is skipped rather than
# converted, because converting would mean writing somewhere.
EBOOK_FORMATS = ("epub", "kepub")

# A fixed namespace, so a given library item always maps to the same identifier. The device
# expects UUIDs and Audiobookshelf hands out identifiers like "li_abc123", so they are derived
# rather than stored. No database, and the mapping survives a restart.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "kobobridge")

_SERIES_INDEX = re.compile(r"^(?P<name>.*?)\s*#\s*(?P<index>[0-9]+(?:\.[0-9]+)?)\s*$")

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def item_uuid(item_id):
    """Map an Audiobookshelf item id onto a stable UUID."""
    return str(uuid.uuid5(NAMESPACE, str(item_id)))


def _moment(value):
    """Audiobookshelf timestamps are milliseconds since the epoch."""
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return EPOCH


def _language(value):
    """Reduce a free text language field to the two letter code the device expects."""
    if not value:
        return "en"
    head = str(value).strip().lower().replace("_", "-").split("-")[0]
    return head if len(head) == 2 and head.isalpha() else "en"


def _split_series(value):
    """``"The Expanse #3"`` becomes ``("The Expanse", 3.0)``."""
    if not value:
        return None, None
    match = _SERIES_INDEX.match(str(value).strip())
    if not match:
        return str(value).strip(), None
    return match.group("name"), float(match.group("index"))


@dataclass
class Book:
    """One ebook, flattened out of an Audiobookshelf library item."""

    item_id: str
    title: str
    authors: List[str] = field(default_factory=list)
    description: Optional[str] = None
    publisher: Optional[str] = None
    published: Optional[str] = None
    language: str = "en"
    series: Optional[str] = None
    series_index: Optional[float] = None
    size: int = 0
    ebook_format: str = "epub"
    created: datetime = EPOCH
    modified: datetime = EPOCH

    @property
    def uuid(self):
        return item_uuid(self.item_id)

    @property
    def is_kepub(self):
        return self.ebook_format == "kepub"

    @classmethod
    def from_item(cls, item):
        media = item.get("media") or {}
        metadata = media.get("metadata") or {}
        series, series_index = _split_series(metadata.get("seriesName"))
        published = metadata.get("publishedDate") or metadata.get("publishedYear")
        ebook_format = str(media.get("ebookFormat") or "").lower().lstrip(".")
        # Audiobookshelf reports a kepub as "kepub.epub" on some versions.
        if ebook_format.startswith("kepub"):
            ebook_format = "kepub"
        size = media.get("size") or 0
        ebook_file = media.get("ebookFile") or {}
        file_metadata = ebook_file.get("metadata") or {}
        if file_metadata.get("size"):
            size = file_metadata["size"]
        created = _moment(item.get("addedAt"))
        modified = _moment(item.get("updatedAt"))
        return cls(
            item_id=str(item.get("id") or ""),
            title=str(metadata.get("title") or "Untitled"),
            authors=[a for a in [metadata.get("authorName")] if a],
            description=metadata.get("description") or None,
            publisher=metadata.get("publisher") or None,
            published=str(published) if published else None,
            language=_language(metadata.get("language")),
            series=series,
            series_index=series_index,
            size=int(size or 0),
            ebook_format=ebook_format or "epub",
            created=created,
            # A library item whose addedAt is later than its updatedAt would make the cursor
            # walk backwards, so the modified stamp is never allowed to trail the created one.
            modified=max(modified, created),
        )


class AudiobookshelfError(RuntimeError):
    """The Audiobookshelf server refused or could not answer a request."""


class Audiobookshelf:
    """Read only access to one Audiobookshelf server."""

    def __init__(self, base_url, token, timeout=30.0, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer {0}".format(token)})

    def _get(self, path, **kwargs):
        """The only outbound call in the project. Read only by construction."""
        url = "{0}{1}".format(self.base_url, path)
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
        except requests.RequestException as error:
            raise AudiobookshelfError("cannot reach {0}: {1}".format(url, error))
        if response.status_code >= 400:
            raise AudiobookshelfError(
                "{0} answered {1} for {2}".format(self.base_url, response.status_code, path)
            )
        return response

    def ping(self):
        """Confirm the token works, and return how many libraries the server exposes."""
        payload = self._get("/api/libraries").json()
        return len(payload.get("libraries") or [])

    def libraries(self):
        payload = self._get("/api/libraries").json()
        return payload.get("libraries") or []

    def book_library_ids(self, only=None):
        """Library ids worth syncing: book libraries, or just the one that was asked for."""
        libraries = self.libraries()
        if only:
            known = {library.get("id") for library in libraries}
            if only not in known:
                raise AudiobookshelfError("library {0} is not on this server".format(only))
            return [only]
        return [
            library["id"]
            for library in libraries
            if library.get("mediaType", "book") == "book" and library.get("id")
        ]

    def books(self, library_id):
        """Every ebook in one library, newest change last."""
        payload = self._get(
            "/api/libraries/{0}/items".format(library_id),
            params={"limit": 0, "minified": 1},
        ).json()
        found = []
        for item in payload.get("results") or []:
            media = item.get("media") or {}
            fmt = str(media.get("ebookFormat") or "").lower().lstrip(".")
            if fmt.split(".")[0] not in EBOOK_FORMATS:
                continue
            book = Book.from_item(item)
            if book.item_id:
                found.append(book)
        found.sort(key=lambda book: (book.modified, book.item_id))
        return found

    def all_books(self, only=None):
        found = []
        for library_id in self.book_library_ids(only):
            found.extend(self.books(library_id))
        found.sort(key=lambda book: (book.modified, book.item_id))
        return found

    def ebook_stream(self, item_id):
        """The ebook file itself, streamed so a large book never lands in memory."""
        return self._get("/api/items/{0}/ebook".format(item_id), stream=True)

    def cover_stream(self, item_id, width=None, height=None):
        params = {}
        if width:
            params["width"] = width
        if height:
            params["height"] = height
        return self._get("/api/items/{0}/cover".format(item_id), params=params, stream=True)
