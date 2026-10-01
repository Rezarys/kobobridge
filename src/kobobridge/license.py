"""The license that unlocks syncing a single collection.

Syncing the whole library is free and stays free. Syncing one collection instead needs a license
key bought from the store. The key is checked and activated online once, against the license endpoints
of the store's payment provider, and the answer is kept in a small file beside the reading state.
Every later start reads that file and makes no network call at all.

The endpoints, the request fields and the response fields used here are the ones in the
provider's official JavaScript client (``src/license`` of ``lmsqueezy/lemonsqueezy.js``): JSON
POSTs to ``/v1/licenses/validate`` with ``license_key`` and to ``/v1/licenses/activate`` with
``license_key`` and ``instance_name``, no API key, and answers carrying ``valid`` or
``activated``, ``error``, ``license_key.status``, ``instance.id`` and ``meta``. These two POSTs
go to the license server only; nothing is ever sent to Audiobookshelf but GET requests.

An invalid, missing or unreachable license never stops the bridge. It logs why, and the bridge
syncs the whole library as it always has.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .state import default_path

ACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/activate"
VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"

# The store and product a key must belong to. A key sold by any other store is refused, or any
# key from any shop using the same provider would unlock the feature. They are None until the
# product is on sale, and while they are None no key unlocks anything.
STORE_ID = None
PRODUCT_ID = None

INSTANCE_NAME = "kobobridge"


class LicenseError(RuntimeError):
    """The key could not be activated. The message says why, in words a user can act on."""


@dataclass
class License:
    customer: str
    instance_id: str


def license_cache_path():
    """Beside the reading state, so one volume holds everything the bridge owns."""
    return os.path.join(os.path.dirname(default_path()), "license.json")


def _fingerprint(key):
    # The key itself is never written to disk, only a hash that tells whether the cached
    # activation belongs to the key that is configured now.
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _read_cache(path, key):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or cached.get("key_sha256") != _fingerprint(key):
        return None
    if not cached.get("instance_id"):
        return None
    return License(customer=str(cached.get("customer") or ""), instance_id=cached["instance_id"])


def _write_cache(path, key, found):
    if not path:
        return
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=folder or ".", delete=False, suffix=".tmp"
    )
    try:
        with handle:
            json.dump(
                {
                    "key_sha256": _fingerprint(key),
                    "instance_id": found.instance_id,
                    "customer": found.customer,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                },
                handle,
            )
        os.replace(handle.name, path)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _post(url, payload, timeout):
    # Redirects are not followed: the key goes to this one address and nowhere else.
    return requests.post(
        url,
        json=payload,
        headers={"Accept": "application/json"},
        timeout=timeout,
        allow_redirects=False,
    )


def _same_product(payload, store_id, product_id):
    meta = payload.get("meta") or {}
    if meta.get("store_id") != store_id or meta.get("product_id") != product_id:
        raise LicenseError("this key was sold for another product")
    return meta


def _check(payload, store_id, product_id):
    """Turn the provider's answer to an activation into a License, or say why it is not one."""
    if not payload.get("activated"):
        raise LicenseError(
            "the license server refused the key: {0}".format(payload.get("error") or "no reason given")
        )
    meta = _same_product(payload, store_id, product_id)
    status = (payload.get("license_key") or {}).get("status")
    if status != "active":
        raise LicenseError("this key is {0}".format(status or "not active"))
    instance = payload.get("instance") or {}
    if not instance.get("id"):
        raise LicenseError("the license server did not return an activation")
    return License(customer=str(meta.get("customer_name") or ""), instance_id=str(instance["id"]))


def unlock(key, cache_path=None, post=_post, timeout=15.0, store_id=None, product_id=None):
    """Return the License for ``key``, from the local file if it was activated before.

    Only a first activation goes to the network. ``store_id`` and ``product_id`` default to the
    module constants; tests pass their own.
    """
    key = (key or "").strip()
    if not key:
        raise LicenseError("set KOBOBRIDGE_LICENSE_KEY to the key you received after purchase")
    cache_path = license_cache_path() if cache_path is None else cache_path
    cached = _read_cache(cache_path, key)
    if cached is not None:
        return cached
    store_id = STORE_ID if store_id is None else store_id
    product_id = PRODUCT_ID if product_id is None else product_id
    # Checked before any request, so a key bought elsewhere never spends one of its activations.
    if store_id is None or product_id is None:
        raise LicenseError("keys for collection sync are not on sale yet in this version")
    # Validating first costs no activation, so a key sold for another product is refused
    # before it spends one of its own.
    checked = _call(post, VALIDATE_URL, {"license_key": key}, timeout)
    if not checked.get("valid"):
        raise LicenseError(
            "the license server refused the key: {0}".format(checked.get("error") or "no reason given")
        )
    _same_product(checked, store_id, product_id)
    payload = _call(post, ACTIVATE_URL, {"license_key": key, "instance_name": INSTANCE_NAME}, timeout)
    found = _check(payload, store_id, product_id)
    _write_cache(cache_path, key, found)
    return found


def _call(post, url, payload, timeout):
    try:
        response = post(url, payload, timeout)
    except requests.RequestException as error:
        raise LicenseError("cannot reach the license server: {0}".format(error))
    if 300 <= response.status_code < 400:
        raise LicenseError("the license server answered with a redirect, which is not followed")
    try:
        answer = response.json()
    except ValueError:
        raise LicenseError(
            "the license server answered {0} without a readable body".format(response.status_code)
        )
    if not isinstance(answer, dict):
        raise LicenseError("the license server sent an answer that could not be read")
    return answer
