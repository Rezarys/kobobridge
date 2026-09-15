"""The resource table is a promise, and these tests hold it to it.

Issue 3 reported a book that the device announced as downloaded and never produced. Nine of
the forty nine addresses the bridge hands the device answered 404, among them the two the
device asks for when it starts a download. An address that is advertised and then refuses is
worse than one that was never offered, because the device has no fallback to reach for.
"""

from conftest import TOKEN, loads
from kobobridge.resources import LIBRARY_SERVICES

# What the device puts in place of the templated parts of an address.
FILLERS = {
    "{RevisionIds}": "6f3d3205-22ea-5acd-8896-32801d17d3a6",
    "{Ids}": "6f3d3205-22ea-5acd-8896-32801d17d3a6",
    "{TagId}": "11111111-1111-1111-1111-111111111111",
    "{ProductId}": "6f3d3205-22ea-5acd-8896-32801d17d3a6",
    "{ProductIds}": "6f3d3205-22ea-5acd-8896-32801d17d3a6",
    "{LibraryItemId}": "6f3d3205-22ea-5acd-8896-32801d17d3a6",
    "{Id}": "6f3d3205-22ea-5acd-8896-32801d17d3a6",
    "{SeriesId}": "22222222-2222-2222-2222-222222222222",
    "{Rating}": "4",
}


def fill(path):
    for placeholder, value in FILLERS.items():
        path = path.replace(placeholder, value)
    return path


def test_every_advertised_address_is_served(client, kobo_url):
    """Not one address in the table may answer 404 to both a GET and a POST."""
    refused = []
    for name, path in sorted(LIBRARY_SERVICES.items()):
        url = kobo_url(fill(path))
        get = client.get(url).status_code
        post = client.post(url, json={}).status_code
        # A 405 means the address exists and wants the other verb, which is an answer.
        if get == 404 and post in (404, 405):
            refused.append("{0} ({1})".format(name, path))
    assert refused == []


def test_the_two_addresses_a_download_starts_with_answer(client, kobo_url):
    """``get_download_keys`` and ``get_download_link``, the pair named in issue 3."""
    for path in ("/v1/library/downloadkeys", "/v1/library/downloadlink"):
        response = client.get(kobo_url(path))
        assert response.status_code == 200, path
        assert loads(response) == {}


def test_the_entitlement_route_does_not_swallow_sync(client, kobo_url):
    """``/v1/library/<ids>`` sits one segment away from ``/v1/library/sync``.

    Adding it must not capture the sync address, which would stop the library dead.
    """
    response = client.get(kobo_url("/v1/library/sync"))
    assert response.status_code == 200
    assert isinstance(loads(response), list)
    assert response.headers.get("x-kobo-synctoken")


def test_the_entitlement_route_does_not_swallow_search_or_download_keys(client, kobo_url):
    """The same neighbourhood, for the three static addresses that share its shape."""
    assert loads(client.get(kobo_url("/v1/library/search"))) == []
    assert loads(client.get(kobo_url("/v1/library/downloadkeys"))) == {}
    assert loads(client.get(kobo_url("/v1/library/downloadlink"))) == {}


def test_an_unknown_device_token_is_still_refused_everywhere(client):
    """Answering more addresses must not open any of them to a wrong token."""
    for path in ("/v1/library/downloadkeys", "/v1/library/search", "/v1/categories"):
        response = client.get("/kobo/not-the-token{0}".format(path))
        assert response.status_code == 401, path
    assert TOKEN != "not-the-token"
