# kobobridge

```
pip install kobobridge
```

Serves the ebooks in your [Audiobookshelf](https://www.audiobookshelf.org/) library to a Kobo eReader over the reader's own sync protocol, so books arrive over wifi instead of over a USB cable.

Answers [audiobookshelf#3504](https://github.com/advplyr/audiobookshelf/issues/3504), where people have been asking for Kobo sync for ebooks.

**Not affiliated with Audiobookshelf, and not affiliated with Rakuten Kobo Inc.** This is an independent project. "Kobo" is a trademark of its owner and is used here only to say which device the software talks to.

## What it does

Your Audiobookshelf server already holds your epubs. Your eReader already knows how to sync a library over the network. kobobridge sits between the two and answers the sync protocol on behalf of your own server:

```
  Kobo eReader  ->  kobobridge  ->  Audiobookshelf
                    (read only)
```

- New epubs in Audiobookshelf appear on the reader after a sync.
- Covers, authors, series and descriptions come across.
- Reading progress from the reader is stored by the bridge.
- Large libraries are sent in batches, so the first sync completes.

## What it does not do

Worth reading before you install.

- **It never writes to your library.** Every call to Audiobookshelf is a GET. Reading progress the reader pushes is kept in a small file next to the bridge, not pushed into Audiobookshelf, so progress does not yet show up in the Audiobookshelf web player. A test asserts this. The true size of each ebook is kept beside it, in `ebook-sizes.json`, and refreshed when the item changes in Audiobookshelf.
- **It does not touch any account on the manufacturer's store.** No store credential, no user key, no device id. The bridge issues its own token and never contacts the store.
- **It does not convert anything.** Only epub and kepub files are offered. Pdf, mobi and azw3 in your library are skipped rather than converted.
- **Audiobooks and podcasts are skipped.** The reader cannot play them.
- **Highlights and annotations are not carried.** The bridge serves no annotation address and never tells the reader about one, so nothing you highlight reaches it. The only things it keeps from what the reader pushes are the reading position, the read status and the reading statistics; anything else the reader sends with them is dropped, and a test asserts that.
- **Shelves are not mirrored.** One Audiobookshelf collection can be synced instead of the whole library with a paid key, see "Syncing one collection" below.
- **The author owns no reader, so nothing here was tested by the author against a device.** One reader of this repository has since run a complete sync on one model and one firmware version, and another reports it failing on theirs. See Status below for exactly what was exercised, by whom, and what is still open.
- **No telemetry, no analytics.** Nothing about your library leaves your machine. The only requests made to anything other than your Audiobookshelf are the key check described in "Syncing one collection", made only if you set a collection and a key, and not repeated once a key is activated.

## Setup

### 1. Get an Audiobookshelf API token

In Audiobookshelf: Settings, Users, pick your user, then copy the API token.

### 2. Run the bridge

```
export KOBOBRIDGE_ABS_URL=http://192.168.1.10:13378
export KOBOBRIDGE_ABS_TOKEN=paste-your-token-here
kobobridge check
```

The `check` command lists the ebooks the reader will be offered. If that looks right:

```
kobobridge run
```

It prints the one line you need to copy.

### 3. Point the reader at the bridge

```
Point the eReader at this bridge:

  1. Plug the eReader into a computer over USB.
  2. Open the hidden folder .kobo/Kobo/ and edit 'Kobo eReader.conf'.
  3. Under [OneStoreServices], set the line below. Create that
     section if it is not already there.

       api_endpoint=http://192.168.1.10:8484/kobo/rG9x2K7pQm4vB1nT8sYw

  4. Eject, then sync from the eReader.
```

Keep a copy of the original `api_endpoint` value. Putting it back returns the reader to normal.

To keep the same token across restarts, set `KOBOBRIDGE_DEVICE_TOKEN` to the value the bridge printed. Otherwise a new one is generated each run and the reader has to be pointed again.

## Syncing one collection

By default kobobridge syncs every ebook in your Audiobookshelf book libraries, and that stays free, along with every bug fix. A key, 15 euros once with no subscription, lets it sync a single Audiobookshelf collection instead, so a sync only sends the books in that collection. Books already on the reader stay there: the bridge never removes a book from the reader. A book you put in the collection later is sent on the next sync, even if it is older than the reader's last sync; the bridge notes when it first saw each book in the collection, in `collection-seen.json` beside the reading state.

This code is under the MIT licence like the rest of kobobridge, so you are free to read it, change it or remove the key check. The key is how you pay for this feature if you want it to work as shipped; it is not a permission you need.

Sales are not open yet. To hear when they are, subscribe to [issue 7](https://github.com/Rezarys/kobobridge/issues/7); the opening will be announced there.

Once you have a key:

```
export KOBOBRIDGE_COLLECTION_ID=the-collection-id
export KOBOBRIDGE_LICENSE_KEY=the-key-from-your-receipt
kobobridge check
```

The collection id is the last part of the address when the collection is open in the Audiobookshelf web page, after `/collection/`.

Once sales are open, a start that has both settings asks the Lemon Squeezy license server (`api.lemonsqueezy.com`) twice: one request to check the key and one to activate it. Those requests carry the key and the name `kobobridge`, and nothing about your library. When that succeeds, the result is kept in `license.json` beside the reading state, and later starts read that file without any network call; until it succeeds, each start asks again. The key itself is not stored, only a hash of it. `kobobridge check` and `kobobridge run` both make this check, so running one on your machine and the other in a container uses two activations. If the key is missing, refused or the license server cannot be reached, the bridge says why in its log and syncs the whole library as before. If the collection id is wrong, Audiobookshelf answers with an error and the sync fails with that error; there is no fallback to the whole library in that case.

In this version no key unlocks the collection yet, because the product is not on sale, and no request is sent to the license server: every key is refused before anything is contacted. The code that reads the collection and checks the key is covered by tests, with a stand in for the license server; it has not been run against the real one.

## Docker

There is no published image yet. The `Dockerfile` in this repository builds one:

```
docker build -t kobobridge .
docker run -d --name kobobridge -p 8484:8484 \
  -e KOBOBRIDGE_ABS_URL=http://192.168.1.10:13378 \
  -e KOBOBRIDGE_ABS_TOKEN=paste-your-token-here \
  -e KOBOBRIDGE_DEVICE_TOKEN=pick-a-long-random-string \
  -v kobobridge-data:/data \
  kobobridge
```

## Settings

Required:

- `KOBOBRIDGE_ABS_URL`: address of your Audiobookshelf server.
- `KOBOBRIDGE_ABS_TOKEN`: Audiobookshelf API token.

Optional:

- `KOBOBRIDGE_DEVICE_TOKEN`: pin the reader token instead of generating one per run.
- `KOBOBRIDGE_PUBLIC_URL`: the address the reader sees, when behind a reverse proxy.
- `KOBOBRIDGE_LIBRARY_ID`: sync one library instead of every book library.
- `KOBOBRIDGE_COLLECTION_ID` and `KOBOBRIDGE_LICENSE_KEY`: sync one collection, with a paid key. See "Syncing one collection".
- `KOBOBRIDGE_PORT`: port to bind, default 8484.
- `KOBOBRIDGE_TIMEOUT`: seconds to wait on Audiobookshelf, default 30.
- `KOBOBRIDGE_LOG_LEVEL`: `debug`, `info`, `warning` or `error`, default `info`.

## Logs

The bridge writes one line per call the reader makes, with the status, the size and how long it took:

```
2026-09-16T00:17:41 INFO    GET /kobo/<token>/v1/library/sync -> 200 48213 bytes in 1840 ms

2026-09-16T00:17:41 INFO    sync: 100 of 2,000 books sent, more to come

2026-09-16T00:17:43 INFO    GET /kobo/<token>/6f3d3205.../355/530/85/false/image.jpg -> 200 18244 bytes in 96 ms
```

The reader token is replaced with `<token>`, so a log can be pasted into a bug report as it is.

`--log-level debug`, or `KOBOBRIDGE_LOG_LEVEL=debug`, adds every call the bridge makes to Audiobookshelf. That is the level to use when a cover or a download does not arrive, because it shows whether the reader asked at all and what Audiobookshelf answered:

```
docker run ... -e KOBOBRIDGE_LOG_LEVEL=debug kobobridge
docker logs -f kobobridge
```

A cover that failed is logged with the book's name. A cover that was never asked for leaves no line at all, which is a different problem from one that was asked for and failed.

## Security

The reader token is the only thing standing between the internet and your library, so:

- Keep the bridge on your home network, or behind a VPN such as Tailscale or Wireguard.
- If you do expose it, put it behind a reverse proxy with TLS and set `KOBOBRIDGE_PUBLIC_URL`. The reader sends the token in the URL, which means it lands in access logs.
- Pin `KOBOBRIDGE_DEVICE_TOKEN` to something long and random.

## Status

Version 0.2.0. The shapes of the sync protocol were learned by reading the long standing [Calibre-Web](https://github.com/janeczku/calibre-web) implementation, which has served this protocol since 2019. Reading only: Calibre-Web is under the GPL, this project is under the MIT licence, and no code was copied from it. The Audiobookshelf side and the batching logic are covered by tests.

The sync protocol has been exercised over HTTP against a real Audiobookshelf holding 1,374 books, by a reader of this repository rather than by the author: initialization, auth, paged sync to completion, downloads and covers.

It has also been run against a physical device, and not by the author, who owns none. One reader ran a complete sync of a three book library on a Libra Colour on firmware 4.46.23836 against version 0.1.4 and posted the trace in issue 6: fifty five requests, none of them a failure, and three files delivered matching their advertised sizes. Another reader reports the opposite on their own device, in issue 3: the books appear on the shelf and the files never arrive. That reader has a Clara B&W on the same firmware, 4.46.23836, so the firmware alone does not explain the difference; which version of kobobridge they ran was never stated. So one model and one firmware version are known to work for a small library, nothing more than that is, and a report of either kind is the single most useful thing you can send.

## Development

```
git clone https://github.com/Rezarys/kobobridge
cd kobobridge
pip install -e ".[dev]"
pytest
```

## License

MIT. Younes Z., built with AI assistance, reviewed and tested by me.
