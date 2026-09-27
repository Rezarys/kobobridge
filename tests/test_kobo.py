from datetime import datetime, timedelta, timezone

from kobobridge.abs import AudiobookshelfError, Book, item_uuid
from kobobridge.kobo import SYNC_ITEM_LIMIT, cover_width, select_for_sync
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


def test_a_large_library_is_listed_once_for_the_whole_sync(config, kobo_url):
    """What the reader's own report of a very slow first sync came down to.

    A library too large for one answer is sent over several rounds. Every round used to read
    the whole library again, so two thousand books cost twenty full listings instead of one,
    and the listing is the slow step: it holds a lock, and on a library that size it also
    resolves the true size of every new book one request at a time.
    """
    from kobobridge.app import create_app
    from kobobridge.state import ReadingStateStore
    from conftest import FakeAudiobookshelf

    count = SYNC_ITEM_LIMIT * 3
    upstream = FakeAudiobookshelf(
        items=[
            item(
                "li_{0:04d}".format(index),
                "Book {0}".format(index),
                added=1_600_000_000_000 + index * 1000,
                updated=1_600_000_000_000 + index * 1000,
            )
            for index in range(count)
        ]
    )
    client = create_app(
        config, client=upstream, reading_states=ReadingStateStore(path=None)
    ).test_client()

    sent, rounds, token = 0, 0, None
    while True:
        headers = {HEADER: token} if token else {}
        response = client.get(kobo_url("/v1/library/sync"), headers=headers)
        sent += len(loads(response))
        rounds += 1
        token = response.headers[HEADER]
        if response.headers.get("x-kobo-sync") != "continue":
            break
        assert rounds < 10

    assert sent == count
    assert rounds == 3
    assert len([call for call in upstream.calls if call[0] == "all_books"]) == 1


def test_a_later_sync_still_sees_a_book_added_since(client, kobo_url, upstream):
    """The guard on the rule above: only a sync the bridge said was unfinished reuses a list.

    A reader coming back the next day also sends a cursor, and it must get a fresh listing.
    """
    token = client.get(kobo_url("/v1/library/sync")).headers[HEADER]
    listings = len([call for call in upstream.calls if call[0] == "all_books"])
    upstream.items.append(
        item("li_later", "A Later Book", added=1_700_000_000_000, updated=1_700_000_000_000)
    )
    results = loads(client.get(kobo_url("/v1/library/sync"), headers={HEADER: token}))
    assert [entry["NewEntitlement"]["BookMetadata"]["Title"] for entry in results] == [
        "A Later Book"
    ]
    assert len([call for call in upstream.calls if call[0] == "all_books"]) == listings + 1


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


def test_a_book_this_bridge_does_not_serve_is_answered_empty(client, kobo_url):
    """A reader holds books from wherever it was synced before, and asks about those too.

    Answering 404 told the reader its request was wrong, so it asked again on the next pass,
    and on every pass after that. The long standing open implementation of this protocol
    answers an unknown book with an empty body and a success status whenever it is not
    proxying to the manufacturer's store, which is this bridge's permanent position.
    """
    for path in ("metadata", "state"):
        response = client.get(
            kobo_url("/v1/library/00000000-0000-0000-0000-000000000009/" + path)
        )
        assert response.status_code == 200, path
        assert loads(response) == {}, path


def test_a_numeric_identifier_is_answered_without_relisting_the_library(
    client, kobo_url, upstream
):
    """Reported by zeeohee0 in issue 3: the same thirty five identifiers, every sync pass.

    A reader synced to another server first carries that server's identifiers, which are
    plain numbers there. No identifier this bridge mints is a number, so no listing can ever
    contain one, and asking the library again in the hope that it does is work that cannot
    succeed. The first such request paid a full listing of a library of two thousand books.
    """
    upstream.calls.clear()
    response = client.put(
        kobo_url("/v1/library/4613/state"),
        json={"ReadingStates": [{"StatusInfo": {"Status": "Finished"}}]},
    )
    assert response.status_code == 200
    assert loads(response) == {}
    assert upstream.calls == [], "an identifier that cannot be ours must cost no lookup"


def test_a_download_streams_the_file_through(client, kobo_url, upstream):
    response = client.get(kobo_url("/download/li_one"))
    assert response.status_code == 200
    assert response.data == b"PK\x03\x04 pretend epub"
    assert response.headers["Content-Type"] == "application/epub+zip"
    assert ("ebook_stream", "li_one") in upstream.calls


def test_a_cover_is_served(client, kobo_url, upstream):
    uuid_one = item_uuid("li_one")
    response = client.get(kobo_url("/{0}/300/400/false/image.jpg".format(uuid_one)))
    assert response.status_code == 200
    assert response.data == b"jpegbytes"
    assert ("cover_stream", "li_one", 360) in upstream.calls


def test_a_cover_with_a_quality_segment_works_too(client, kobo_url):
    uuid_one = item_uuid("li_one")
    response = client.get(kobo_url("/{0}/300/400/85/false/image.jpg".format(uuid_one)))
    assert response.status_code == 200


