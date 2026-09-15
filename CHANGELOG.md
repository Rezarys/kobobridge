# Changelog

## 0.1.3

- The bridge logs what it does. Every call from the reader is written with its status, its size and how long it took, a library listing reports how many books it found and how long it took, and a failure names the book it was about when the bridge knows which one it was. The device token is replaced with `<token>` in the log, so a log can be pasted into a bug report as it is. `--log-level debug` adds every call made to Audiobookshelf. Before this, the server ran under waitress, which writes nothing per request, and there was no way to tell a reader that never asked for a cover from one that asked and got an error.
- The container image sets `PYTHONUNBUFFERED`. Output was block buffered because a container has no terminal attached, so lines waited in the buffer instead of reaching `docker logs` and even the startup text looked missing.
- Nine of the forty nine addresses handed to the reader answered 404, among them `get_download_keys` and `get_download_link`, the pair a reader asks for when it begins a download. The others were `add_entitlement`, `delete_entitlement`, `library_search`, `categories`, `configuration_data`, `funnel_metrics` and `productsv2`. All of them now answer. A test walks the whole table and fails if any address in it refuses.
- Covers now carry their length through from Audiobookshelf, so a served cover shows its size in the log instead of a dash.
- `kobobridge --version` reported 0.1.1 while the package was 0.1.2.

Reported by @zeeohee0 in issue 3.

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
