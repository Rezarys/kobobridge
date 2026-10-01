import pytest
import requests

from conftest import FakeResponse, item
from kobobridge import license
from kobobridge.abs import Audiobookshelf
from kobobridge.app import Bridge
from kobobridge.config import Config
from kobobridge.license import LicenseError, unlock
from kobobridge.kobo import select_for_sync
from kobobridge.state import CollectionSeenStore, ReadingStateStore
from kobobridge.synctoken import SyncToken

STORE = 101
PRODUCT = 202


def answer(kind, ok=True, store=STORE, product=PRODUCT, status="active", error=None):
    """An answer in the shape of the provider's official client types."""
    payload = {
        kind: ok,
        "error": error,
        "license_key": {"id": 1, "status": status, "key": "KEY", "activation_limit": 3,
                        "activation_usage": 1, "expires_at": None, "test_mode": False},
        "instance": {"id": "inst-1", "name": "kobobridge"} if kind == "activated" else None,
        "meta": {"store_id": store, "product_id": product, "customer_name": "Ada Reader",
                 "customer_email": "ada@example.org"},
    }
    return FakeResponse(payload=payload, status=200 if ok else 400)


class FakePost:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload))
        found = self.answers.pop(0)
        if isinstance(found, Exception):
            raise found
        return found


def run(post, path, key="KEY"):
    return unlock(key, cache_path=str(path), post=post, store_id=STORE, product_id=PRODUCT)


def test_a_good_key_is_validated_then_activated_once(tmp_path):
    post = FakePost(answer("valid"), answer("activated"))
    found = run(post, tmp_path / "license.json")
    assert found.customer == "Ada Reader"
    assert [url for url, _ in post.calls] == [license.VALIDATE_URL, license.ACTIVATE_URL]
    assert post.calls[1][1] == {"license_key": "KEY", "instance_name": "kobobridge"}


def test_a_second_start_reads_the_file_and_makes_no_call(tmp_path):
    path = tmp_path / "license.json"
    run(FakePost(answer("valid"), answer("activated")), path)
    offline = FakePost()
    assert run(offline, path).instance_id == "inst-1"
    assert offline.calls == []


def test_the_key_itself_is_never_written_to_disk(tmp_path):
    path = tmp_path / "license.json"
    run(FakePost(answer("valid"), answer("activated")), path, key="SECRET-KEY-123")
    assert "SECRET-KEY-123" not in path.read_text(encoding="utf-8")


def test_a_cached_activation_does_not_serve_another_key(tmp_path):
    path = tmp_path / "license.json"
    run(FakePost(answer("valid"), answer("activated")), path, key="FIRST")
    post = FakePost(answer("valid", ok=False, error="license_key not found."))
    with pytest.raises(LicenseError):
        run(post, path, key="SECOND")
    assert len(post.calls) == 1


def test_a_key_from_another_store_is_refused_before_it_is_activated(tmp_path):
    post = FakePost(answer("valid", store=999))
    with pytest.raises(LicenseError) as caught:
        run(post, tmp_path / "license.json")
    assert "another product" in str(caught.value)
    assert [url for url, _ in post.calls] == [license.VALIDATE_URL]


def test_a_refused_key_says_what_the_server_said(tmp_path):
    post = FakePost(answer("valid"), answer("activated", ok=False,
                                             error="This license key has reached the activation limit."))
    with pytest.raises(LicenseError) as caught:
        run(post, tmp_path / "license.json")
    assert "activation limit" in str(caught.value)
    assert not (tmp_path / "license.json").exists()


def test_a_disabled_key_is_refused(tmp_path):
    post = FakePost(answer("valid"), answer("activated", status="disabled"))
    with pytest.raises(LicenseError):
        run(post, tmp_path / "license.json")


def test_no_network_means_no_unlock_and_no_file(tmp_path):
    post = FakePost(requests.ConnectionError("offline"))
    with pytest.raises(LicenseError) as caught:
        run(post, tmp_path / "license.json")
    assert "cannot reach" in str(caught.value)
    assert not (tmp_path / "license.json").exists()


def test_a_redirect_is_refused(tmp_path):
    post = FakePost(FakeResponse(payload={}, status=302))
    with pytest.raises(LicenseError):
        run(post, tmp_path / "license.json")


