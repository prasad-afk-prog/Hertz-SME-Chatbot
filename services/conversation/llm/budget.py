"""Cost & latency budgets (M09 §5.6, §3.4) — POA/09's runaway-cost mitigation.

Three limits, because they fail in different ways:

* **per-session tokens** — one conversation looping forever;
* **per-customer daily tokens** — one account driving disproportionate spend;
* **global daily spend** — the bill, in money rather than tokens.

Exceeding a budget is **not an error to the customer**. `check()` returns a
decision, and M09 turns a refusal into the same safe templated fallback it uses
for an outage. POA/09 §1 is explicit that an error never reaches the customer,
and "you have used too many tokens" is an error.

The store is a protocol. `InMemoryBudgetStore` is Phase 1; Redis (§4) slots in
behind the same three methods once M15 provisions it — the accounting logic does
not move.

Prices are per million tokens and default to Claude Opus 5's published rates.
They are a *config* value, not a constant: they change, and a stale hard-coded
price silently under-reports spend.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Protocol, runtime_checkable

from services.common.resilience import Clock


class BudgetVerdict(str, Enum):
    ok = "ok"
    session_tokens_exceeded = "session_tokens_exceeded"
    customer_tokens_exceeded = "customer_tokens_exceeded"
    global_spend_exceeded = "global_spend_exceeded"


@dataclass
class BudgetPolicy:
    """All limits are per the window named in the field. Zero disables a limit."""
    max_tokens_per_session: int = 20_000
    max_tokens_per_customer_per_day: int = 200_000
    max_spend_per_day: Decimal = Decimal("50.00")

    # $/1M tokens. Config, not constants — published prices change.
    input_price_per_mtok: Decimal = Decimal("5.00")
    output_price_per_mtok: Decimal = Decimal("25.00")

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(prompt_tokens) / million * self.input_price_per_mtok
            + Decimal(completion_tokens) / million * self.output_price_per_mtok
        ).quantize(Decimal("0.000001"))


@runtime_checkable
class BudgetStore(Protocol):
    def add(self, key: str, tokens: int, spend: Decimal, ttl_s: float) -> None: ...
    def tokens(self, key: str) -> int: ...
    def spend(self, key: str) -> Decimal: ...


@dataclass
class InMemoryBudgetStore:
    """Phase-1 store. Entries expire on the injected clock, so a 'daily' window
    is asserted in tests rather than waited for."""
    clock: Clock = time.monotonic
    _tokens: dict[str, tuple[float, int]] = field(default_factory=dict, init=False)
    _spend: dict[str, tuple[float, Decimal]] = field(default_factory=dict, init=False)

    def _live(self, bucket: dict, key: str):
        hit = bucket.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if self.clock() >= expires_at:
            del bucket[key]
            return None
        return value

    def add(self, key: str, tokens: int, spend: Decimal, ttl_s: float) -> None:
        existing_tokens = self._live(self._tokens, key) or 0
        existing_spend = self._live(self._spend, key) or Decimal("0")
        expires_at = self.clock() + ttl_s
        self._tokens[key] = (expires_at, existing_tokens + tokens)
        self._spend[key] = (expires_at, existing_spend + spend)

    def tokens(self, key: str) -> int:
        return self._live(self._tokens, key) or 0

    def spend(self, key: str) -> Decimal:
        return self._live(self._spend, key) or Decimal("0")


DAY_S = 24 * 60 * 60
SESSION_S = 4 * 60 * 60


@dataclass
class BudgetDecision:
    verdict: BudgetVerdict
    session_tokens: int = 0
    customer_tokens: int = 0
    daily_spend: Decimal = Decimal("0")

    @property
    def allowed(self) -> bool:
        return self.verdict is BudgetVerdict.ok


class BudgetGuard:
    """Checks before generating, records after.

    Checking *before* is what makes the limit a limit: recording alone would let
    a single very large request blow straight past the cap.
    """

    def __init__(
        self,
        policy: BudgetPolicy | None = None,
        store: BudgetStore | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self.policy = policy or BudgetPolicy()
        self.store = store or InMemoryBudgetStore(clock=clock)

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def _customer_key(customer_id: str) -> str:
        return f"customer:{customer_id}"

    _GLOBAL_KEY = "global:spend"

    def check(self, session_id: str, customer_id: str) -> BudgetDecision:
        session_tokens = self.store.tokens(self._session_key(session_id))
        customer_tokens = self.store.tokens(self._customer_key(customer_id))
        daily_spend = self.store.spend(self._GLOBAL_KEY)

        p = self.policy
        verdict = BudgetVerdict.ok
        if p.max_tokens_per_session and session_tokens >= p.max_tokens_per_session:
            verdict = BudgetVerdict.session_tokens_exceeded
        elif p.max_tokens_per_customer_per_day and customer_tokens >= p.max_tokens_per_customer_per_day:
            verdict = BudgetVerdict.customer_tokens_exceeded
        elif p.max_spend_per_day and daily_spend >= p.max_spend_per_day:
            verdict = BudgetVerdict.global_spend_exceeded

        return BudgetDecision(
            verdict=verdict,
            session_tokens=session_tokens,
            customer_tokens=customer_tokens,
            daily_spend=daily_spend,
        )

    def record(
        self,
        session_id: str,
        customer_id: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Decimal:
        total = prompt_tokens + completion_tokens
        spend = self.policy.cost(prompt_tokens, completion_tokens)
        self.store.add(self._session_key(session_id), total, spend, SESSION_S)
        self.store.add(self._customer_key(customer_id), total, Decimal("0"), DAY_S)
        self.store.add(self._GLOBAL_KEY, 0, spend, DAY_S)
        return spend
