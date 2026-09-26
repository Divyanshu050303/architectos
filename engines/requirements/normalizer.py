"""Deterministic normalization of requirement *text*: find the quantities in a sentence and turn
each into a typed bound (operator, exact value or range, explicit unit, percentile).

Pure: no I/O, no model. The input is never rewritten; every result carries the character span it
came from. Nothing is guessed: a spelling is either in the lexicon below or reported as a problem,
and every conversion that involves an interpretation (a year as 365 days, "$" as US dollars) is
recorded as an ``Interpretation`` for the assumption engine to surface.

It builds on the domain normalizer (``core.domain.requirements.normalization``) for unit symbols,
so structured input and free text end in the same canonical units.

    "at least 2,000 requests/sec"        >=      2000 requests/second
    "p95 latency below 300ms"            <       300 ms (percentile 95 reported alongside)
    "between 10GB and 20GB"              between 10 and 20 GB
    "100K daily active users"            (none)  100000 users, qualifier "daily active"
    "four nines"                         (none)  99.99 %
    "retain data for 7 years"            (none)  2555 d  + interpretation year_as_365_days
    "$500 per month"                     (none)  500 USD/month + interpretation dollar_as_usd

An operator the text does not state is ``None``: the classifier supplies the metric's natural
reading (at least N requests, at most N ms) and records that it did.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from core.domain.requirements.errors import InvalidRequirement
from core.domain.requirements.normalization import normalize_unit
from core.domain.requirements.value_objects import (
    MAX_DECIMAL_PLACES,
    MAX_MAGNITUDE,
    Operator,
    Unit,
    decimal_places,
    decimal_to_str,
    unit_for,
)

# --- results ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interpretation:
    """A conversion that assumed something the text did not say."""

    code: str  # e.g. "year_as_365_days"
    detail: str  # human-readable


@dataclass(frozen=True, slots=True)
class Quantity:
    """A number with its unit, exactly as found at ``[start, end)`` of the text."""

    value: Decimal
    unit: Unit | None  # None: a bare number, or one followed by a word that is not a unit
    start: int
    end: int
    noun: str | None = None  # the word after a unit-less number ("2000 connections")
    qualifier: str | None = None  # "daily active", "monthly active", "concurrent", "registered"
    interpretations: tuple[Interpretation, ...] = ()


@dataclass(frozen=True, slots=True)
class Bound:
    """One constraint-shaped reading of a quantity (or a range of two) in its context."""

    operator: Operator | None  # None: not stated in the text
    start: int
    end: int
    quantity: Quantity | None = None
    minimum: Quantity | None = None  # ranges
    maximum: Quantity | None = None
    interpretations: tuple[Interpretation, ...] = ()

    @property
    def is_range(self) -> bool:
        return self.minimum is not None

    @property
    def unit(self) -> Unit | None:
        found = self.quantity or self.maximum or self.minimum
        return found.unit if found is not None else None

    @property
    def qualifier(self) -> str | None:
        quantities = [q for q in (self.quantity, self.minimum, self.maximum) if q is not None]
        return next((q.qualifier for q in quantities if q.qualifier), None)

    @property
    def noun(self) -> str | None:
        return self.quantity.noun if self.quantity is not None else None

    def structured_data(
        self, metric: str, operator: Operator, percentile: Decimal | None = None
    ) -> dict[str, object]:
        """The domain's structured-data shape; ``operator`` applies when the text stated none."""
        unit = self.unit
        if unit is None:
            raise ValueError("a bound without a unit has no structured form")
        data: dict[str, object] = {"metric": metric, "unit": unit.symbol}
        if self.minimum is not None and self.maximum is not None:
            data |= {
                "operator": Operator.BETWEEN.value,
                "min": decimal_to_str(self.minimum.value),
                "max": decimal_to_str(self.maximum.value),
            }
        elif self.quantity is not None:
            data |= {
                "operator": (self.operator or operator).value,
                "value": decimal_to_str(self.quantity.value),
            }
        if percentile is not None:
            data["percentile"] = decimal_to_str(percentile)
        return data


