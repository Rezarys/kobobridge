"""Serve an Audiobookshelf ebook library to a Kobo eReader over the native sync protocol."""

__version__ = "0.1.3"

__all__ = ["__version__", "create_app"]


def create_app(config=None):
    """Build the WSGI application. Imported lazily to keep ``import kobobridge`` cheap."""
    from .app import create_app as _create_app

    return _create_app(config)
