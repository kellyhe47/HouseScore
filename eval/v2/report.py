"""Ticket 104 / R34: deterministic V2 recalculation report + PII scan.

`build_report(data_dir, out_path)` reads ONLY the published run artifacts
(doors.geojson, houseaccount.sqlite, run_manifest.json) — no network, no
re-derivation — and writes the V2-shaped ``eval/report.json``. It raises
``PIIViolationError`` when any published artifact carries owner names,
mailing addresses, or permit-agent identity fields (R25), which is what
makes the ``make eval`` entrypoint exit nonzero.

Test-first stub: the tests in tests/test_v2_report.py pin the key schema;
this raises NotImplementedError until the implementer lands it.
"""

from __future__ import annotations

from pathlib import Path


class PIIViolationError(Exception):
    """A published artifact contains a forbidden personal-identity field."""


def build_report(data_dir: str | Path, out_path: str | Path) -> dict:
    """Build the R34 report from published artifacts and write it to out_path.

    Deterministic: identical artifacts produce byte-identical output.
    Raises PIIViolationError when the PII scan finds a violation.
    """
    raise NotImplementedError("ticket 104: R34 report builder not implemented")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by `make eval`. Nonzero exit on PII violation."""
    raise NotImplementedError("ticket 104: report CLI not implemented")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