def test_every_size_the_reader_asks_for_falls_on_one_of_three_widths(client, kobo_url, upstream):
    """The reason is on the server's side, and it is the cost of a cover that misses its cache.

    Audiobookshelf renders a cover by spawning ffmpeg and caches the result under the exact
    dimensions asked for. Forwarding the reader's own pixel sizes meant a fresh process for
    every size it ever asked for, and on a large library that is thousands of them.
    """
    uuid_one = item_uuid("li_one")
    for width, height in ((149, 223), (355, 530), (500, 750), (1072, 1448)):
        client.get(kobo_url("/{0}/{1}/{2}/false/image.jpg".format(uuid_one, width, height)))
    widths = {call[2] for call in upstream.calls if call[0] == "cover_stream"}
    assert widths <= {360, 720, 1080}


def test_the_width_asked_for_is_rounded_up_never_down_and_never_by_the_height():
    """The rounding is on the width, because the width is what is sent.

    355 by 530 is the shape a library grid asks for. Rounding on the height would put it in a
    band chosen by 530 and serve a 720 wide image into a 355 wide slot, four times the pixels,
    on exactly the path this rounding exists to unclog.
    """
    assert cover_width(355) == 360
    assert cover_width(360) == 360
    assert cover_width(361) == 720
    assert cover_width(1072) == 1080
    # Nothing above the largest band, whatever the reader asks for.
    assert cover_width(4000) == 1080
    # A reader that sends something that is not a number still gets a cover.
    assert cover_width("wide") == 360


def test_no_height_is_ever_sent_to_the_library(client, kobo_url, upstream):
    """Sending a height stretches the cover to the reader's frame and doubles the cache key."""
    uuid_one = item_uuid("li_one")
    client.get(kobo_url("/{0}/355/530/false/image.jpg".format(uuid_one)))
    covers = [call for call in upstream.calls if call[0] == "cover_stream"]
    assert covers and all(len(call) == 3 for call in covers)


def test_a_book_with_no_cover_art_answers_404_not_502(client, kobo_url, upstream):
    """A 404 from the library is a fact about the book, not a fault of the bridge.

    A reader told 404 shows its own placeholder for that one book. A reader told 502 has been
    handed a server fault, and may give up on the covers that follow.
    """
    upstream.cover_fails_with = AudiobookshelfError("abs answered 404", status=404)
    response = client.get(kobo_url("/{0}/300/400/false/image.jpg".format(item_uuid("li_one"))))
    assert response.status_code == 404


def test_an_unreachable_library_still_answers_502_for_a_cover(client, kobo_url, upstream):
    upstream.cover_fails_with = AudiobookshelfError("connection refused")
    response = client.get(kobo_url("/{0}/300/400/false/image.jpg".format(item_uuid("li_one"))))
    assert response.status_code == 502


def test_an_unreachable_library_answers_502_rather_than_crashing_on_a_lookup(
    client, kobo_url, upstream
):
    upstream.fail_with = AudiobookshelfError("connection refused")
    for path in (
        "/{0}/300/400/false/image.jpg".format(item_uuid("li_one")),
        "/v1/library/{0}/metadata".format(item_uuid("li_one")),
        "/v1/library/{0}/state".format(item_uuid("li_one")),
    ):
        assert client.get(kobo_url(path)).status_code == 502


def test_an_unknown_book_does_not_relist_the_library_once_per_request(
    client, kobo_url, upstream
):
    """The shape this took on a large library, and why it mattered for covers.

    A cover asked for a book that is not in the listing used to force a full listing, under
    the lock. A reader asking for a burst of such covers queued one full listing per request,
    each behind the last, and nothing else was served meanwhile.
    """
    stranger = item_uuid("li_does_not_exist")
    client.get(kobo_url("/v1/library/sync"))
    before = len([call for call in upstream.calls if call[0] == "all_books"])
    for _ in range(25):
        assert client.get(
            kobo_url("/{0}/300/400/false/image.jpg".format(stranger))
        ).status_code == 404
    after = len([call for call in upstream.calls if call[0] == "all_books"])
    assert after == before


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


def test_a_store_address_answers_for_its_children_too(client, kobo_url, upstream):
    """A reader asks for a child of an address, not only the address itself.

    A Libra Colour on 4.46 asks for `/v1/categories/<id>` nine times while it draws its
    home screen. `/v1/categories` alone left every one of those a 404.
    """
    paths = (
        "/v1/categories/00000000-0000-0000-0000-000000000001",
        "/v1/categories/00000000-0000-0000-0000-000000000001/featured",
        "/v1/products/1b5ea2b4-19b5-45ea-a979-8c2624111ed8/recommendations",
        "/v1/user/browsehistory",
    )
    for path in paths:
        assert client.get(kobo_url(path)).status_code == 200, path
    assert upstream.calls == [], "no store endpoint may touch the library"


def test_store_endpoints_answer_without_reaching_anyone(client, kobo_url, upstream):
    for path in ("/v1/products/dailydeal", "/v1/user/profile", "/v1/analytics/gettests"):
        assert client.get(kobo_url(path)).status_code == 200
    assert client.get(kobo_url("/v1/library/tags")).status_code == 200
    assert upstream.calls == [], "no store endpoint may touch the library"
