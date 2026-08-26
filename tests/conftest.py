"""Shared fixtures. One seeded world is shared by the composer and the mocks so
generated claims and the booking-API verifier agree (the whole point of P2).
"""
from __future__ import annotations

import pytest

from generator.config import GenConfig
from generator.scenarios import ScenarioComposer
from generator.world import World, WorldBuilder

SEED = 42


@pytest.fixture(scope="session")
def world() -> World:
    return WorldBuilder(SEED).build()


@pytest.fixture(scope="session")
def scenarios(world: World):
    return ScenarioComposer(world).all()


@pytest.fixture(scope="session")
def cfg() -> GenConfig:
    return GenConfig(seed=SEED, n_customers=300)
