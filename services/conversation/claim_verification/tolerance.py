"""Price tolerance policy (M10 §5.4, §3.2) — POA/10 §10.2.

§10.2 ("exact match vs rounded/'from'") is a **product** decision that is still
open. Rather than leave a bare `Decimal` and call the task done, this module
implements the plausible policies as selectable modes with one documented
default, so answering §10.2 becomes a config change rather than a code change.

The modes exist because "is £42.21 the same as £42?" has genuinely different
right answers depending on how the price was phrased:

| Mode | Passes when | Use when |
|------|-------------|----------|
| `exact` | quoted == actual | strictest; audit or regulated contexts |
| `absolute` | \\|quoted - actual\\| <= `absolute` | the default: small rounding drift |
| `percentage` | \\|quoted - actual\\| <= actual × `percentage` | prices vary widely by class |
| `rounded` | quoted == actual rounded to `round_to` | the bot says "about £42" |
| `at_least` | actual <= quoted | the bot says "from £42" — a floor, not a figure |

`at_least` is the one that is easy to get backwards. "From £42/day" is a promise
that nothing costs *less than* £42 is **wrong** — it promises the customer can
get it *for* £42, so the claim holds when the live price is £42 or lower, and
fails when the real price is higher. A customer quoted "from £42" who is charged
£55 was misled; one charged £38 was not.

**The default is deliberately strict.** POA/09 §8's principle — prefer the safe
path when unsure — applies here too: a tolerance that is too tight corrects a
correct price (annoying, harmless); one that is too loose lets a wrong price
reach a customer, which is the failure M10 exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class ToleranceMode(str, Enum):
    exact = "exact"
    absolute = "absolute"
    percentage = "percentage"
    rounded = "rounded"
    at_least = "at_least"


@dataclass(frozen=True)
class TolerancePolicy:
    """How close a quoted price must be to the live price to pass verification."""
    mode: ToleranceMode = ToleranceMode.absolute
    absolute: Decimal = Decimal("0.01")     # a penny of rounding drift
    percentage: Decimal = Decimal("0.00")   # e.g. 0.02 for 2%
    round_to: Decimal = Decimal("1.00")     # for `rounded`: nearest whole unit

    def accepts(self, quoted: Decimal, actual: Decimal) -> bool:
        if self.mode is ToleranceMode.exact:
            return quoted == actual
        if self.mode is ToleranceMode.absolute:
            return abs(actual - quoted) <= self.absolute
        if self.mode is ToleranceMode.percentage:
            return abs(actual - quoted) <= (actual * self.percentage).copy_abs()
        if self.mode is ToleranceMode.rounded:
            return quoted == self._round(actual)
        if self.mode is ToleranceMode.at_least:
            # "from £X" holds while the real price is not ABOVE what we promised.
            return actual <= quoted
        raise ValueError(f"unknown tolerance mode {self.mode!r}")   # pragma: no cover

    def _round(self, value: Decimal) -> Decimal:
        if self.round_to <= 0:                                      # pragma: no cover
            return value
        return (value / self.round_to).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * self.round_to

    def describe(self) -> str:
        """Human-readable, for the audit log — a verification outcome is only
        meaningful alongside the rule that produced it."""
        if self.mode is ToleranceMode.exact:
            return "exact match"
        if self.mode is ToleranceMode.absolute:
            return f"within {self.absolute}"
        if self.mode is ToleranceMode.percentage:
            return f"within {self.percentage * 100}%"
        if self.mode is ToleranceMode.rounded:
            return f"rounded to nearest {self.round_to}"
        return "quoted price is a floor ('from £X')"


#: The documented default until POA/10 §10.2 is answered by product.
DEFAULT_POLICY = TolerancePolicy(mode=ToleranceMode.absolute, absolute=Decimal("0.01"))

#: For drafts phrased "from £X" / "starting at £X". M09's structured claim output
#: is where this would be signalled per-claim once the phrasing is tagged.
FROM_PRICE_POLICY = TolerancePolicy(mode=ToleranceMode.at_least)
