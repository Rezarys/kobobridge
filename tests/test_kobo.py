from datetime import datetime, timedelta, timezone

from kobobridge.abs import AudiobookshelfError, Book, item_uuid
from kobobridge.kobo import SYNC_ITEM_LIMIT, select_for_sync
from kobobridge.synctoken import EPOCH, HEADER, SyncToken

from conftest import item, loads


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


def test_a_wrong_token_is_refused(client):
    assert client.get("/kobo/not-the-token/v1/library/sync").status_code == 401


def test_health_reports_the_upstream(client):
    body = loads(client.get("/health"))
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_initialization_points_the_device_back_at_the_bridge(client, kobo_url):
    resources = loads(client.get(kobo_url("/v1/initialization")))["Resources"]
    prefix = "http://bridge.local:8484/kobo/test-device-token"
    assert resources["library_sync"] == prefix + "/v1/library/sync"
    assert resources["library_metadata"] == prefix + "/v1/library/{Ids}/metadata"
    assert resources["reading_state"] == prefix + "/v1/library/{Ids}/state"
    assert resources["device_auth"] == prefix + "/v1/auth/device"
    assert resources["image_url_template"].startswith(prefix)
    assert resources["image_host"] == "http://bridge.local:8484"


def test_no_library_address_is_left_pointing_at_the_store(client, kobo_url):
    """The promise the README makes about the manufacturer's servers, checked."""
    from kobobridge.resources import DEVICE_SERVICES

    resources = loads(client.get(kobo_url("/v1/initialization")))["Resources"]
    elsewhere = {
        name: value
        for name, value in resources.items()
        if isinstance(value, str)
        and value.startswith("http")
        and not value.startswith("http://bridge.local:8484")
    }
    # Only the four device functions the bridge does not serve, and none of them is a
    # library address.
    assert set(elsewhere) == set(DEVICE_SERVICES)
    assert not any("library" in name or "product" in name for name in elsewhere)


def test_store_features_are_announced_as_unavailable(client, kobo_url):
    resources = loads(client.get(kobo_url("/v1/initialization")))["Resources"]
    for flag in ("kobo_subscriptions_enabled", "kobo_wishlist_enabled", "use_one_store"):
        assert resources[flag] == "False"


def test_auth_hands_back_a_token(client, kobo_url):
    body = loads(client.post(kobo_url("/v1/auth/device"), json={"UserKey": "abc"}))
    assert body["TokenType"] == "Bearer"
    assert body["UserKey"] == "abc"
    assert body["AccessToken"]


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_a_first_sync_sends_every_ebook_as_new(client, kobo_url):
    response = client.get(kobo_url("/v1/library/sync"))
    results = loads(response)
    assert len(results) == 2, "the audiobook is not offered to an eReader"
    assert all("NewEntitlement" in entry for entry in results)
    assert HEADER in response.headers
    titles = {entry["NewEntitlement"]["BookMetadata"]["Title"] for entry in results}
    assert titles == {"First Book", "Second Book"}


def test_a_second_sync_with_the_returned_token_sends_nothing(client, kobo_url):
    first = client.get(kobo_url("/v1/library/sync"))
    token = first.headers[HEADER]
    second = client.get(kobo_url("/v1/library/sync"), headers={HEADER: token})
    assert loads(second) == []


def test_a_book_edited_after_the_last_sync_comes_back_as_changed(client, kobo_url, upstream):
    token = client.get(kobo_url("/v1/library/sync")).headers[HEADER]
    upstream.items[0]["updatedAt"] = 1_700_000_000_000
    results = loads(client.get(kobo_url("/v1/library/sync"), headers={HEADER: token}))
    assert len(results) == 1
    assert "ChangedEntitlement" in results[0]
    assert results[0]["ChangedEntitlement"]["BookMetadata"]["Title"] == "First Book"


