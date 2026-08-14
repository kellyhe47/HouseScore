"""Municipal rental-registration seam. STUB — T005 tests define the behaviour."""

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
    """The shipped default: the list was never obtained, so nothing matches."""

    def is_registered_rental(self, pams_pin: str) -> bool:
        raise NotImplementedError

    def declination_reason(self) -> str | None:
        raise NotImplementedError


class FixtureRentalProvider:
    """Seeded from a set of PINs, for fixtures and eval."""

    def __init__(self, pins: Iterable[str]) -> None:
        raise NotImplementedError

    def is_registered_rental(self, pams_pin: str) -> bool:
        raise NotImplementedError

    def declination_reason(self) -> str | None:
        raise NotImplementedError
