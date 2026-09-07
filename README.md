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

- **It never writes to your library.** Every call to Audiobookshelf is a GET. Reading progress the reader pushes is kept in a small file next to the bridge, not pushed into Audiobookshelf, so progress does not yet show up in the Audiobookshelf web player. A test asserts this.
- **It does not touch any account on the manufacturer's store.** No store credential, no user key, no device id. The bridge issues its own token and never contacts the store.
- **It does not convert anything.** Only epub and kepub files are offered. Pdf, mobi and azw3 in your library are skipped rather than converted.
- **Audiobooks and podcasts are skipped.** The reader cannot play them.
- **Shelves and collections are not mirrored yet.**
- **No telemetry, no phoning home, no analytics.** Nothing about your library leaves your machine.

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

## Docker

```
docker run -d --name kobobridge -p 8484:8484 \
  -e KOBOBRIDGE_ABS_URL=http://192.168.1.10:13378 \
  -e KOBOBRIDGE_ABS_TOKEN=paste-your-token-here \
  -e KOBOBRIDGE_DEVICE_TOKEN=pick-a-long-random-string \
  -v kobobridge-data:/data \
  ghcr.io/rezarys/kobobridge:latest
```

## Settings

Required:

- `KOBOBRIDGE_ABS_URL`: address of your Audiobookshelf server.
- `KOBOBRIDGE_ABS_TOKEN`: Audiobookshelf API token.

Optional:

- `KOBOBRIDGE_DEVICE_TOKEN`: pin the reader token instead of generating one per run.
- `KOBOBRIDGE_PUBLIC_URL`: the address the reader sees, when behind a reverse proxy.
- `KOBOBRIDGE_LIBRARY_ID`: sync one library instead of every book library.
- `KOBOBRIDGE_PORT`: port to bind, default 8484.
- `KOBOBRIDGE_TIMEOUT`: seconds to wait on Audiobookshelf, default 30.

## Security

The reader token is the only thing standing between the internet and your library, so:

- Keep the bridge on your home network, or behind a VPN such as Tailscale or Wireguard.
- If you do expose it, put it behind a reverse proxy with TLS and set `KOBOBRIDGE_PUBLIC_URL`. The reader sends the token in the URL, which means it lands in access logs.
- Pin `KOBOBRIDGE_DEVICE_TOKEN` to something long and random.

## Status

Version 0.1.0, first release. The shapes of the sync protocol were learned by reading the long standing [Calibre-Web](https://github.com/janeczku/calibre-web) implementation, which has served this protocol since 2019. Reading only: Calibre-Web is under the GPL, this project is under the MIT licence, and no code was copied from it. The Audiobookshelf side and the batching logic are covered by tests.

What has not been exercised yet is a full sync against a physical reader, because the author does not own one. If you try it, an issue saying what happened is the single most useful thing you can send, working or not.

## Development

```
git clone https://github.com/Rezarys/kobobridge
cd kobobridge
pip install -e ".[dev]"
pytest
```

## License

MIT. Younes Z.
