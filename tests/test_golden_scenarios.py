"""Every golden scenario drives its pinned expected outcome through the reference
pipeline. This is the coverage-complete acceptance layer (POA/16 §5, §13).
"""
from __future__ import annotations

import pytest

from tests.runner import run_scenario


def _ids(scenarios):
    return [s.scenario_id for s in scenarios]


def test_all_branches_have_a_scenario(scenarios):
    # sanity: we have the 7 distinct-branch golden scenarios
    assert len(scenarios) == 7
    assert len(set(_ids(scenarios))) == 7


@pytest.mark.parametrize("idx", range(7))
def test_scenario_matches_expected(scenarios, world, idx):
    sc = scenarios[idx]
    exp = sc.expected
    res = run_scenario(sc, world)

    assert res.fired == exp.fired, f"{sc.scenario_id}: fired"
    if not exp.fired:
        assert res.suppressed_reason == exp.suppressed_reason
        return

    assert res.message_kind == exp.message_kind, f"{sc.scenario_id}: message_kind"
    assert res.terminal_state == exp.terminal_state, f"{sc.scenario_id}: terminal_state"

    # the core trust guarantee: nothing forbidden survives in the delivered text
    for forbidden in exp.delivered_excludes:
        assert forbidden not in res.delivered, (
            f"{sc.scenario_id}: forbidden token {forbidden!r} leaked into delivered message"
        )