def test_while_the_product_is_not_on_sale_no_key_unlocks_and_nothing_is_sent(tmp_path):
    post = FakePost()
    with pytest.raises(LicenseError) as caught:
        unlock("KEY", cache_path=str(tmp_path / "license.json"), post=post)
    assert "not on sale" in str(caught.value)
    assert post.calls == []


def test_the_real_post_does_not_follow_redirects(monkeypatch):
    seen = {}

    def fake(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(payload={})

    monkeypatch.setattr(requests, "post", fake)
    license._post(license.ACTIVATE_URL, {"license_key": "K"}, 5)
    assert seen["allow_redirects"] is False
    assert seen["json"] == {"license_key": "K"}


def collection_config(**kw):
    return Config(abs_url="http://abs.local", abs_token="t", device_token="d",
                  collection_id="col-1", license_key="KEY", **kw)


def granted(key):
    return license.License(customer="Ada Reader", instance_id="inst-1")


def refused(key):
    raise LicenseError("collection licenses are not on sale yet in this version")


def collection_bridge(upstream, unlock, seen=None):
    return Bridge(collection_config(), client=upstream, reading_states=ReadingStateStore(path=None),
                  unlock=unlock, collection_seen=seen or CollectionSeenStore(path=None))


def test_a_licensed_collection_syncs_only_its_books(upstream):
    upstream.collections = {"col-1": ["li_two"]}
    bridge = collection_bridge(upstream, granted)
    assert [book.item_id for book in bridge.refresh(force=True)] == ["li_two"]
    assert "Ada Reader" in bridge.license_note


def test_an_old_book_put_in_the_collection_later_is_still_sent(upstream):
    """Adding a book to a collection need not touch its modified stamp, which the cursor reads."""
    upstream.collections = {"col-1": ["li_two"]}
    bridge = collection_bridge(upstream, granted)
    batch, token, _ = select_for_sync(bridge.refresh(force=True), SyncToken())
    assert [book.item_id for book in batch] == ["li_two"]

    upstream.collections["col-1"].append("li_one")  # older than li_two, behind the cursor
    batch, _, _ = select_for_sync(bridge.refresh(force=True), token)
    assert [book.item_id for book in batch] == ["li_one"]


def test_first_sightings_survive_a_restart(tmp_path):
    path = str(tmp_path / "collection-seen.json")
    first = CollectionSeenStore(path).first_seen("col-1", ["li_a"])
    assert CollectionSeenStore(path).first_seen("col-1", ["li_a"]) == first


def test_without_a_license_the_whole_library_still_syncs(upstream):
    upstream.collections = {"col-1": ["li_two"]}
    bridge = collection_bridge(upstream, refused)
    assert sorted(book.item_id for book in bridge.refresh(force=True)) == ["li_one", "li_two"]
    assert ("collection_item_ids", "col-1") not in upstream.calls
    assert "whole library" in bridge.license_note


def test_without_a_collection_the_license_is_never_checked(upstream):
    def must_not_run(key):
        raise AssertionError("checked")

    config = Config(abs_url="http://abs.local", abs_token="t", device_token="d")
    bridge = Bridge(config, client=upstream, reading_states=ReadingStateStore(path=None),
                    unlock=must_not_run)
    assert bridge.license_note is None
    assert len(bridge.refresh(force=True)) == 2


def test_the_environment_names_the_collection_and_the_key():
    config = Config.from_env({"KOBOBRIDGE_ABS_URL": "http://abs.local", "KOBOBRIDGE_ABS_TOKEN": "t",
                              "KOBOBRIDGE_COLLECTION_ID": " col-1 ", "KOBOBRIDGE_LICENSE_KEY": "KEY"})
    assert config.collection_id == "col-1"
    assert config.license_key == "KEY"


class FakeSession:
    def __init__(self, payload):
        self.headers = {}
        self.payload = payload
        self.urls = []

    def get(self, url, timeout=None, **kwargs):
        self.urls.append(url)
        return FakeResponse(payload=self.payload)


def test_a_collection_is_read_with_a_get_on_its_own_route():
    session = FakeSession({"id": "col-1", "name": "Holiday",
                           "books": [item("li_a", "A"), item("li_b", "B"), {"media": {}}]})
    client = Audiobookshelf("http://abs.local", "t", session=session)
    assert client.collection_item_ids("col-1") == {"li_a", "li_b"}
    assert session.urls == ["http://abs.local/api/collections/col-1"]
