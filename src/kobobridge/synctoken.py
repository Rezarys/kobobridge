"""The ``x-kobo-synctoken`` header.

The eReader stores whatever the server puts in this header and sends it back on the next sync.
It is the only state the bridge keeps, which is why the bridge needs no database: the device
carries its own cursor.
"""

import json
from base64 import b64decode, b64encode
from datetime import datetime, timezone

HEADER = "x-kobo-synctoken"
VERSION = "1-1-0"
MIN_VERSION = "1-0-0"

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_FIELDS = ("books_last_created", "books_last_modified")


def to_epoch(moment):
    return (moment - EPOCH).total_seconds()


def from_epoch(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return EPOCH


class SyncToken:
    """A cursor over the library, carried by the device."""

    def __init__(self, books_last_created=EPOCH, books_last_modified=EPOCH):
        self.books_last_created = books_last_created
        self.books_last_modified = books_last_modified

    @classmethod
    def from_headers(cls, headers):
        raw = headers.get(HEADER, "") or ""
        if not raw:
            return cls()
        # On its very first sync the device may still be holding a token issued by the
        # manufacturer's store. That one has a dot in it and means nothing here, so start over.
        if "." in raw:
            return cls()
        try:
            padded = raw + "=" * (-len(raw) % 4)
            payload = json.loads(b64decode(padded))
            if not isinstance(payload, dict):
                raise ValueError("token is not an object")
            if str(payload.get("version", "")) < MIN_VERSION:
                raise ValueError("token version is too old")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("token carries no data object")
        except Exception:
            return cls()
        return cls(**{name: from_epoch(data.get(name)) for name in _FIELDS})

    def to_header_value(self):
        payload = {
            "version": VERSION,
            "data": {name: to_epoch(getattr(self, name)) for name in _FIELDS},
        }
        return b64encode(json.dumps(payload).encode()).decode("utf-8")

    def __repr__(self):
        return "SyncToken(created={0.books_last_created}, modified={0.books_last_modified})".format(self)