@dataclass(frozen=True, slots=True)
class Problem:
    """A quantity-like piece of text that cannot be normalized safely."""

    reason: str  # ambiguous_unit, unsupported_unit, negative_value, out_of_range, too_precise
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class Percentile:
    value: Decimal
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class NormalizedText:
    bounds: tuple[Bound, ...] = ()
    problems: tuple[Problem, ...] = ()
    percentiles: tuple[Percentile, ...] = ()


# --- lexicon ---------------------------------------------------------------------------------------

_NUMBER = r"(?P<sign>[-\u2212])?(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# "2k", "1.5M", "2bn" (attached) or "2 thousand", "10 million", "2 billion" (words).
_MAGNITUDE = r"(?:(?P<suffix>k|K|M|bn)(?![A-Za-z])|\s+(?P<word>thousand|million|billion)\b)?"
_MAGNITUDES = {
    "k": 10**3,
    "K": 10**3,
    "M": 10**6,
    "bn": 10**9,
    "thousand": 10**3,
    "million": 10**6,
    "billion": 10**9,
}
# Not part of a word ("eu-west-1", "v2", "x3.5") nor of a larger number.
_QUANTITY = re.compile(rf"(?<![\w.,])(?<![A-Za-z]-){_NUMBER}{_MAGNITUDE}")

_PER = r"\s*(?:/|per\s+|a\s+|an\s+)\s*"
_SECOND = r"(?:seconds?|secs?|s)\b"
_MINUTE = r"(?:minutes?|mins?)\b"
_HOUR = r"(?:hours?|hrs?|h)\b"
_DAY = r"(?:days?|d)\b"
_USERS = (
    r"(?:(?P<qual>daily\s+active|monthly\s+active|daily|monthly|concurrent|simultaneous|registered|active)\s+)?"
    r"users?\b"
)

# Case-insensitive units, longest first: (pattern after the number, unit symbol or marker).
_UNITS_ANY_CASE: list[tuple[str, str]] = [
    (rf"(?:requests?|reqs?){_PER}{_SECOND}", "requests/second"),
    (rf"(?:requests?|reqs?){_PER}{_MINUTE}", "requests/minute"),
    (rf"(?:requests?|reqs?){_PER}{_HOUR}", "requests/hour"),
    (rf"(?:requests?|reqs?){_PER}{_DAY}", "requests/day"),
    (rf"orders?{_PER}{_SECOND}", "orders/second"),
    (rf"orders?{_PER}{_MINUTE}", "orders/minute"),
    (rf"orders?{_PER}{_HOUR}", "orders/hour"),
    (rf"orders?{_PER}{_DAY}", "orders/day"),
    (r"rps\b", "requests/second"),
    (r"rpm\b", "requests/minute"),
    (_USERS, "users"),
    (r"dau\b", "users:daily active"),
    (r"mau\b", "users:monthly active"),
    (r"(?:ms|msecs?|milliseconds?)\b", "ms"),
    (r"(?:seconds?|secs?|s)\b", "s"),
    (r"(?:minutes?|mins?)\b", "min"),
    (r"(?:hours?|hrs?|h)\b", "h"),
    (r"(?:days?|d)\b", "d"),
    (r"weeks?\b", "week"),
    (r"months?\b", "month"),
    (r"(?:years?|yrs?)\b", "year"),
    (r"(?:%|percent\b|pct\b)", "%"),
    (r"bytes?\b", "B"),
]
# Case-sensitive: the case of a data size carries meaning (MB is not Mb), and "m" is ambiguous.
_UNITS_EXACT_CASE: list[tuple[str, str]] = [
    (r"(?P<data>[KMGTP]B)\b", "data"),
    (r"(?P<code>[A-Z]{3})" + _PER + r"month\b", "money"),
    (r"[KMGTP]iB\b", "unsupported"),  # binary sizes are not units (yet)
    (r"[kmgtp]b\b", "ambiguous"),  # bits or bytes?
    (r"m\b", "ambiguous"),  # milli-, million or minutes?
]
_COMPILED_UNITS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\s*(?:" + pattern + ")", re.IGNORECASE), marker) for pattern, marker in _UNITS_ANY_CASE
] + [(re.compile(r"\s*(?:" + pattern + ")"), marker) for pattern, marker in _UNITS_EXACT_CASE]
# The ambiguous and case-sensitive patterns must win over their case-insensitive look-alikes.
_COMPILED_UNITS = _COMPILED_UNITS[len(_UNITS_ANY_CASE) :] + _COMPILED_UNITS[: len(_UNITS_ANY_CASE)]

