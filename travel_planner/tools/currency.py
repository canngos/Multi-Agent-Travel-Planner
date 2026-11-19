"""Prices in other currencies: read a price text and convert it to euro.

Wikivoyage and OpenStreetMap write prices in the local currency ("20 zł", "350 CZK"). The cost check and the
guide work in euro, so such prices are converted with the ECB reference rates (Frankfurter API: free, no key,
cached for a week). Without rates (offline, or the API is down) a foreign price stays unknown, as before.
"""
import re

from ._http import get_json
from ._result import ToolError

RATES_URL = "https://api.frankfurter.dev/v1/latest"
RATES_MAX_AGE_S = 7 * 24 * 3600

# Unambiguous symbols and codes only: "kr" (SEK, NOK, DKK, ISK) and bare "$" variants other than USD are left out
# on purpose, and so is "TRY" (an English word).
_CODES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "zł": "PLN", "zl": "PLN", "pln": "PLN",
          "£": "GBP", "gbp": "GBP", "kč": "CZK", "czk": "CZK", "ft": "HUF", "huf": "HUF", "$": "USD", "us$": "USD",
          "usd": "USD", "chf": "CHF", "sek": "SEK", "nok": "NOK", "dkk": "DKK", "ron": "RON", "lei": "RON",
          "bgn": "BGN", "isk": "ISK", "₺": "TRY"}
_SYMBOL = "|".join(sorted((re.escape(c) for c in _CODES), key=len, reverse=True))
_CUR = rf"(?<![a-z])(?:{_SYMBOL})(?![a-z])"
_NUM = r"\d(?:[\d.,   ]*\d)?"
_PRICE = re.compile(rf"(?P<c1>{_CUR})\s*(?P<n1>{_NUM})|(?P<n2>{_NUM})\s*(?P<c2>{_CUR})", re.IGNORECASE)


def parse_number(text: str) -> float:
    """'1.234,56' and '1,234.56' -> 1234.56; '12,50' -> 12.5; '1,234' and '2 500' -> thousands.
    The last separator is the decimal mark unless exactly three digits follow it."""
    s = re.sub(r"[\s  ]", "", text)
    last = re.search(r"[.,](\d+)$", s)
    if last and len(last.group(1)) != 3:
        return float(re.sub(r"[.,]", "", s[:last.start()]) + "." + last.group(1))
    return float(re.sub(r"[.,]", "", s))


def parse_money(text: str, whole: bool = False) -> tuple[float, str] | None:
    """(amount, ISO currency code) of the first price in the text, e.g. '20 zł' -> (20.0, 'PLN').
    whole=True: only when the text is nothing but that one price (not a range or a price list)."""
    s = (text or "").strip()
    m = (_PRICE.fullmatch if whole else _PRICE.search)(s)
    if not m:
        return None
    code = _CODES[(m.group("c1") or m.group("c2")).lower()]
    return parse_number(m.group("n1") or m.group("n2")), code


# The price a price list starts with: "€6 (adults), €3 (seniors)", "Adult 10 zł, conc 8 zł", "Ticket 10 €, ..."
# (not "Tower €3", and not a range: the atomic group stops "€15-30" from matching as "€1").
_LEADING = re.compile(rf"^\s*(?:(?:adults?|tickets?|admission|entry|entrance)\s*:?\s*)?(?>{_PRICE.pattern})"
                      r"(?!\s*[-–]\s*\d)", re.IGNORECASE)


def leading_price(text: str) -> tuple[float, str] | None:
    """(amount, code) of the first price when the text starts with it (or with "Adult"/"Ticket"): the standard
    adult ticket of a price list."""
    m = _LEADING.match(text or "")
    if not m:
        return None
    return parse_number(m.group("n1") or m.group("n2")), _CODES[(m.group("c1") or m.group("c2")).lower()]


def to_eur(amount: float, code: str, rates: dict[str, float] | None) -> float | None:
    if code == "EUR":
        return amount
    if rates and rates.get(code):
        return round(amount / rates[code], 2)
    return None


def is_foreign(text: str) -> bool:
    money = parse_money(text)
    return money is not None and money[1] != "EUR"


async def eur_rates() -> dict[str, float] | None:
    """Units of each currency per euro (ECB reference rates), or None when they cannot be fetched."""
    try:
        data = await get_json(RATES_URL, namespace="frankfurter", params={"base": "EUR"}, max_age_s=RATES_MAX_AGE_S)
    except ToolError:
        return None
    rates = data.get("rates") if isinstance(data, dict) else None
    return {k: float(v) for k, v in rates.items()} if rates else None
