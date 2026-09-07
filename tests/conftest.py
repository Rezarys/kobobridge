import io
import json

import pytest

from kobobridge.app import create_app
from kobobridge.config import Config
from kobobridge.state import ReadingStateStore

TOKEN = "test-device-token"


def item(item_id, title, added=1_600_000_000_000, updated=1_600_000_000_000, fmt="epub", **kw):
    """One Audiobookshelf library item, in the shape the minified listing returns."""
    metadata = {
        "title": title,
        "authorName": kw.get("author", "A Writer"),
        "description": kw.get("description"),
        "publisher": kw.get("publisher"),
        "publishedYear": kw.get("published_year"),
        "language": kw.get("language", "en"),
        "seriesName": kw.get("series"),
    }
    return {
        "id": item_id,
        "addedAt": added,
        "updatedAt": updated,
        "media": {
            "metadata": metadata,
            "ebookFormat": fmt,
            "size": kw.get("size", 1234),
        },
    }


class FakeResponse:
    def __init__(self, payload=None, body=b"", headers=None, status=200):
        self._payload = payload
        self.body = body
        self.headers = headers or {}
        self.status_code = status

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1):
        stream = io.BytesIO(self.body)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                return
            yield chunk


class FakeAudiobookshelf:
    """Stands in for a server. Records every call so tests can assert on them."""

    def __init__(self, items=None, libraries=None):
        self.items = list(items or [])
        self.libraries_payload = libraries or [{"id": "lib-1", "mediaType": "book"}]
        self.calls = []
        self.fail_with = None

    # The real client exposes exactly these, and all of them are reads.
    def ping(self):
        self.calls.append(("ping",))
        return len(self.libraries_payload)

    def libraries(self):
        return list(self.libraries_payload)

    def all_books(self, only=None):
        from kobobridge.abs import EBOOK_FORMATS, Book

        self.calls.append(("all_books", only))
        if self.fail_with:
            raise self.fail_with
        books = [
            Book.from_item(raw)
            for raw in self.items
            if str((raw.get("media") or {}).get("ebookFormat") or "")
            .lower()
            .lstrip(".")
            .split(".")[0]
            in EBOOK_FORMATS
        ]
        books.sort(key=lambda book: (book.modified, book.item_id))
        return books

    def ebook_stream(self, item_id):
        self.calls.append(("ebook_stream", item_id))
        if self.fail_with:
            raise self.fail_with
        return FakeResponse(
            body=b"PK\x03\x04 pretend epub",
            headers={"Content-Type": "application/epub+zip", "Content-Length": "20"},
        )

    def cover_stream(self, item_id, width=None, height=None):
        self.calls.append(("cover_stream", item_id, width, height))
        if self.fail_with:
            raise self.fail_with
        return FakeResponse(body=b"jpegbytes", headers={"Content-Type": "image/jpeg"})


@pytest.fixture
def config():
    return Config(
        abs_url="http://abs.local:13378",
        abs_token="abs-token",
        device_token=TOKEN,
        public_url="http://bridge.local:8484",
    )


@pytest.fixture
def upstream():
    return FakeAudiobookshelf(
        items=[
            item("li_one", "First Book", added=1_600_000_000_000, updated=1_600_000_000_000),
            item(
                "li_two",
                "Second Book",
                added=1_600_000_100_000,
                updated=1_600_000_200_000,
                series="The Expanse #3",
                fmt="kepub.epub",
            ),
            item("li_audio", "An Audiobook", fmt=""),
        ]
    )


@pytest.fixture
def app(config, upstream):
    return create_app(config, client=upstream, reading_states=ReadingStateStore(path=None))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def kobo_url():
    def build(path):
        return "/kobo/{0}{1}".format(TOKEN, path)

    return build


def loads(response):
    return json.loads(response.data)
