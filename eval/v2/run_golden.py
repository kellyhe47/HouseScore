"""V2 golden adapter (ticket 101) — fixture loading + canonicalization.

Runs all 42 fixtures in eval/v2/golden/ through the production V2 entry point
(``houseaccount.scoring.v2``) and deep-compares against ``expect.exact`` after
canonicalization per eval/v2/provenance.md:

- Floats (mover.strength, mover_lift, pre_rounding, adjustment; and any float
  evidence points) round to 3 dp before comparison.
- result.evidence is projected to {type, points} sorted by type.
- result.data_gaps is projected to {type} sorted by type.
- state_changes / emitted_events / external_calls are exactly [] (pure).

This module is test infrastructure and is fully implemented; the engine it
drives is stubbed until the implementation ticket. ``__main__`` runs every
fixture and reports pass/fail (serves ``make test-golden`` debugging).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

#: Envelope floats that provenance.md rounds to 3 dp before comparison.
_FLOAT_KEYS = ("mover_lift", "pre_rounding", "adjustment")


def load_fixtures() -> list[dict[str, Any]]:
    """All golden fixture documents, sorted by fixture id (G-001..G-042)."""
    fixtures = [
        json.loads(path.read_text()) for path in sorted(GOLDEN_DIR.glob("*.json"))
    ]
    fixtures.sort(key=lambda f: f["id"])
    return fixtures


def parse_as_of(clock: Mapping[str, Any]) -> date:
    """given.clock.as_of (ISO 8601 instant) -> calendar date."""
    raw = clock["as_of"]
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def _round3(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 3)
    return value


def canonicalize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical form of a V2 result envelope per provenance.md."""
    out: dict[str, Any] = dict(result)
    for key in _FLOAT_KEYS:
        if key in out:
            out[key] = _round3(out[key])
    mover = out.get("mover")
    if isinstance(mover, Mapping):
        mover = dict(mover)
        if "strength" in mover:
            mover["strength"] = _round3(mover["strength"])
        out["mover"] = mover
    out["evidence"] = sorted(
        (
            {"type": e["type"], "points": _round3(e["points"])}
            for e in result.get("evidence", ())
        ),
        key=lambda e: e["type"],
    )
    out["data_gaps"] = sorted(
        ({"type": g["type"]} for g in result.get("data_gaps", ())),
        key=lambda g: g["type"],
    )
    return out


def canonicalize_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical form of a full expect.exact-shaped envelope."""
    out = dict(envelope)
    out["result"] = canonicalize_result(envelope["result"])
    for key in ("state_changes", "emitted_events", "external_calls"):
        out[key] = list(envelope.get(key, ()))
    return out


def run_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Drive one fixture through the production V2 entry point.

    Returns the produced envelope (result + empty side-effect lists),
    canonicalized. Scoring is pure, so the side-effect lists are literal.
    """
    from houseaccount.scoring.v2 import V2Bundle, score_door_v2

    given = fixture["given"]
    assert fixture["when"]["operation"] == "score_door"
    bundle = V2Bundle.from_fixture(given)
    result = score_door_v2(bundle, parse_as_of(given["clock"]))
    return canonicalize_envelope(
        {
            "result": result,
            "state_changes": [],
            "emitted_events": [],
            "external_calls": [],
        }
    )


def main() -> int:
    failures = 0
    for fixture in load_fixtures():
        fid = fixture["id"]
        expected = canonicalize_envelope(fixture["expect"]["exact"])
        try:
            actual = run_fixture(fixture)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"FAIL {fid} {fixture['name']}: {type(exc).__name__}: {exc}")
            continue
        if actual == expected:
            print(f"PASS {fid} {fixture['name']}")
        else:
            failures += 1
            print(f"FAIL {fid} {fixture['name']}: envelope mismatch")
    total = len(load_fixtures())
    print(f"{total - failures}/{total} fixtures passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
