"""Where reading progress lives.

The eReader pushes reading progress back to whatever server it syncs with. That progress is
kept here, in a small file owned by the bridge, and it is never pushed into Audiobookshelf: the
library stays untouched, which is the promise the README makes. Pass ``path=None`` for an
in memory store that forgets everything on exit.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def default_path():
    """Follow the XDG data directory, so the file lands somewhere a user can find it."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "kobobridge", "reading-state.json")


class ReadingStateStore:
    """Reading progress, keyed by the book identifier the device knows."""

    def __init__(self, path=None):
        self.path = path
        self._lock = threading.Lock()
        self._entries = {}
        self._load()

    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            self._entries = {
                key: value for key, value in loaded.items() if isinstance(value, dict)
            }

    def _flush(self):
        if not self.path:
            return
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        # Write beside the target then rename, so a crash never leaves a half written file.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=folder or ".", delete=False, suffix=".tmp"
        )
        try:
            with handle:
                json.dump(self._entries, handle)
            os.replace(handle.name, self.path)
        except OSError:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    def get(self, book_uuid):
        with self._lock:
            return dict(self._entries.get(book_uuid) or {})

    def put(self, book_uuid, entry):
        with self._lock:
            self._entries[book_uuid] = dict(entry)
            self._flush()

    def __len__(self):
        return len(self._entries)
