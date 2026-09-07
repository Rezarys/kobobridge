"""The endpoints the eReader calls.

Everything sits under ``/kobo/<device token>/``. The device is told to come here by editing one
line of its own configuration file, and the token is one this project generated. No account and
no credential belonging to the device manufacturer is involved at any point.
"""

import base64
import os
import secrets
import uuid
from datetime import timezone

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from .abs import AudiobookshelfError
from .synctoken import HEADER as SYNC_HEADER
from .synctoken import SyncToken
from .state import utcnow

# The device stops asking for more once a sync answer gets long, so answers are cut into
# batches and the next one is announced with the continue header.
SYNC_ITEM_LIMIT = 100

# One category identifier is enough for a library that does not sell anything.
DEFAULT_CATEGORY = "00000000-0000-0000-0000-000000000001"

bp = Blueprint("kobo", __name__, url_prefix="/kobo/<device_token>")


def kobo_time(moment):
    """The timestamp shape the device parses."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bridge():
    return current_app.extensions["kobobridge"]


@bp.url_value_preprocessor
def take_token(_endpoint, values):
    if values is not None:
        request.device_token = values.pop("device_token", None)


@bp.url_defaults
def put_token_back(_endpoint, values):
    if "device_token" not in values:
        values["device_token"] = getattr(request, "device_token", None)


@bp.before_request
def check_token():
    given = getattr(request, "device_token", None) or ""
    if not secrets.compare_digest(str(given), bridge().config.device_token):
        return jsonify({"error": "unknown device token"}), 401
    return None


def base_url():
    """Where the device should come back to. Behind a reverse proxy, set the public url."""
    configured = bridge().config.public_url
    if configured:
        return configured
    return request.url_root.rstrip("/")


def prefix():
    return "{0}/kobo/{1}".format(base_url(), bridge().config.device_token)


# ---------------------------------------------------------------------------
# Library lookups
# ---------------------------------------------------------------------------


def find_book(book_uuid):
    """Resolve a device side identifier back to a library item, refreshing once if needed."""
    library = bridge()
    book = library.book_by_uuid(book_uuid)
    if book is None:
        library.refresh(force=True)
        book = library.book_by_uuid(book_uuid)
    return book


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def download_urls(book):
    """The formats offered for one book. The device picks the first one it understands."""
    url = "{0}/download/{1}".format(prefix(), book.item_id)
    formats = ["KEPUB"] if book.is_kepub else ["EPUB3", "EPUB"]
    return [
        {
            "Format": fmt,
            "Size": book.size,
            "Url": url,
            "Platform": "Generic",
        }
        for fmt in formats
    ]


def book_metadata(book):
    book_uuid = book.uuid
    metadata = {
        "Categories": [DEFAULT_CATEGORY],
        "Contributors": list(book.authors),
        "ContributorRoles": [{"Name": name} for name in book.authors],
        "CoverImageId": book_uuid,
        "CrossRevisionId": book_uuid,
        "CurrentDisplayPrice": {"CurrencyCode": "USD", "TotalAmount": 0},
        "CurrentLoveDisplayPrice": {"TotalAmount": 0},
        "Description": book.description,
        "DownloadUrls": download_urls(book),
        "EntitlementId": book_uuid,
        "ExternalIds": [],
        "Genre": DEFAULT_CATEGORY,
        "IsEligibleForKoboLove": False,
        "IsInternetArchive": False,
        "IsPreOrder": False,
        "IsSocialEnabled": True,
        "Language": book.language,
        "PhoneticPronunciations": {},
        "PublicationDate": kobo_time(book.created),
        "Publisher": {"Imprint": "", "Name": book.publisher},
        "RevisionId": book_uuid,
        "Title": book.title,
        "WorkId": book_uuid,
    }
    if book.series:
        metadata["Series"] = {
            "Name": book.series,
            "Number": int(book.series_index) if book.series_index else 1,
            "NumberFloat": float(book.series_index) if book.series_index else 1.0,
            "Id": str(uuid.uuid5(uuid.NAMESPACE_DNS, book.series)),
        }
    return metadata


def book_entitlement(book):
    book_uuid = book.uuid
    return {
        "Accessibility": "Full",
        "ActivePeriod": {"From": kobo_time(utcnow())},
        "Created": kobo_time(book.created),
        "CrossRevisionId": book_uuid,
        "Id": book_uuid,
        "IsHiddenFromArchive": False,
        "IsLocked": False,
        "IsRemoved": False,
        "LastModified": kobo_time(book.modified),
        "OriginCategory": "Imported",
        "RevisionId": book_uuid,
        "Status": "Active",
    }


def reading_state(book):
    """What the device last told us about this book, or a clean unread state."""
    stored = bridge().reading_states.get(book.uuid)
    last_modified = stored.get("LastModified") or kobo_time(book.modified)
    response = {
        "EntitlementId": book.uuid,
        "Created": kobo_time(book.created),
        "LastModified": last_modified,
        "PriorityTimestamp": last_modified,
        "StatusInfo": stored.get("StatusInfo")
        or {
            "LastModified": last_modified,
            "Status": "ReadyToRead",
            "TimesStartedReading": 0,
        },
        "Statistics": stored.get("Statistics") or {"LastModified": last_modified},
        "CurrentBookmark": stored.get("CurrentBookmark") or {"LastModified": last_modified},
    }
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@bp.route("/v1/initialization")
def initialization():
    """Hand the device a resource table that points back at this bridge."""
    return jsonify({"Resources": bridge().resources(prefix(), base_url())})


@bp.route("/v1/auth/device", methods=["POST"])
@bp.route("/v1/auth/refresh", methods=["POST"])
def auth():
    """The device wants a token here. It is never checked again, so any token will do."""
    payload = request.get_json(silent=True) or {}
    return jsonify(
        {
            "AccessToken": base64.b64encode(os.urandom(24)).decode("utf-8"),
            "RefreshToken": base64.b64encode(os.urandom(24)).decode("utf-8"),
            "TokenType": "Bearer",
            "TrackingId": str(uuid.uuid4()),
            "UserKey": payload.get("UserKey", ""),
        }
    )


def select_for_sync(books, token):
    """Pick the batch to send, and say where the cursor lands.

    Returns ``(batch, new_token, more_to_come)``. Books are compared against the cursor the
    device sent back, so a device that has seen everything gets an empty answer.

    Selection runs on the modified stamp alone. A freshly added book always has a modified
    stamp at least as recent as its added stamp, so nothing is missed, and a book is offered
    exactly once. The created cursor decides only whether a book is announced as new or as
    changed, which is why it never takes part in the filter: doing so would resend books that
    the device already holds every time a batch was cut short.
    """
    candidates = [book for book in books if book.modified > token.books_last_modified]
    candidates.sort(key=lambda book: (book.modified, book.item_id))
    batch, rest = candidates[:SYNC_ITEM_LIMIT], candidates[SYNC_ITEM_LIMIT:]
    more = bool(rest)
    if more:
        # Never park the cursor in the middle of a group of books sharing one timestamp: the
        # rest of that group would be skipped forever on the next sync.
        boundary = rest[0].modified
        trimmed = [book for book in batch if book.modified < boundary]
        # Unless the whole batch shares that timestamp, in which case send it and move on.
        batch = trimmed or batch
    new_token = SyncToken(
        books_last_created=token.books_last_created,
        books_last_modified=token.books_last_modified,
    )
    if batch:
        new_token.books_last_modified = max(book.modified for book in batch)
        # Only over the batch that was actually sent. A book still waiting for the next batch
        # must not be labelled as merely changed when it finally goes out.
        new_token.books_last_created = max(
            [token.books_last_created] + [book.created for book in batch]
        )
    return batch, new_token, more


@bp.route("/v1/library/sync")
def sync():
    library = bridge()
    token = SyncToken.from_headers(request.headers)
    try:
        # A sync always reads the library afresh: a book added a moment ago has to show up.
        # The cache exists for the burst of metadata and cover calls that follows.
        books = library.refresh(force=True)
    except AudiobookshelfError as error:
        current_app.logger.error("sync failed: %s", error)
        return jsonify({"error": str(error)}), 502

    batch, new_token, more = select_for_sync(books, token)
    results = []
    for book in batch:
        entitlement = {
            "BookEntitlement": book_entitlement(book),
            "BookMetadata": book_metadata(book),
            "ReadingState": reading_state(book),
        }
        key = (
            "NewEntitlement"
            if book.created > token.books_last_created
            else "ChangedEntitlement"
        )
        results.append({key: entitlement})

    headers = {SYNC_HEADER: new_token.to_header_value()}
    if more:
        headers["x-kobo-sync"] = "continue"
    response = jsonify(results)
    response.headers.extend(headers)
    return response


@bp.route("/v1/library/<book_uuid>/metadata")
def metadata(book_uuid):
    book = find_book(book_uuid)
    if book is None:
        return jsonify([]), 404
    return jsonify([book_metadata(book)])


@bp.route("/v1/library/<book_uuid>/state", methods=["GET", "PUT"])
def state(book_uuid):
    book = find_book(book_uuid)
    if book is None:
        return jsonify([]), 404
    if request.method == "GET":
        return jsonify([reading_state(book)])

    payload = request.get_json(silent=True) or {}
    incoming = (payload.get("ReadingStates") or [{}])[0]
    now = kobo_time(utcnow())
    entry = {"LastModified": now}
    outcome = {"EntitlementId": book_uuid}
    for field, result in (
        ("CurrentBookmark", "CurrentBookmarkResult"),
        ("Statistics", "StatisticsResult"),
        ("StatusInfo", "StatusInfoResult"),
    ):
        value = incoming.get(field)
        if value:
            value = dict(value)
            value["LastModified"] = now
            entry[field] = value
            outcome[result] = {"Result": "Success"}
    bridge().reading_states.put(book_uuid, entry)
    return jsonify({"RequestResult": "Success", "UpdateResults": [outcome]})


@bp.route("/download/<item_id>")
def download(item_id):
    """Stream one ebook straight through, so a large book never lands in memory."""
    try:
        upstream = bridge().client.ebook_stream(item_id)
    except AudiobookshelfError as error:
        current_app.logger.error("download failed: %s", error)
        return jsonify({"error": str(error)}), 502
    headers = {}
    for name in ("Content-Length", "Content-Disposition"):
        if name in upstream.headers:
            headers[name] = upstream.headers[name]
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        headers=headers,
        content_type=upstream.headers.get("Content-Type", "application/epub+zip"),
    )


@bp.route("/<book_uuid>/<width>/<height>/<is_greyscale>/image.jpg")
@bp.route("/<book_uuid>/<width>/<height>/<quality>/<is_greyscale>/image.jpg")
def cover(book_uuid, width, height, is_greyscale, quality=None):
    book = find_book(book_uuid)
    if book is None:
        return jsonify({"error": "unknown book"}), 404
    try:
        upstream = bridge().client.cover_stream(book.item_id, width=width, height=height)
    except AudiobookshelfError as error:
        current_app.logger.error("cover failed: %s", error)
        return jsonify({"error": str(error)}), 502
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=32768)),
        content_type=upstream.headers.get("Content-Type", "image/jpeg"),
    )


@bp.route("/v1/library/tags", methods=["GET", "POST", "DELETE"])
@bp.route("/v1/library/tags/<path:rest>", methods=["GET", "POST", "PUT", "DELETE"])
def tags(rest=None):
    """Shelves are not mirrored yet. Answering politely keeps the device from retrying."""
    return jsonify([])


@bp.route("/v1/analytics/<path:rest>", methods=["GET", "POST"])
@bp.route("/v1/assets", methods=["GET"])
@bp.route("/v1/deals", methods=["GET", "POST"])
@bp.route("/v1/affiliate", methods=["GET", "POST"])
@bp.route("/v1/products", methods=["GET", "POST"])
@bp.route("/v1/products/<path:rest>", methods=["GET", "POST"])
@bp.route("/v1/user/<path:rest>", methods=["GET", "POST"])
def quiet(rest=None):
    """Everything the store would answer and a private library has no opinion about."""
    return jsonify({})


@bp.route("/", methods=["GET"])
def root():
    return jsonify({"Resources": {}})
