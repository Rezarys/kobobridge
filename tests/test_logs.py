"""Logging, the third thing issue 3 reported.

A bridge that answers thousands of calls in silence cannot be debugged by the person running
it, and it cannot be reported on either: the reporter of issue 3 wrote that he would have sent
a trace if there had been one to send.
"""

import io
import logging

import pytest

from kobobridge import logs


@pytest.fixture
def captured():
    """Point the bridge logger at a string, and put it back afterwards."""
    stream = io.StringIO()
    logs.configure(level=logging.DEBUG, stream=stream)
    yield stream
    logs.configure(level=logging.INFO, stream=None)


def test_a_request_is_logged_with_its_status_and_duration(client, kobo_url, captured):
    client.get(kobo_url("/v1/library/sync"))
    written = captured.getvalue()
    assert "GET" in written
    assert "/v1/library/sync" in written
    assert "-> 200" in written
    assert "ms" in written


def test_the_device_token_never_reaches_the_log(client, kobo_url, captured):
    """A log has to be safe to paste into a public issue."""
    from conftest import TOKEN

    client.get(kobo_url("/v1/library/sync"))
    written = captured.getvalue()
    assert TOKEN not in written
    assert "/kobo/<token>/v1/library/sync" in written


def test_a_failing_request_is_logged_too(client, kobo_url, upstream, captured):
    from kobobridge.abs import AudiobookshelfError

    upstream.fail_with = AudiobookshelfError("upstream is down")
    response = client.get(kobo_url("/v1/library/sync"))
    assert response.status_code == 502
    written = captured.getvalue()
    assert "sync failed: upstream is down" in written
    assert "-> 502" in written


def test_a_cover_that_cannot_be_fetched_says_which_book(client, kobo_url, upstream, captured):
    from kobobridge.abs import AudiobookshelfError, item_uuid

    book_uuid = item_uuid("li_one")
    client.get(kobo_url("/v1/library/sync"))
    upstream.fail_with = AudiobookshelfError("audiobookshelf answered 404")
    response = client.get(kobo_url("/{0}/355/530/85/false/image.jpg".format(book_uuid)))
    assert response.status_code == 502
    written = captured.getvalue()
    assert "cover of First Book (li_one) failed" in written


def test_a_cover_that_is_served_reports_its_size(client, kobo_url, captured):
    """The log promises a size, so a cover has to carry one rather than print a dash."""
    from kobobridge.abs import item_uuid

    client.get(kobo_url("/v1/library/sync"))
    url = kobo_url("/{0}/355/530/85/false/image.jpg".format(item_uuid("li_one")))
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["Content-Length"] == "9"
    assert "-> 200 9 bytes in" in captured.getvalue()


def test_a_download_reports_the_size_the_device_will_check(client, kobo_url, captured):
    client.get(kobo_url("/v1/library/sync"))
    response = client.get(kobo_url("/download/li_one"))
    assert response.status_code == 200
    written = captured.getvalue()
    assert "download of li_one: upstream says 20 bytes" in written


def test_the_level_comes_from_the_environment():
    assert logs.level_from_env({}) == logging.INFO
    assert logs.level_from_env({"KOBOBRIDGE_LOG_LEVEL": "debug"}) == logging.DEBUG
    assert logs.level_from_env({"KOBOBRIDGE_LOG_LEVEL": "WARNING"}) == logging.WARNING
    # An unreadable value must not silence the bridge, which is the failure being fixed.
    assert logs.level_from_env({"KOBOBRIDGE_LOG_LEVEL": "loud"}) == logging.INFO


def test_configure_twice_does_not_double_every_line(captured):
    logs.configure(level=logging.INFO, stream=captured)
    logs.configure(level=logging.INFO, stream=captured)
    logs.get_logger().info("once")
    assert captured.getvalue().count("once") == 1


def test_the_token_marker_leaves_other_paths_alone():
    assert logs._hide_token("/health") == "/health"
    assert logs._hide_token("/kobo/abc123/v1/library/sync") == "/kobo/<token>/v1/library/sync"
