"""Municipal rental-registration seam (PRD R11.3).

Ramsey's rental register is obtainable only by OPRA request, on the
municipality's timetable, and may never arrive. Rather than block the absentee
modifier on a document that does not exist, the system ships the *seam*: a
protocol, a Null implementation that answers False for every door and says why,
and a fixture implementation that the golden fixtures and the eval harness seed
directly. Swapping in a real list later is a constructor change and nothing else.

The narrowness of the interface is the privacy design, not an accident of
scope. The score engine consumes exactly one bit — `rental_registration_match`,
worth -15 — so a provider takes a PAMS_PIN and returns a `bool`. It never
returns, stores, or infers a name, a tenancy, a mailing address, or anything
else about whoever lives at an address (R11.1, R11.3). A richer return type is
the thing that would make this module dangerous, so there isn't one, and the
providers keep no public surface beyond the two protocol methods.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

#: Why the real list is absent (PRD R11.3).
DECLINATION_REASON = "municipal rental registration not obtained via OPRA"


@runtime_checkable
class RentalRegistrationProvider(Protocol):
    """A PAMS_PIN in, a bool out. Nothing else about a household, ever."""

    def is_registered_rental(self, pams_pin: str) -> bool: ...

    def declination_reason(self) -> str | None: ...


class NullRentalProvider:
    """The shipped default: the list was never obtained, so nothing matches.

    False here is "not known to be a rental", never "known not to be one" — the
    declination reason is what carries that distinction to the UI, and it is why
    a no-op provider still has something to say.
    """

    def is_registered_rental(self, pams_pin: str) -> bool:
        return False

    def declination_reason(self) -> str | None:
        return DECLINATION_REASON


class FixtureRentalProvider:
    """Seeded from a set of PINs, for fixtures and eval.

    The seeded PINs live in a private set: a public attribute holding them would
    be an occupancy list hanging off the object, which is exactly the shape this
    seam exists to avoid.
    """

    def __init__(self, pins: Iterable[str]) -> None:
        self._pins = frozenset(str(pin).strip() for pin in pins if str(pin).strip())

    def is_registered_rental(self, pams_pin: str) -> bool:
        return str(pams_pin).strip() in self._pins

    def declination_reason(self) -> str | None:
        """None: this provider's answers are complete for the doors it covers."""
        return None