# Currency symbols before the number: "$500 per month", "€20/month".
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
_CURRENCY_CODE = re.compile(r"(?P<code>[A-Z]{3})\s$")
_MONEY_SUFFIX = re.compile(r"\s*(?:" + _PER + r"month\b|monthly\b)", re.IGNORECASE)

_NINES = re.compile(r"\b(?P<count>two|three|four|five|six)\s+nines\b", re.IGNORECASE)
_NINES_VALUE = {"two": "99", "three": "99.9", "four": "99.99", "five": "99.999", "six": "99.9999"}
_PERCENTILE = re.compile(
    r"\bp(?P<p>\d{1,2}(?:\.\d+)?)\b"
    r"|\b(?P<ord>\d{1,2}(?:\.\d+)?)(?:st|nd|rd|th)[\s-]*percentile\b"
    r"|\b(?P<median>median)\b",
    re.IGNORECASE,
)
_ORDINAL_PERCENTILE = re.compile(r"(?:st|nd|rd|th)[\s-]*percentile\b", re.IGNORECASE)
_RANGE_OPENER = re.compile(r"(?:between|from)\s*$", re.IGNORECASE)
_RANGE_JOINER = re.compile(r"\s*(?:and|to|-|\u2013)\s*", re.IGNORECASE)
_NOUN = re.compile(r"\s+(?P<noun>[A-Za-z][A-Za-z_-]*)")

# Operator phrases right before the number ("at least 2000") or right after the unit ("2000 or more").
_BEFORE: list[tuple[re.Pattern[str], Operator]] = [
    (re.compile(r"(?:" + phrase + r")\s*$", re.IGNORECASE), operator)
    for phrase, operator in [
        (
            r"at\s+least|no\s+(?:less|fewer)\s+than|not\s+less\s+than|(?:a\s+)?minimum\s+of|min\.?|>=|≥",
            Operator.AT_LEAST,
        ),
        (
            r"at\s+most|no\s+more\s+than|not\s+more\s+than|(?:a\s+)?maximum\s+of|max\.?|up\s+to|within|<=|≤",
            Operator.AT_MOST,
        ),
        (r"less\s+than|fewer\s+than|lower\s+than|under|below|faster\s+than|<", Operator.LESS_THAN),
        (
            r"more\s+than|greater\s+than|higher\s+than|over|above|exceeding|in\s+excess\s+of|>",
            Operator.MORE_THAN,
        ),
        (r"exactly|equal\s+to|=", Operator.EQUALS),
    ]
]
_AFTER: list[tuple[re.Pattern[str], Operator]] = [
    (re.compile(r"\s*(?:" + phrase + r")\b", re.IGNORECASE), operator)
    for phrase, operator in [
        (r"or\s+(?:more|higher|greater)|and\s+(?:above|up)|at\s+minimum", Operator.AT_LEAST),
        (r"or\s+(?:less|fewer|lower|below)|at\s+(?:most|maximum)", Operator.AT_MOST),
    ]
]
_WINDOW = 40  # characters before a number searched for an operator phrase

YEAR_AS_365_DAYS = Interpretation("year_as_365_days", "A year was read as 365 days.")
MONTH_AS_30_DAYS = Interpretation("month_as_30_days", "A month was read as 30 days.")
DOLLAR_AS_USD = Interpretation("dollar_as_usd", "“$” was read as US dollars (USD).")
UNIT_SHARED_IN_RANGE = Interpretation(
    "unit_shared_in_range", "The range's lower value has no unit; it was read in the upper value's unit."
)


