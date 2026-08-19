"""Ticket 101 golden gate: all 42 V2 fixtures through the production engine.

Each fixture is loaded from eval/v2/golden/, the production bundle is built
from given.inputs + given.clock.as_of via houseaccount.scoring.v2, scored,
canonicalized per eval/v2/provenance.md, and deep-compared against
expect.exact. Written test-first: these fail (NotImplementedError) until the
V2 engine lands.
"""

from __future__ import annotations

import pytest

from eval.v2.run_golden import (
    canonicalize_envelope,
    load_fixtures,
    run_fixture,
)

FIXTURES = load_fixtures()


def test_all_42_fixtures_present():
    """Infrastructure: the golden suite is complete, ids G-001..G-042."""
    assert [f["id"] for f in FIXTURES] == [f"G-{n:03d}" for n in range(1, 43)]


@pytest.mark.parametrize(
    "fixture", FIXTURES, ids=[f["id"] for f in FIXTURES]
)
def test_golden_fixture(fixture):
    expected = canonicalize_envelope(fixture["expect"]["exact"])
    actual = run_fixture(fixture)
    assert actual["result"] == expected["result"]
    assert actual["state_changes"] == [] == expected["state_changes"]
    assert actual["emitted_events"] == [] == expected["emitted_events"]
    assert actual["external_calls"] == [] == expected["external_calls"]
