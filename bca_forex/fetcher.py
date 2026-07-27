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


def fetch(url: str = URL, timeout: int = 40, retries: int = 3, backoff: float = 5.0) -> str:
    """GET the page, retrying transient failures.

    Retries matter more here than in the app: this runs unattended on a mini PC
    whose network may come and go, and a slot missed for a blip becomes a
    permanent hole in the history — nothing backfills it.
    """
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                return _decode(resp.read(), resp.headers.get("Content-Encoding"))
        except Exception as e:  # network, DNS, TLS, timeout, HTTP
            last = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise FetchError(f"could not fetch {url} after {retries} attempts: {last}")