# --- scanning --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _UnitMatch:
    marker: str
    end: int
    qualifier: str | None = None
    code: str | None = None


def _unit_after(text: str, position: int) -> _UnitMatch | None:
    for pattern, marker in _COMPILED_UNITS:
        match = pattern.match(text, position)
        if match is None:
            continue
        groups = match.groupdict()
        if marker == "users":
            qualifier = " ".join(groups["qual"].lower().split()) if groups.get("qual") else None
            return _UnitMatch("users", match.end(), qualifier)
        if marker.startswith("users:"):
            return _UnitMatch("users", match.end(), marker.split(":")[1])
        if marker == "data":
            return _UnitMatch(groups["data"], match.end())
        if marker == "money":
            return _UnitMatch("money", match.end(), code=groups["code"])
        return _UnitMatch(marker, match.end())
    return None


def _read_quantity(text: str, match: re.Match[str]) -> Quantity | Problem:
    start = match.start()
    value = Decimal(match.group("number").replace(",", "")) * _MAGNITUDES.get(
        match.group("suffix") or match.group("word") or "", 1
    )
    if match.group("sign"):
        return Problem("negative_value", start, match.end(), match.group(0))
    currency = _currency_before(text, start)
    if currency is not None:
        return _money(text, match, value, currency)

    found = _unit_after(text, match.end())
    if found is None:
        noun = _NOUN.match(text, match.end())
        return _checked(
            value, None, start, match.end(), text, noun=noun.group("noun").lower() if noun else None
        )
    if found.marker in {"ambiguous", "unsupported"}:
        return Problem(f"{found.marker}_unit", start, found.end, text[start : found.end])
    symbol, value, interpretations = _symbol(found, value)
    try:
        unit = unit_for(symbol)
    except InvalidRequirement:
        return Problem("unsupported_unit", start, found.end, text[start : found.end])
    return _checked(
        value, unit, start, found.end, text, qualifier=found.qualifier, interpretations=interpretations
    )


def _symbol(found: _UnitMatch, value: Decimal) -> tuple[str, Decimal, tuple[Interpretation, ...]]:
    """The unit symbol, the value in it, and any interpretation the conversion needed."""
    match found.marker:
        case "money":
            return normalize_unit(f"{found.code}/month"), value, ()
        case "week":
            return "d", value * 7, ()
        case "month":
            return "d", value * 30, (MONTH_AS_30_DAYS,)
        case "year":
            return "d", value * 365, (YEAR_AS_365_DAYS,)
        case _:
            return found.marker, value, ()


def _currency_before(text: str, start: int) -> tuple[str, int] | None:
    """A currency symbol ("$500") or code ("EUR 500") right before the number: (code, its start)."""
    if start > 0 and text[start - 1] in _CURRENCY_SYMBOLS:
        return text[start - 1], start - 1
    code = _CURRENCY_CODE.search(text, max(0, start - 4), start)
    return (code.group("code"), code.start()) if code else None


def _money(text: str, match: re.Match[str], value: Decimal, currency: tuple[str, int]) -> Quantity | Problem:
    marker, start = currency
    suffix = _MONEY_SUFFIX.match(text, match.end())
    if suffix is None:  # "$500" alone: a budget per what?
        return Problem("unsupported_unit", start, match.end(), text[start : match.end()])
    code = _CURRENCY_SYMBOLS.get(marker, marker)
    interpretations = (DOLLAR_AS_USD,) if marker == "$" else ()
    return _checked(
        value, unit_for(f"{code}/month"), start, suffix.end(), text, interpretations=interpretations
    )


def _checked(
    value: Decimal,
    unit: Unit | None,
    start: int,
    end: int,
    text: str,
    *,
    noun: str | None = None,
    qualifier: str | None = None,
    interpretations: tuple[Interpretation, ...] = (),
) -> Quantity | Problem:
    if abs(value) >= MAX_MAGNITUDE:
        return Problem("out_of_range", start, end, text[start:end])
    if decimal_places(value) > MAX_DECIMAL_PLACES:
        return Problem("too_precise", start, end, text[start:end])
    return Quantity(value.normalize() + 0, unit, start, end, noun, qualifier, interpretations)


