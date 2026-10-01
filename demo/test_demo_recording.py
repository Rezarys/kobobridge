"""Record the terminal session shown in the demo, and render it as an animated SVG.

Not part of the test suite (``testpaths`` is ``tests``). Run it on purpose:

    python -m pytest demo/test_demo_recording.py

Every line the session shows after a ``$`` command is printed by kobobridge itself. What is not
real is said on screen: the Audiobookshelf server is a stand-in holding six made-up library
entries (public domain titles), the eReader is a test client making the main requests a reader
makes when it syncs (initialization, library sync, downloads), and the license server is a stand-in that accepts one test key. In the released 0.2.0 no
key unlocks anything yet, because the store and product ids are not set.

Writes, next to this file: ``transcript.json`` (every line of the session, for review) and
``kobobridge-demo.svg`` (the animated replay).
"""

import html
import io
import json
import logging
import os
import textwrap

import pytest
import waitress

import kobobridge.app
import kobobridge.license
from kobobridge import __version__
from kobobridge.__main__ import main
from kobobridge.abs import EBOOK_FORMATS, Book

HERE = os.path.dirname(os.path.abspath(__file__))

ABS_URL = "http://192.168.1.10:13378"
PUBLIC_URL = "http://192.168.1.10:8484"
DEVICE_TOKEN = "demo-device"
COLLECTION = "col_summer"
TEST_KEY = "TEST-0000-DEMO-KEY"

TITLES = [
    ("li_01", "Pride and Prejudice", "Jane Austen", "epub"),
    ("li_02", "Moby-Dick", "Herman Melville", "epub"),
    ("li_03", "Frankenstein", "Mary Shelley", "kepub.epub"),
    ("li_04", "Dracula", "Bram Stoker", "epub"),
    ("li_05", "The Time Machine", "H. G. Wells", "epub"),
    ("li_06", "Middlemarch", "George Eliot", "epub"),
    ("li_07", "The Odyssey", "Homer", ""),  # an audiobook: the bridge skips it
]
IN_COLLECTION = {"li_03", "li_05"}


class Body:
    def __init__(self, data, content_type):
        self.data = data
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
        self.status_code = 200

    def iter_content(self, chunk_size=1):
        stream = io.BytesIO(self.data)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                return
            yield chunk


class StandInLibrary:
    """Answers the reads the bridge makes, from the made-up entries above."""

    def __init__(self, *args, **kwargs):
        self.items = []
        for number, (item_id, title, author, fmt) in enumerate(TITLES):
            stamp = 1_700_000_000_000 + number * 60_000
            self.items.append(
                {
                    "id": item_id,
                    "addedAt": stamp,
                    "updatedAt": stamp,
                    "media": {
                        "metadata": {"title": title, "authorName": author, "language": "en"},
                        "ebookFormat": fmt,
                        "size": 400_000,
                    },
                }
            )

    def ping(self):
        return 1

    def all_books(self, only=None):
        books = [
            Book.from_item(raw)
            for raw in self.items
            if str(raw["media"]["ebookFormat"]).lower().split(".")[0] in EBOOK_FORMATS
        ]
        books.sort(key=lambda book: (book.modified, book.item_id))
        return books

    def collection_item_ids(self, collection_id):
        return set(IN_COLLECTION) if collection_id == COLLECTION else set()

    def ebook_stream(self, item_id):
        return Body(b"PK\x03\x04" + b"\0" * 2044, "application/epub+zip")

    def cover_stream(self, item_id, width=None):
        return Body(b"\xff\xd8\xff" + b"\0" * 509, "image/jpeg")


class StandInLicenseServer:
    """Plays the store's license endpoints for one test key, and counts the calls."""

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, **kwargs):
        self.calls.append(url)
        ok = (json or {}).get("license_key") == TEST_KEY
        meta = {"store_id": 1, "product_id": 2, "customer_name": "Demo Buyer"}
        if url.endswith("/validate"):
            payload = {"valid": ok, "error": None if ok else "license_key not found", "meta": meta}
        else:
            payload = {
                "activated": ok,
                "error": None if ok else "license_key not found",
                "license_key": {"status": "active"},
                "instance": {"id": "demo-instance"},
                "meta": meta,
            }

        class Answer:
            status_code = 200

            def json(self):
                return payload

        return Answer()


