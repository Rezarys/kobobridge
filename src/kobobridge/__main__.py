"""The command line: check the connection, print the setup line, run the server."""

import argparse
import os
import sys

from . import __version__
from .abs import AudiobookshelfError
from .config import Config, ConfigError


def _load_config():
    try:
        return Config.from_env()
    except ConfigError as error:
        sys.stderr.write("kobobridge: {0}\n".format(error))
        raise SystemExit(2)


def _endpoint(config, host, port):
    if config.public_url:
        return "{0}/kobo/{1}".format(config.public_url, config.device_token)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return "http://{0}:{1}/kobo/{2}".format(shown, port, config.device_token)


def _print_setup(config, host, port):
    endpoint = _endpoint(config, host, port)
    print("")
    print("Point the eReader at this bridge:")
    print("")
    print("  1. Plug the eReader into a computer over USB.")
    print("  2. Open the hidden folder .kobo/Kobo/ and edit 'Kobo eReader.conf'.")
    print("  3. Under [OneStoreServices], set:")
    print("")
    print("       api_endpoint={0}".format(endpoint))
    print("")
    print("  4. Eject, then sync from the eReader.")
    print("")
    if not os.environ.get("KOBOBRIDGE_DEVICE_TOKEN"):
        print("This token was generated for this run only. To keep it across restarts, set:")
        print("")
        print("  KOBOBRIDGE_DEVICE_TOKEN={0}".format(config.device_token))
        print("")


def cmd_check(args):
    config = _load_config()
    from .app import Bridge

    bridge = Bridge(config)
    try:
        count = bridge.client.ping()
    except AudiobookshelfError as error:
        sys.stderr.write("kobobridge: {0}\n".format(error))
        return 1
    print("Audiobookshelf answered: {0} librar{1}.".format(count, "y" if count == 1 else "ies"))
    try:
        books = bridge.refresh(force=True)
    except AudiobookshelfError as error:
        sys.stderr.write("kobobridge: {0}\n".format(error))
        return 1
    print("Ebooks the eReader can read: {0}.".format(len(books)))
    for book in books[: args.limit]:
        author = ", ".join(book.authors) or "unknown author"
        print("  {0} ({1}) [{2}]".format(book.title, author, book.ebook_format))
    if len(books) > args.limit:
        print("  and {0} more.".format(len(books) - args.limit))
    if not books:
        print("")
        print("No epub found. Audiobookshelf only reports one when the library item has an")
        print("ebook file; audiobooks and podcasts are skipped on purpose.")
    return 0


def cmd_setup(args):
    config = _load_config()
    _print_setup(config, args.host, args.port)
    return 0


def cmd_run(args):
    config = _load_config()
    from .app import create_app
    from .logs import LEVELS, configure, get_logger
    from waitress import serve

    configure(level=LEVELS.get(args.log_level))
    app = create_app(config)
    _print_setup(config, args.host, args.port)
    sys.stdout.flush()
    get_logger().info(
        "listening on %s:%s, logging at %s level", args.host, args.port, args.log_level
    )
    serve(app, host=args.host, port=args.port, threads=args.threads)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kobobridge",
        description="Serve an Audiobookshelf ebook library to a Kobo eReader.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    def with_address(sub):
        sub.add_argument("--host", default="0.0.0.0", help="address to bind, default 0.0.0.0")
        sub.add_argument(
            "--port", type=int, default=int(os.environ.get("KOBOBRIDGE_PORT", 8484)),
            help="port to bind, default 8484",
        )
        return sub

    run = with_address(subparsers.add_parser("run", help="run the bridge"))
    run.add_argument("--threads", type=int, default=8, help="worker threads, default 8")
    run.add_argument(
        "--log-level", default=os.environ.get("KOBOBRIDGE_LOG_LEVEL", "info").lower(),
        choices=("debug", "info", "warning", "error"),
        help="how much to log, default info; debug adds every call made to Audiobookshelf",
    )
    run.set_defaults(func=cmd_run)

    check = subparsers.add_parser("check", help="test the Audiobookshelf connection")
    check.add_argument("--limit", type=int, default=10, help="how many titles to list")
    check.set_defaults(func=cmd_check)

    setup = with_address(subparsers.add_parser("setup", help="print the eReader setup line"))
    setup.set_defaults(func=cmd_setup)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
