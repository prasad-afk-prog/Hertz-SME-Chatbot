"""Seeded RNG helpers (design principle P4: deterministic & seeded).

A master seed deterministically derives independent per-namespace streams, so
regenerating any single layer/scenario is stable and reproducible in isolation.
"""
from __future__ import annotations

import hashlib
import math
import random
import uuid
from typing import Sequence, TypeVar

T = TypeVar("T")

# Fixed namespace for deterministic ids (design principle P4). Keep this stable —
# changing it would renumber every generated event id.
_ID_NAMESPACE = uuid.UUID("6f1a2b3c-0000-5000-8000-000000000001")


def sub_rng(master_seed: int, namespace: str) -> random.Random:
    digest = hashlib.sha256(f"{master_seed}:{namespace}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def stable_uuid(*parts: object) -> str:
    """A deterministic UUIDv5 built from stable parts, so generated ids (e.g.
    event ids) are byte-reproducible across regens instead of churning like
    uuid4. Callers must pass fields that uniquely identify the record."""
    return str(uuid.uuid5(_ID_NAMESPACE, "|".join(str(p) for p in parts)))


def weighted_choice(rng: random.Random, weights: dict[T, float]) -> T:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm — stdlib only, no numpy dependency."""
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= l:
            return k - 1


def lognormal(rng: random.Random, mu: float, sigma: float) -> float:
    return math.exp(rng.gauss(mu, sigma))


def sample(rng: random.Random, seq: Sequence[T]) -> T:
    return seq[rng.randrange(len(seq))]
