import pathlib
import re
from datetime import datetime, timezone

import pytest

from kobobridge import abs as abs_module
from kobobridge.abs import Audiobookshelf, AudiobookshelfError, Book, item_uuid

from conftest import FakeResponse, item


def test_the_client_only_ever_reads():
    """The promise the README makes, checked against the source rather than trusted."""
    source = pathlib.Path(abs_module.__file__).read_text(encoding="utf-8")
    for verb in ("post", "put", "patch", "delete"):
        assert not re.search(r"\.{0}\s*\(".format(verb), source), (
            "abs.py must contain no {0} call".format(verb.upper())
        )
    assert source.count("self.session.get(") == 1, "one outbound door, not several"


def test_identifiers_are_stable_and_distinct():
    assert item_uuid("li_one") == item_uuid("li_one")
    assert item_uuid("li_one") != item_uuid("li_two")
    # The device rejects anything that is not shaped like a UUID.
    assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", item_uuid("li_one"))


def test_a_library_item_becomes_a_book():
    book = Book.from_item(
        item(
            "li_two",
            "Second Book",
            added=1_600_000_100_000,
            updated=1_600_000_200_000,
            series="The Expanse #3",
            fmt="kepub.epub",
            author="James Corey",
            language="en-GB",
        )
    )
    assert book.title == "Second Book"
    assert book.authors == ["James Corey"]
    assert book.series == "The Expanse"
    assert book.series_index == 3.0
    assert book.language == "en"
    assert book.is_kepub
    assert book.created == datetime(2020, 9, 13, 12, 28, 20, tzinfo=timezone.utc)
    assert book.modified > book.created


def test_a_series_without_a_number_keeps_its_name():
    book = Book.from_item(item("li_x", "Loose", series="Standalones"))
    assert book.series == "Standalones"
    assert book.series_index is None


def test_an_odd_language_falls_back_to_english():
    for value in (None, "", "English", "x", "fr_CA"):
        book = Book.from_item(item("li_x", "T", language=value))
        assert book.language in ("en", "fr")


def test_modified_never_trails_created():
    # A library item edited before it was added would walk the sync cursor backwards.
    book = Book.from_item(item("li_x", "T", added=2_000, updated=1_000))
    assert book.modified == book.created


def test_only_readable_formats_are_offered(monkeypatch):
    client = Audiobookshelf("http://abs.local", "token")
    payload = {
        "results": [
            item("li_one", "Epub", fmt="epub"),
            item("li_two", "Kepub", fmt="kepub.epub"),
            item("li_three", "Audiobook", fmt=""),
            item("li_four", "A pdf", fmt="pdf"),
        ]
    }

    class Response:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.setattr(client, "_get", lambda *a, **k: Response())
    titles = [book.title for book in client.books("lib-1")]
    assert titles == ["Epub", "Kepub"]


def test_an_unknown_library_is_refused(monkeypatch):
    client = Audiobookshelf("http://abs.local", "token")
    monkeypatch.setattr(client, "libraries", lambda: [{"id": "lib-1", "mediaType": "book"}])
    assert client.book_library_ids(None) == ["lib-1"]
    with pytest.raises(AudiobookshelfError):
        client.book_library_ids("lib-missing")


def test_podcast_libraries_are_skipped(monkeypatch):
    client = Audiobookshelf("http://abs.local", "token")
    monkeypatch.setattr(
        client,
        "libraries",
        lambda: [
            {"id": "lib-1", "mediaType": "book"},
            {"id": "lib-2", "mediaType": "podcast"},
        ],
    )
    assert client.book_library_ids(None) == ["lib-1"]


def test_the_real_ebook_size_replaces_the_item_size(monkeypatch, tmp_path):
    """A listing reports the whole item; on a combined library that is the audiobook."""
    from kobobridge.state import EbookSizeStore

    sizes = EbookSizeStore(str(tmp_path / "sizes.json"))
    client = Audiobookshelf("http://abs.local", "token", sizes=sizes)
    payload = {"results": [item("li_one", "Combined", fmt="epub", size=419_340_239)]}
    asked = []

    def fake_get(path, **kwargs):
        if path.endswith("/ebook"):
            asked.append((path, kwargs.get("headers")))
            return FakeResponse(headers={"Content-Range": "bytes 0-0/716042"})
        return FakeResponse(payload=payload)

    monkeypatch.setattr(client, "_get", fake_get)
    books = client.books("lib-1")
    assert books[0].size == 716042, "the EPUB size, not the item size"
    assert asked == [("/api/items/li_one/ebook", {"Range": "bytes=0-0"})]


def test_a_known_size_is_not_fetched_twice(monkeypatch, tmp_path):
    from kobobridge.state import EbookSizeStore

    sizes = EbookSizeStore(str(tmp_path / "sizes.json"))
    client = Audiobookshelf("http://abs.local", "token", sizes=sizes)
    payload = {"results": [item("li_one", "Cached", fmt="epub", updated=1_600_000_000_000)]}
    calls = []

    def fake_get(path, **kwargs):
        if path.endswith("/ebook"):
            calls.append(path)
            return FakeResponse(headers={"Content-Range": "bytes 0-0/4242"})
        return FakeResponse(payload=payload)

    monkeypatch.setattr(client, "_get", fake_get)
    assert client.books("lib-1")[0].size == 4242
    assert client.books("lib-1")[0].size == 4242
    assert len(calls) == 1, "the second sync reads the cache"

    # A changed item invalidates its entry.
    payload["results"] = [item("li_one", "Cached", fmt="epub", updated=1_700_000_000_000)]
    client.books("lib-1")
    assert len(calls) == 2


def test_a_failed_size_lookup_keeps_the_listing_figure(monkeypatch, tmp_path):
    """Better an approximate size than a zero, which the device would read as empty."""
    from kobobridge.state import EbookSizeStore

    sizes = EbookSizeStore(str(tmp_path / "sizes.json"))
    client = Audiobookshelf("http://abs.local", "token", sizes=sizes)
    payload = {"results": [item("li_one", "Unreachable", fmt="epub", size=555)]}

    def fake_get(path, **kwargs):
        if path.endswith("/ebook"):
            raise AudiobookshelfError("upstream is down")
        return FakeResponse(payload=payload)

    monkeypatch.setattr(client, "_get", fake_get)
    assert client.books("lib-1")[0].size == 555


def test_a_server_without_ranges_falls_back_to_content_length(monkeypatch, tmp_path):
    from kobobridge.state import EbookSizeStore

    sizes = EbookSizeStore(str(tmp_path / "sizes.json"))
    client = Audiobookshelf("http://abs.local", "token", sizes=sizes)
    monkeypatch.setattr(
        client, "_get", lambda path, **kw: FakeResponse(headers={"Content-Length": "9001"})
    )
    assert client.ebook_size("li_one") == 9001