def fresh_process_logging():
    """Each command is a new process in the session, so the logger starts unconfigured.

    An unconfigured process prints warnings bare on stderr (Python's last resort handler); pytest
    would otherwise catch them in its own log capture and the screen would not show them.
    """
    logger = logging.getLogger("kobobridge")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.addHandler(logging.lastResort)
    logger.propagate = False
    logger.setLevel(logging.NOTSET)


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for name in list(os.environ):
        if name.startswith("KOBOBRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(kobobridge.app, "Audiobookshelf", StandInLibrary)
    server = StandInLicenseServer()
    monkeypatch.setattr(kobobridge.license.requests, "post", server.post)
    return server


def test_record_and_render(session, monkeypatch, capsys):
    steps = []

    def note(text):
        steps.append(("note", text))

    def export(name, value):
        steps.append(("cmd", "export {0}={1}".format(name, value)))
        monkeypatch.setenv(name, value)

    def run(argv):
        steps.append(("cmd", "kobobridge " + " ".join(argv)))
        fresh_process_logging()
        capsys.readouterr()
        main(argv)
        captured = capsys.readouterr()
        for line in (captured.err + captured.out).rstrip("\n").split("\n"):
            steps.append(("out", line))

    received = []

    def reader_syncs(app, host, port, threads):
        """Stands in for waitress: the requests a reader makes when it syncs, then stop."""
        client = app.test_client()
        prefix = "/kobo/" + DEVICE_TOKEN
        client.get(prefix + "/v1/initialization")
        answer = client.get(prefix + "/v1/library/sync")
        for entry in answer.get_json():
            book = list(entry.values())[0]["BookMetadata"]
            received.append(book["Title"])
            url = book["DownloadUrls"][0]["Url"]
            client.get(url[len(PUBLIC_URL):])

    monkeypatch.setattr(waitress, "serve", reader_syncs)

    # Scene 1: the free tool, the whole library.
    note("# kobobridge {0}: made-up library, stand-in servers, test key.".format(__version__))
    note("# Free part: the whole Audiobookshelf library goes to the eReader.")
    export("KOBOBRIDGE_ABS_URL", ABS_URL)
    export("KOBOBRIDGE_ABS_TOKEN", "demo-abs-token")
    export("KOBOBRIDGE_PUBLIC_URL", PUBLIC_URL)
    export("KOBOBRIDGE_DEVICE_TOKEN", DEVICE_TOKEN)
    run(["check"])
    steps.append(("clear", ""))

    note("# Run the bridge. Below, a test client makes the main requests an eReader makes on sync.")
    run(["run"])
    note("# On the eReader: {0} books, the whole library.".format(len(received)))
    assert len(received) == 6
    steps.append(("clear", ""))

    # Scene 2: the paid feature, one collection, refused without a key.
    note("# Paid part: sync one Audiobookshelf collection instead of the whole library.")
    note("# Without a key, the bridge says why and keeps syncing everything.")
    export("KOBOBRIDGE_COLLECTION_ID", COLLECTION)
    run(["check", "--limit", "3"])
    steps.append(("clear", ""))

    # Scene 3: with a key. A test key, against a stand-in license server.
    note("# With a key. Here a test key and a stand-in key server: the released 0.2.0 accepts no key "
         "until sales open.")
    # The released 0.2.0 has no store and product ids yet, so no key unlocks; the demo sets the
    # ids its stand-in server answers with.
    monkeypatch.setattr(kobobridge.license, "STORE_ID", 1)
    monkeypatch.setattr(kobobridge.license, "PRODUCT_ID", 2)
    export("KOBOBRIDGE_LICENSE_KEY", TEST_KEY)
    run(["check"])
    assert len(session.calls) == 2  # validate, then activate, once
    note("# The key was checked online once ({0} requests). Later starts read a local file.".format(
        len(session.calls)))
    steps.append(("clear", ""))

    note("# Run the bridge again; the eReader syncs.")
    received.clear()
    run(["run"])
    assert len(session.calls) == 2
    assert sorted(received) == ["Frankenstein", "The Time Machine"]
    note("# This sync sends only the {0} books of the collection. No new key check.".format(
        len(received)))
    note("# Whole library sync stays free, MIT. The key is 15 EUR, paid once, no subscription.")

    write_transcript(steps)
    svg = render_svg(steps)
    with open(os.path.join(HERE, "kobobridge-demo.svg"), "w", encoding="utf-8") as handle:
        handle.write(svg)


def write_transcript(steps):
    """The replay as data: one [kind, text] pair per line, kind being cmd, out, note or clear."""
    with open(os.path.join(HERE, "transcript.json"), "w", encoding="utf-8") as handle:
        json.dump([list(step) for step in steps], handle, indent=1)
        handle.write("\n")


# Rendering. One scene per screen; inside a scene, lines appear one after the other.
COLUMNS = 104
CHAR_W = 8.4
LINE_H = 19
TOP = 44
LEFT = 16
PAUSE_BEFORE_CMD = 1.6
PAUSE_OUT = 0.12
PAUSE_NOTE = 2.6
HOLD_SCENE = 5.0
COLORS = {"cmd": "#e6e6e6", "out": "#b8c4b8", "note": "#e5c07b"}


def wrap(text):
    # textwrap drops leading spaces, so a line that fits is left exactly as printed.
    if len(text) <= COLUMNS:
        return [text]
    return textwrap.wrap(text, COLUMNS, subsequent_indent="  ", break_on_hyphens=False) or [""]


def render_svg(steps):
    scenes, current = [], []
    for step in steps:
        if step[0] == "clear":
            scenes.append(current)
            current = []
        else:
            current.append(step)
    scenes.append(current)

    timeline, t = [], 0.0  # (scene, row, kind, text, appears)
    rows_max = 0
    ends = []
    for number, scene in enumerate(scenes):
        row = 0
        for kind, text in scene:
            t += {"cmd": PAUSE_BEFORE_CMD, "out": PAUSE_OUT, "note": PAUSE_NOTE}[kind]
            shown = ("$ " + text) if kind == "cmd" else text
            for piece in wrap(shown):
                timeline.append((number, row, kind, piece, t))
                row += 1
        rows_max = max(rows_max, row)
        t += HOLD_SCENE
        ends.append(t)
    total = t
    width = int(LEFT * 2 + COLUMNS * CHAR_W)
    height = int(TOP + rows_max * LINE_H + 20)

    def pct(seconds):
        return "{0:.3f}".format(min(100.0, max(0.0, 100.0 * seconds / total)))

    css, body = [], []
    for index, (scene, row, kind, text, appears) in enumerate(timeline):
        on, off = appears, ends[scene] - 0.05
        css.append(
            "@keyframes k{0}{{0%,{1}%{{opacity:0}}{2}%,{3}%{{opacity:1}}{4}%,100%{{opacity:0}}}}"
            ".l{0}{{animation:k{0} {5:.2f}s linear infinite}}".format(
                index, pct(on - 0.01), pct(on), pct(off), pct(off + 0.01), total
            )
        )
        body.append(
            '<text class="l{0}" x="{1}" y="{2}" fill="{3}" xml:space="preserve">{4}</text>'.format(
                index, LEFT, TOP + (row + 1) * LINE_H - 5, COLORS[kind],
                html.escape(text, quote=False),
            )
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        'role="img" aria-label="kobobridge demo, a replay of a terminal session">\n'
        "<title>kobobridge {v} demo: made-up library, stand-in servers, test key</title>\n"
        "<style>text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;"
        "font-size:14px;opacity:0}}{css}</style>\n"
        '<rect width="{w}" height="{h}" rx="8" fill="#1d1f21"/>\n'
        '<rect width="{w}" height="28" rx="8" fill="#2b2e31"/>\n'
        '<circle cx="18" cy="14" r="5" fill="#ff5f56"/><circle cx="36" cy="14" r="5" fill="#ffbd2e"/>'
        '<circle cx="54" cy="14" r="5" fill="#27c93f"/>\n'
        '<text x="{mid}" y="19" fill="#9a9a9a" text-anchor="middle" style="opacity:1">'
        "kobobridge {v} demo (replay, {secs:.0f} s, loops)</text>\n"
        "{body}\n</svg>\n"
    ).format(
        w=width, h=height, v=__version__, css="".join(css), body="\n".join(body),
        mid=width // 2, secs=total,
    )
