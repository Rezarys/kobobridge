# Changelog

## 0.1.2

- Download sizes are now the size of the ebook rather than the size of the whole library item. A library listing never carries `ebookFile`, so the size reported to the reader fell back to `media.size`, which on an item holding an audiobook and an ebook is the audio: one 716 KB epub was advertised as 419 MB, and a library of 1,374 books as 781 GB against 5 GB on disk. The true figure is read with a one byte ranged request and cached against the item's `updatedAt`, so only new or changed books are ever fetched. Reported and fixed by @Bothari.
- Covers are asked for as jpeg. The route the reader reads is `image.jpg`, and Audiobookshelf answers webp to any request whose `Accept` header is `*/*`.

## 0.1.1

- The readme no longer points at a container image that is not published. Build it from the `Dockerfile` in the repository instead.

## 0.1.0

First release.

- Serves the eReader sync protocol in front of an Audiobookshelf library, read only.
- Sends epub and kepub files, with covers, authors, series and descriptions.
- Cuts large libraries into batches so a first sync completes.
- Keeps reading progress pushed by the reader in a local file, never in Audiobookshelf.
- No telemetry, and no call to any server other than your own Audiobookshelf.
