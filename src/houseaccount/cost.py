"""API cost ledger — what a run actually spent, per source and per door.

`make eval` has to print a cost-per-door line (PRD R14), and "cheap enough to
run on a whole town" is a claim the project makes, so it has to be measured
rather than asserted. Sources record what they spend as they spend it; the
harness saves the ledger next to the run's outputs.

Records are aggregated per source, because the operationally useful question is
"which source cost the money", not "which of the 5,671 calls did".

`per_door(0)` returns 0.0 deliberately: an eval run that scored no doors is a
result to report, not a ZeroDivisionError that takes down the harness.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SourceCost:
    """Everything one source spent during a run."""

    units: int = 0
    usd: float = 0.0


class CostLedger:
    """Accumulates spend by source. Cheap to create, safe to save mid-run."""

    def __init__(self, sources: dict[str, SourceCost] | None = None) -> None:
        self._sources: dict[str, SourceCost] = dict(sources or {})

    # --- accumulation -------------------------------------------------------

    def record(self, source: str, units: int, usd: float) -> None:
        """Add one source's spend. `units` is whatever that source bills in
        (images, geocodes, tokens); `usd` is the cost of exactly those units."""
        entry = self._sources.setdefault(source, SourceCost())
        entry.units += int(units)
        entry.usd += float(usd)

    # --- reporting ----------------------------------------------------------

    def total_usd(self) -> float:
        return sum(entry.usd for entry in self._sources.values())

    def per_door(self, doors: int) -> float:
        """Cost amortised over the doors scored. No doors -> 0.0, not a crash."""
        if doors <= 0:
            return 0.0
        return self.total_usd() / doors

    def as_dict(self) -> dict[str, object]:
        """The saved shape, also handy for embedding in a run manifest."""
        return {
            "sources": {name: asdict(entry) for name, entry in sorted(self._sources.items())},
            "total_usd": self.total_usd(),
        }

    # --- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CostLedger":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        sources = {
            name: SourceCost(units=int(entry.get("units", 0)), usd=float(entry.get("usd", 0.0)))
            for name, entry in payload.get("sources", {}).items()
        }
        return cls(sources)
