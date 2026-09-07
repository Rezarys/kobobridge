from datetime import datetime, timezone

from kobobridge.synctoken import EPOCH, HEADER, SyncToken


def test_absent_header_starts_from_the_epoch():
    token = SyncToken.from_headers({})
    assert token.books_last_created == EPOCH
    assert token.books_last_modified == EPOCH


def test_round_trip_preserves_the_cursor():
    moment = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    written = SyncToken(books_last_created=moment, books_last_modified=moment)
    read = SyncToken.from_headers({HEADER: written.to_header_value()})
    assert read.books_last_created == moment
    assert read.books_last_modified == moment


def test_a_token_from_the_manufacturer_store_is_ignored():
    # Those tokens carry a dot and mean nothing here, so the sync starts over.
    token = SyncToken.from_headers({HEADER: "abc.def"})
    assert token.books_last_modified == EPOCH


def test_unreadable_tokens_do_not_raise():
    for bad in ("not base64 at all !!", "eyJ9", "", "e30="):
        assert SyncToken.from_headers({HEADER: bad}).books_last_modified == EPOCH


def test_header_value_survives_missing_padding():
    written = SyncToken(books_last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc))
    stripped = written.to_header_value().rstrip("=")
    read = SyncToken.from_headers({HEADER: stripped})
    assert read.books_last_modified == datetime(2026, 1, 1, tzinfo=timezone.utc)
