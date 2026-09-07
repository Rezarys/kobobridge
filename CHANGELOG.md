# Changelog

## 0.1.1

- The readme no longer points at a container image that is not published. Build it from the `Dockerfile` in the repository instead.

## 0.1.0

First release.

- Serves the eReader sync protocol in front of an Audiobookshelf library, read only.
- Sends epub and kepub files, with covers, authors, series and descriptions.
- Cuts large libraries into batches so a first sync completes.
- Keeps reading progress pushed by the reader in a local file, never in Audiobookshelf.
- No telemetry, and no call to any server other than your own Audiobookshelf.
