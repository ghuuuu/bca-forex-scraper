"""Turn the BCA kurs page into rates. Standard library only — no dependencies.

The page (https://www.bca.co.id/id/informasi/kurs) is server-rendered and tags
every rate cell with a machine-readable attribute, which is what we key off:

    <td class="rate-column" rate-type="eRate-buy">
        <p>17.980,00</p>
    </td>

Six cells per currency (e-Rate, TT Counter, Bank Notes — each buy and sell), one
table row per currency, the code carried in a <span> in the row's first cell.

Reading the ATTRIBUTE rather than the column position is deliberate: BCA has
reordered and restyled this table before, and a positional parser silently
returns the wrong numbers when it happens — the worst possible failure for a
price feed. An attribute that stops matching gives nothing, loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: BCA's rate-type attribute (lower-cased) -> our field name.
RATE_KEYS: dict[str, str] = {
    "erate-buy": "e_rate_buy",
    "erate-sell": "e_rate_sell",
    "ttcounter-buy": "tt_buy",
    "ttcounter-sell": "tt_sell",
    "banknotes-buy": "bank_notes_buy",
    "banknotes-sell": "bank_notes_sell",
}

FIELDS: tuple[str, ...] = tuple(RATE_KEYS.values())

_CELL_RE = re.compile(
    r"rate-type\s*=\s*\"([^\"]+)\"[^>]*>\s*<p[^>]*>([^<]*)</p>", re.IGNORECASE
)
_CODE_RE = re.compile(r"<span>\s*([A-Z]{3})\s*</span>")
_ROW_SPLIT_RE = re.compile(r"<tr\b", re.IGNORECASE)
_TAGS_RE = re.compile(r"<[^>]+>")
_UPDATED_RE = re.compile(
    r"[Tt]erakhir diperbarui pada\s*([0-9]{1,2}\s+\w+\s+[0-9]{4}\s+[0-9]{1,2}[.:][0-9]{2})\s*WIB"
)


class ParseError(RuntimeError):
    """The page loaded but produced no rates — almost always a layout change."""


@dataclass
class CurrencyRates:
    code: str
    e_rate_buy: float | None = None
    e_rate_sell: float | None = None
    tt_buy: float | None = None
    tt_sell: float | None = None
    bank_notes_buy: float | None = None
    bank_notes_sell: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {f: getattr(self, f) for f in FIELDS}


@dataclass
class Pull:
    """One scrape: every currency on the page, plus what the page said about itself."""

    rates: dict[str, CurrencyRates] = field(default_factory=dict)
    page_updated: str | None = None


def parse_number(text: str) -> float | None:
    """Parse an Indonesian-formatted number: '17.980,00' -> 17980.0.

    Thousands are dots and the decimal is a comma, which is the reverse of the
    English convention — read the wrong way round, 17.980,00 becomes 17.98 and
    a quote is out by a factor of a thousand. Also tolerates the English form
    and a plain machine number, so a future change of format degrades to a
    correct read rather than a silently wrong one.
    """
    t = text.strip().replace("\xa0", "").replace(" ", "")
    if not t:
        return None
    if re.fullmatch(r"\d+(\.\d{1,2})?", t):  # plain: 17980 / 17980.00
        return float(t)
    if re.fullmatch(r"\d{1,3}(\.\d{3})*(,\d+)?", t):  # id-ID: 17.980,00
        return float(t.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?", t):  # en-US: 17,980.00
        return float(t.replace(",", ""))
    return None


def parse_page_updated(html: str) -> str | None:
    """The 'Terakhir diperbarui pada …' stamp BCA prints above the table."""
    m = _UPDATED_RE.search(_TAGS_RE.sub(" ", html))
    return f"{m.group(1)} WIB" if m else None


def parse(html: str) -> Pull:
    """Parse a saved or freshly fetched page. Raises ParseError if nothing matched."""
    pull = Pull(page_updated=parse_page_updated(html))
    for row in _ROW_SPLIT_RE.split(html):
        if "rate-type" not in row:
            continue
        code_m = _CODE_RE.search(row)
        if not code_m:
            continue
        code = code_m.group(1).upper()
        # the page repeats the table for its mobile layout; first row wins
        if code in pull.rates:
            continue
        cur = CurrencyRates(code=code)
        found = False
        for raw_type, raw_value in _CELL_RE.findall(row):
            key = RATE_KEYS.get(raw_type.strip().lower())
            if not key:
                continue
            value = parse_number(raw_value)
            if value is None:
                continue
            setattr(cur, key, value)
            found = True
        if found:
            pull.rates[code] = cur
    if not pull.rates:
        raise ParseError(
            "no rates parsed — the BCA page structure has probably changed. "
            "Re-save the page and check the rate-type attributes in parser.RATE_KEYS."
        )
    return pull
