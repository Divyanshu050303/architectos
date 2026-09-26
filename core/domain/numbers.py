"""The exact decimal context the deterministic engines calculate in (cost, reliability): 34
significant digits, half-even rounding. Results are then kept at each engine's stated precision."""

import decimal
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def arithmetic() -> Iterator[None]:
    with decimal.localcontext() as context:
        context.prec = 34
        context.rounding = decimal.ROUND_HALF_EVEN
        yield
