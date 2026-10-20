"""Fetch the BCA kurs page. Standard library only."""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
import zlib

URL = "https://www.bca.co.id/id/informasi/kurs"

HEADERS = {
    # BCA serves a stripped page to clients it does not recognise as a browser
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
    "Cache-Control": "no-cache",
}


class FetchError(RuntimeError):
    pass


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", "replace")


def table_end(buf: bytes) -> int:
    """Where the rate table ends in a partial page, or -1 if it has not yet.

    All 18 currencies sit in the first <table> on the page, about a third of
    the way into its ~370 KB; everything after is footer, scripts and a second
    copy of the table for the mobile layout. Stopping there and dropping the
    connection cuts a read to ~120 KB. BCA offers nothing lighter: no JSON
    feed for spot rates, no compression, no ETag, no byte ranges.
    """
    first = buf.find(b'rate-type="')
    if first < 0:
        return -1
    end = buf.find(b"</table>", first)
    return end + len(b"</table>") if end >= 0 else -1


def _read_table(resp) -> bytes:
    buf = b""
    while True:
        chunk = resp.read(16384)
        if not chunk:
            return buf  # no table end found: hand over the whole page
        buf += chunk
        end = table_end(buf)
        if end >= 0:
            return buf[:end]


def fetch(
    url: str = URL, timeout: int = 40, retries: int = 3, backoff: float = 5.0,
    whole: bool = False,
) -> str:
    """GET the page, up to the end of the rate table, retrying transient failures.

    Retries matter more here than in the app: this runs unattended on a mini PC
    whose network may come and go, and a slot missed for a blip becomes a
    permanent hole in the history — nothing backfills it.

    `whole` reads the entire page, for saving a test fixture.
    """
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                encoding = resp.headers.get("Content-Encoding")
                # A compressed body cannot be cut short and still decompress.
                raw = resp.read() if whole or encoding else _read_table(resp)
                return _decode(raw, encoding)
        except Exception as e:  # network, DNS, TLS, timeout, HTTP
            last = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise FetchError(f"could not fetch {url} after {retries} attempts: {last}")
