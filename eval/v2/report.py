"""Ticket 104 / R34: deterministic V2 recalculation report + PII scan.

`build_report(data_dir, out_path)` reads ONLY the published run artifacts
(doors.geojson, houseaccount.sqlite, run_manifest.json) — no network, no
re-derivation of the scoring rules — and writes the V2-shaped
``eval/report.json``. It raises ``PIIViolationError`` when any published
artifact carries owner names, mailing addresses, or permit-agent identity
fields (R25), which is what makes the ``make eval`` entrypoint exit nonzero.

Where each block comes from:

* coverage / freshness / cost — ``run_manifest.json`` verbatim.
* category + score distributions — the 540 published envelopes in
  ``doors.geojson``; deciles via inclusive quantiles so the R38 ramp can use
  them as stops.
* missing signals — the typed ``data_gaps`` published on every door.
* exclusions — published evidence counts (``project_neutralized``,
  ``mover_invalid_sale``) and the manifest's own counters; published-or-zero,
  never a re-derivation of the conservative rules. This run's live feeds carry
  no SDL lifecycle and no cross-source municipal id, so nothing was voided or
  coalesced — those counters are honestly 0 unless the manifest publishes
  otherwise (``exclusions.voided_or_admin`` / ``.cross_source_dedup_collapsed``).
* vision — PRD R5.1 metrics carried over from the V1 harness. Hand labels
  have still not been collected, so the numbers are the frozen fixture-09
  confusion-set arithmetic, and the NOT-A-MEASUREMENT banner travels with
  them exactly as it did in V1.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
DEFAULT_DATA_DIR = EVAL_DIR.parent / "data"
DEFAULT_REPORT_PATH = EVAL_DIR / "report.json"

DECILE_KEYS = tuple(f"q{n}" for n in range(10, 100, 10))

#: The frozen fixture-09 confusion set (V1 golden contract, retained verbatim
#: through the cutover). The arithmetic runs here; nothing is a pasted result.
_FROZEN_CONFUSION = {"tp": 9, "fp": 2, "fn": 1, "tn": 28}
_FROZEN_PROBE = {"claimed_detections": 1, "universe": 20}

FROZEN_SOURCE = "frozen fixture 09 (hand labels not yet collected)"
NOT_A_MEASUREMENT_BANNER = (
    "NOT A MEASUREMENT: precision/recall/hallucination are frozen fixture "
    "arithmetic over fixture 09's confusion set, not scores over hand-labeled "
    "imagery. Hand labels have not been collected yet."
)

#: Field-name fragments that mean a published artifact is carrying a personal
#: identity (R25: owner names, mailing addresses, permit-agent identity).
_PII_KEY_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"owner",
        r"mailing",
        r"mail_addr",
        r"\bagent\b|_agent|agent_",
        r"applicant",
        r"taxpayer",
        r"occupant",
        r"resident",
    )
)


class PIIViolationError(Exception):
    """A published artifact contains a forbidden personal-identity field."""


# --- PII scan -----------------------------------------------------------------


def _key_is_pii(key: str) -> bool:
    return any(p.search(key) for p in _PII_KEY_PATTERNS)


def _scan_json(node: Any, path: str, findings: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if _key_is_pii(str(key)):
                findings.append(here)
            _scan_json(value, here, findings)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_json(value, f"{path}[{index}]", findings)


def _pii_scan(data_dir: Path, geojson: dict, manifest: dict) -> list[str]:
    """Every published artifact, scanned for identity-shaped field names."""
    findings: list[str] = []
    _scan_json(geojson, "doors.geojson", findings)
    _scan_json(manifest, "run_manifest.json", findings)

    db = sqlite3.connect(data_dir / "houseaccount.sqlite")
    try:
        tables = [
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        ]
        for table in sorted(tables):
            for row in db.execute(f"pragma table_info({table})"):
                column = str(row[1])
                if _key_is_pii(column):
                    findings.append(f"houseaccount.sqlite:{table}.{column}")
    finally:
        db.close()
    return sorted(findings)


# --- distribution helpers -------------------------------------------------------


def _round(value: float) -> float:
    """One rounding rule for every derived float, so re-runs agree in bytes."""
    return round(float(value), 6)


def _dist(values: list[float]) -> dict[str, float]:
    return {
        "min": _round(min(values)),
        "max": _round(max(values)),
        "mean": _round(statistics.fmean(values)),
    }


def _deciles(values: list[float]) -> dict[str, float]:
    cuts = statistics.quantiles(values, n=10, method="inclusive")
    return {key: _round(cut) for key, cut in zip(DECILE_KEYS, cuts)}


# --- blocks ---------------------------------------------------------------------


def _exclusions(data_dir: Path, manifest: dict) -> dict[str, int]:
    """Per-conservative-rule counts, read from what the run published.

    The evidence table is the record of the first two rules firing; the last
    two are manifest counters, published-or-zero (this run's statewide permit
    feed carries no lifecycle status and no cross-source municipal id, so its
    true counts are zero unless a future run publishes otherwise).
    """
    db = sqlite3.connect(data_dir / "houseaccount.sqlite")
    try:
        def count(evidence_type: str) -> int:
            return db.execute(
                "select count(*) from evidence where type=?", (evidence_type,)
            ).fetchone()[0]

        stale_open = count("project_neutralized")
        non_arms = count("mover_invalid_sale")
    finally:
        db.close()

    published = manifest.get("exclusions") or {}
    return {
        "stale_open_neutralized": stale_open,
        "non_arms_length_sales": non_arms,
        "voided_or_admin": int(published.get("voided_or_admin", 0)),
        "cross_source_dedup_collapsed": int(
            published.get("cross_source_dedup_collapsed", 0)
        ),
    }


def _vision(manifest: dict) -> dict[str, Any]:
    """PRD R5.1 metrics, carried from the V1 eval path with its banner.

    No hand labels exist yet, so the numbers are the frozen fixture-09
    arithmetic — and the block says so instead of dressing them up.
    """
    tp, fp, fn = (
        _FROZEN_CONFUSION["tp"],
        _FROZEN_CONFUSION["fp"],
        _FROZEN_CONFUSION["fn"],
    )
    return {
        "precision": _round(tp / (tp + fp)),
        "recall": _round(tp / (tp + fn)),
        "hallucination_rate": _round(
            _FROZEN_PROBE["claimed_detections"] / _FROZEN_PROBE["universe"]
        ),
        "cost_per_door": manifest.get("cost_per_door", 0.0),
        "metrics_source": FROZEN_SOURCE,
        "banner": NOT_A_MEASUREMENT_BANNER,
    }


# --- the report -----------------------------------------------------------------


def build_report(data_dir: str | Path, out_path: str | Path) -> dict:
    """Build the R34 report from published artifacts and write it to out_path.

    Deterministic: identical artifacts produce byte-identical output.
    Raises PIIViolationError when the PII scan finds a violation.
    """
    data_dir = Path(data_dir)
    out_path = Path(out_path)

    manifest = json.loads((data_dir / "run_manifest.json").read_text(encoding="utf-8"))
    geojson = json.loads((data_dir / "doors.geojson").read_text(encoding="utf-8"))

    findings = _pii_scan(data_dir, geojson, manifest)
    if findings:
        raise PIIViolationError(
            "published artifacts carry identity fields (R25): " + ", ".join(findings)
        )

    properties = [feature["properties"] for feature in geojson["features"]]
    scored = [p for p in properties if p.get("score") is not None]
    scores = [float(p["score"]) for p in scored]

    categories = {
        cat: _dist([float(p["categories"][cat]) for p in scored])
        for cat in ("project", "capacity", "fit")
    }

    gap_counts: dict[str, int] = {}
    for p in properties:
        for gap in p.get("data_gaps") or ():
            gap_type = str(gap.get("type"))
            gap_counts[gap_type] = gap_counts.get(gap_type, 0) + 1

    fixtures_total = len(sorted(GOLDEN_DIR.glob("*.json")))

    report = {
        "score_contract_version": "v2",
        "as_of": manifest["as_of"],
        "golden": {"fixtures_total": fixtures_total},
        "coverage": {
            "doors_total": manifest["doors_total"],
            "doors_scored": manifest["doors_scored"],
            "coverage": manifest["coverage"],
        },
        "categories": categories,
        "score_distribution": {**_dist(scores), "quantiles": _deciles(scores)},
        "missing_signals": {key: gap_counts[key] for key in sorted(gap_counts)},
        "source_freshness": manifest["retrieved"],
        "exclusions": _exclusions(data_dir, manifest),
        "vision": _vision(manifest),
        "pii_scan": {"ok": True, "findings": []},
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by `make eval`. Nonzero exit on PII violation."""
    try:
        report = build_report(DEFAULT_DATA_DIR, DEFAULT_REPORT_PATH)
    except PIIViolationError as error:
        print(f"eval: PII violation -- {error}")
        return 1
    except FileNotFoundError as error:
        print(f"eval: missing published artifact -- {error}")
        return 1

    quantiles = report["score_distribution"]["quantiles"]
    print("HouseAccount R34 recalculation report")
    print(f"  as_of:            {report['as_of']}")
    print(
        "  coverage:         "
        f"{report['coverage']['doors_scored']}/{report['coverage']['doors_total']}"
    )
    print(f"  score deciles:    {[quantiles[k] for k in DECILE_KEYS]}")
    print(f"  exclusions:       {report['exclusions']}")
    print(f"  vision source:    {report['vision']['metrics_source']}")
    print(f"  {report['vision']['banner']}")
    print(f"  pii scan:         ok")
    print(f"  written:          {DEFAULT_REPORT_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