def test_a_book_added_after_the_last_sync_comes_back_as_new(client, kobo_url, upstream):
    token = client.get(kobo_url("/v1/library/sync")).headers[HEADER]
    upstream.items.append(
        item("li_three", "Third Book", added=1_700_000_000_000, updated=1_700_000_000_000)
    )
    results = loads(client.get(kobo_url("/v1/library/sync"), headers={HEADER: token}))
    assert len(results) == 1
    assert results[0]["NewEntitlement"]["BookMetadata"]["Title"] == "Third Book"


def test_an_unreachable_library_says_so_instead_of_crashing(client, kobo_url, upstream):
    upstream.fail_with = AudiobookshelfError("connection refused")
    response = client.get(kobo_url("/v1/library/sync"))
    assert response.status_code == 502
    assert "connection refused" in loads(response)["error"]


# ---------------------------------------------------------------------------
# Batching, tested on the selection function directly
# ---------------------------------------------------------------------------


def _book(index, modified, created=None):
    moment = EPOCH + timedelta(seconds=modified)
    return Book(
        item_id="li_{0}".format(index),
        title="Book {0}".format(index),
        created=EPOCH + timedelta(seconds=created if created is not None else modified),
        modified=moment,
    )


def test_a_large_library_is_cut_into_batches_and_announces_more():
    books = [_book(i, 1000 + i) for i in range(SYNC_ITEM_LIMIT + 25)]
    batch, token, more = select_for_sync(books, SyncToken())
    assert len(batch) == SYNC_ITEM_LIMIT
    assert more is True
    # The cursor stops where the batch stops, so nothing is skipped.
    assert token.books_last_modified == batch[-1].modified
    rest, _, still_more = select_for_sync(books, token)
    assert len(rest) == 25
    assert still_more is False


def test_the_cursor_never_splits_a_group_sharing_one_timestamp():
    # Ninety nine books at one second, then thirty at the next: parking the cursor on the
    # second timestamp would drop the tail of that group forever.
    books = [_book(i, 1000) for i in range(99)] + [_book(100 + i, 2000) for i in range(30)]
    batch, token, more = select_for_sync(books, SyncToken())
    assert more is True
    assert {book.modified for book in batch} == {EPOCH + timedelta(seconds=1000)}
    assert len(batch) == 99
    rest, _, still_more = select_for_sync(books, token)
    assert len(rest) == 30
    assert still_more is False


def test_a_group_larger_than_one_batch_is_still_sent():
    books = [_book(i, 1000) for i in range(SYNC_ITEM_LIMIT + 5)]
    batch, _, more = select_for_sync(books, SyncToken())
    assert more is True
    assert len(batch) == SYNC_ITEM_LIMIT


def test_the_created_cursor_only_covers_what_was_actually_sent():
    books = [_book(i, 1000 + i) for i in range(SYNC_ITEM_LIMIT + 5)]
    batch, partial, more = select_for_sync(books, SyncToken())
    assert more is True
    # Books still waiting must stay ahead of the cursor, or they would arrive announced as
    # merely changed on a device that has never seen them.
    assert partial.books_last_created == max(book.created for book in batch)
    assert partial.books_last_created < max(book.created for book in books)
    rest, complete, still_more = select_for_sync(books, partial)
    assert still_more is False
    assert all(book.created > partial.books_last_created for book in rest)
    assert complete.books_last_created == max(book.created for book in books)


def test_paging_terminates_and_sends_each_book_once():
    books = [_book(i, 1000 + i) for i in range(SYNC_ITEM_LIMIT * 2 + 7)]
    token, seen, rounds = SyncToken(), [], 0
    while True:
        batch, token, more = select_for_sync(books, token)
        seen.extend(book.item_id for book in batch)
        rounds += 1
        assert rounds < 10, "paging must not loop"
        if not more:
            break
    assert len(seen) == len(set(seen)) == len(books)
    # And a device that is fully caught up gets nothing at all.
    assert select_for_sync(books, token) == ([], token, False) or (
        select_for_sync(books, token)[0] == []
    )


# ---------------------------------------------------------------------------
# Metadata, covers, downloads
# ---------------------------------------------------------------------------