def _operator_before(text: str, start: int) -> tuple[Operator | None, int]:
    window_start = max(0, start - _WINDOW)
    window = text[window_start:start]
    for pattern, operator in _BEFORE:
        match = pattern.search(window)
        if match:
            return operator, window_start + match.start()
    return None, start


def _operator_after(text: str, end: int) -> tuple[Operator | None, int]:
    for pattern, operator in _AFTER:
        match = pattern.match(text, end)
        if match:
            return operator, match.end()
    return None, end


def _inside_percentile(text: str, match: re.Match[str]) -> bool:
    """The 95 of "p95" or of "95th percentile" is not a quantity."""
    start = match.start()
    if start > 0 and text[start - 1] in "pP":
        return True
    return _ORDINAL_PERCENTILE.match(text, match.end()) is not None


def _percentile(match: re.Match[str]) -> Percentile:
    value = Decimal(50) if match.group("median") else Decimal(match.group("p") or match.group("ord"))
    return Percentile(value, match.start(), match.end())


# --- entry point -----------------------------------------------------------------------------------


def normalize(text: str) -> NormalizedText:
    """Every quantity in ``text`` as a bound (with its operator, if stated), the quantity-like
    pieces that could not be normalized, and the percentiles mentioned (the classifier attaches a
    percentile to the latency it qualifies)."""
    quantities: list[Quantity] = []
    problems: list[Problem] = []
    for match in _QUANTITY.finditer(text):
        if _inside_percentile(text, match):
            continue
        read = _read_quantity(text, match)
        if isinstance(read, Problem):
            problems.append(read)
        else:
            quantities.append(read)
    for match in _NINES.finditer(text):
        nines = Decimal(_NINES_VALUE[match.group("count").lower()])
        quantities.append(Quantity(nines, unit_for("%"), match.start(), match.end()))
    quantities.sort(key=lambda q: q.start)
    return NormalizedText(
        bounds=tuple(_bounds(text, quantities)),
        problems=tuple(problems),
        percentiles=tuple(_percentile(m) for m in _PERCENTILE.finditer(text)),
    )


def _bounds(text: str, quantities: list[Quantity]) -> list[Bound]:
    """Pairs quantities joined as a range ("between 10GB and 20GB", "from 1k to 5k rps", "10-20
    GB"; a unit given once applies to both ends) and reads the operator phrase of the others."""
    bounds: list[Bound] = []
    index = 0
    while index < len(quantities):
        first = quantities[index]
        second = quantities[index + 1] if index + 1 < len(quantities) else None
        if second is not None and _joined_as_range(text, first, second):
            opener = _RANGE_OPENER.search(text, max(0, first.start - 12), first.start)
            shared = first.unit is None  # "between 10 and 20 GB": the 10 is read in GB
            low = Quantity(first.value, second.unit, first.start, first.end) if shared else first
            shared_note = (UNIT_SHARED_IN_RANGE,) if shared else ()
            bounds.append(
                Bound(
                    Operator.BETWEEN,
                    opener.start() if opener else first.start,
                    second.end,
                    minimum=low,
                    maximum=second,
                    interpretations=low.interpretations + second.interpretations + shared_note,
                )
            )
            index += 2
            continue
        operator, start = _operator_before(text, first.start)
        after, end = _operator_after(text, first.end)
        bounds.append(
            Bound(operator or after, start, end, quantity=first, interpretations=first.interpretations)
        )
        index += 1
    return bounds


def _joined_as_range(text: str, first: Quantity, second: Quantity) -> bool:
    joiner = _RANGE_JOINER.fullmatch(text, first.end, second.start)
    if joiner is None or second.unit is None or first.unit not in {None, second.unit}:
        return False
    opened = _RANGE_OPENER.search(text, max(0, first.start - 12), first.start) is not None
    return opened or joiner.group(0).strip() in {"-", "\u2013", "to"}
