"""Municipal rental-registration seam (T005, PRD R11.3).

The municipal rental register is OPRA-request-only and may never arrive. The
system therefore ships the *seam* plus a documented declination: a typed protocol
that takes a PAMS_PIN and returns a bool, a Null implementation that answers
False for everything and says why, and a fixture implementation for the golden
fixtures and the eval harness.

The privacy shape is the point. Scoring consumes exactly one bit per door
(is this PIN currently a registered rental), so these providers must never return, store or
infer anything else about whoever lives at an address (R11.1, R11.3). The tests
below pin the surface, not just the answers.
"""

import pytest

from houseaccount.sources.rental import (
    FixtureRentalProvider,
    NullRentalProvider,
    RentalRegistrationProvider,
)

#: PAMS_PIN shapes: municipality_block_lot, as MOD-IV writes them.
SEEDED_PIN = "0248_2702_15"
OTHER_SEEDED_PIN = "0248_3801_7"
UNKNOWN_PIN = "0248_1101_3"

#: The only names either provider is allowed to expose publicly.
ALLOWED_PUBLIC_API = {"is_registered_rental", "declination_reason"}


#: Constructed lazily so a broken constructor fails a test, not collection.
PROVIDER_FACTORIES = [
    NullRentalProvider,
    lambda: FixtureRentalProvider([SEEDED_PIN, OTHER_SEEDED_PIN]),
]
PROVIDER_IDS = ["null", "fixture"]


# --- the protocol -----------------------------------------------------------


@pytest.mark.parametrize("make_provider", PROVIDER_FACTORIES, ids=PROVIDER_IDS)
def test_both_providers_satisfy_the_protocol(make_provider):
    assert isinstance(make_provider(), RentalRegistrationProvider)


@pytest.mark.parametrize("make_provider", PROVIDER_FACTORIES, ids=PROVIDER_IDS)
@pytest.mark.parametrize("pin", [SEEDED_PIN, UNKNOWN_PIN, ""], ids=["seeded", "unknown", "empty"])
def test_the_answer_is_always_a_plain_bool(make_provider, pin):
    answer = make_provider().is_registered_rental(pin)
    assert type(answer) is bool, "the engine consumes one bit; nothing else may ride along"


@pytest.mark.parametrize("make_provider", PROVIDER_FACTORIES, ids=PROVIDER_IDS)
def test_nothing_occupant_identifying_is_exposed(make_provider):
    public = {name for name in dir(make_provider()) if not name.startswith("_")}
    assert public <= ALLOWED_PUBLIC_API, (
        "a rental provider exposes a yes/no and a declination reason, nothing else"
    )


@pytest.mark.parametrize("make_provider", PROVIDER_FACTORIES, ids=PROVIDER_IDS)
def test_asking_about_an_unknown_pin_answers_false_rather_than_raising(make_provider):
    assert make_provider().is_registered_rental(UNKNOWN_PIN) is False


# --- the null provider (what actually ships today) --------------------------


@pytest.mark.parametrize(
    "pin", [SEEDED_PIN, OTHER_SEEDED_PIN, UNKNOWN_PIN, ""], ids=["a", "b", "c", "empty"]
)
def test_the_null_provider_matches_nothing_at_all(pin):
    assert NullRentalProvider().is_registered_rental(pin) is False


def test_the_null_provider_reports_a_human_readable_declination():
    reason = NullRentalProvider().declination_reason()
    assert isinstance(reason, str) and reason.strip()
    assert "opra" in reason.lower()
    assert "rental" in reason.lower()


# --- the fixture provider ---------------------------------------------------


@pytest.mark.parametrize(
    "pin,expected",
    [
        (SEEDED_PIN, True),
        (OTHER_SEEDED_PIN, True),
        (UNKNOWN_PIN, False),
        ("", False),
    ],
    ids=["seeded", "also-seeded", "unknown", "empty"],
)
def test_the_fixture_provider_answers_for_seeded_pins_only(pin, expected):
    provider = FixtureRentalProvider([SEEDED_PIN, OTHER_SEEDED_PIN])
    assert provider.is_registered_rental(pin) is expected


def test_the_fixture_provider_has_no_declination_to_report():
    assert FixtureRentalProvider([SEEDED_PIN]).declination_reason() is None


def test_an_empty_fixture_provider_matches_nothing():
    assert FixtureRentalProvider([]).is_registered_rental(SEEDED_PIN) is False


# --- what the score does with the bit ----------------------------------------
#
# V1's engine consumed this bit as a -15 "absentee_likely" modifier; that
# contract is deleted (plan R27/R32). V2 consumes rental evidence through the
# bundle's `rental_registry` block, where a current verified registration is
# -25 and a missing registry is the `rental_data_missing` gap — pinned by the
# locked tests/test_v2_engine.py and tests/test_v2_golden.py, and end-to-end
# (every real door carries the gap) by tests/test_v2_cutover.py. This module
# keeps pinning only the provider seam above.