def test_metadata_offers_a_download_url_on_this_bridge(client, kobo_url):
    body = loads(client.get(kobo_url("/v1/library/{0}/metadata".format(item_uuid("li_one")))))
    assert len(body) == 1
    urls = body[0]["DownloadUrls"]
    assert [entry["Format"] for entry in urls] == ["EPUB3", "EPUB"]
    assert urls[0]["Url"] == (
        "http://bridge.local:8484/kobo/test-device-token/download/li_one"
    )


def test_a_kepub_is_announced_as_a_kepub(client, kobo_url):
    body = loads(client.get(kobo_url("/v1/library/{0}/metadata".format(item_uuid("li_two")))))
    assert [entry["Format"] for entry in body[0]["DownloadUrls"]] == ["KEPUB"]
    assert body[0]["Series"]["Name"] == "The Expanse"
    assert body[0]["Series"]["NumberFloat"] == 3.0


def test_metadata_for_an_unknown_book_is_a_clean_404(client, kobo_url):
    response = client.get(kobo_url("/v1/library/00000000-0000-0000-0000-000000000009/metadata"))
    assert response.status_code == 404


def test_a_download_streams_the_file_through(client, kobo_url, upstream):
    response = client.get(kobo_url("/download/li_one"))
    assert response.status_code == 200
    assert response.data == b"PK\x03\x04 pretend epub"
    assert response.headers["Content-Type"] == "application/epub+zip"
    assert ("ebook_stream", "li_one") in upstream.calls


def test_a_cover_is_fetched_at_the_size_the_device_asked_for(client, kobo_url, upstream):
    uuid_one = item_uuid("li_one")
    response = client.get(kobo_url("/{0}/300/400/false/image.jpg".format(uuid_one)))
    assert response.status_code == 200
    assert response.data == b"jpegbytes"
    assert ("cover_stream", "li_one", "300", "400") in upstream.calls


def test_a_cover_with_a_quality_segment_works_too(client, kobo_url):
    uuid_one = item_uuid("li_one")
    response = client.get(kobo_url("/{0}/300/400/85/false/image.jpg".format(uuid_one)))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------


def test_an_unread_book_starts_ready_to_read(client, kobo_url):
    body = loads(client.get(kobo_url("/v1/library/{0}/state".format(item_uuid("li_one")))))
    assert body[0]["StatusInfo"]["Status"] == "ReadyToRead"


def test_progress_pushed_by_the_device_comes_back_on_the_next_read(client, kobo_url):
    uuid_one = item_uuid("li_one")
    put = client.put(
        kobo_url("/v1/library/{0}/state".format(uuid_one)),
        json={
            "ReadingStates": [
                {
                    "CurrentBookmark": {"ProgressPercent": 42},
                    "StatusInfo": {"Status": "Reading", "TimesStartedReading": 1},
                }
            ]
        },
    )
    assert loads(put)["RequestResult"] == "Success"
    body = loads(client.get(kobo_url("/v1/library/{0}/state".format(uuid_one))))
    assert body[0]["CurrentBookmark"]["ProgressPercent"] == 42
    assert body[0]["StatusInfo"]["Status"] == "Reading"


def test_progress_is_never_pushed_into_the_library(client, kobo_url, upstream):
    """The library is read only. Nothing the device sends may travel upstream."""
    client.put(
        kobo_url("/v1/library/{0}/state".format(item_uuid("li_one"))),
        json={"ReadingStates": [{"CurrentBookmark": {"ProgressPercent": 42}}]},
    )
    assert all(call[0] in ("all_books", "ping") for call in upstream.calls)


# ---------------------------------------------------------------------------
# Endpoints that exist only to keep the device quiet
# ---------------------------------------------------------------------------


def test_store_endpoints_answer_without_reaching_anyone(client, kobo_url, upstream):
    for path in ("/v1/products/dailydeal", "/v1/user/profile", "/v1/analytics/gettests"):
        assert client.get(kobo_url(path)).status_code == 200
    assert client.get(kobo_url("/v1/library/tags")).status_code == 200
    assert upstream.calls == [], "no store endpoint may touch the library"
