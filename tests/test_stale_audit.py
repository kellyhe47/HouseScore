"""Ticket 108 — R35 stale-contract audit, versioned allowlist, R31/R33 docs pins.

NOTE: this file deliberately contains V1 contract strings as test data. The
auditor MUST exclude tests/ from its scan (pinned below), so these pins never
trip the audit they exercise.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "scripts" / "audit_stale_contract.py"
ALLOWLIST_PATH = REPO_ROOT / "eval" / "v2" / "allowlist-historical.json"
V2_PLAN_NAME = "2026-08-18-2221-feat-door-score-v2-plan.md"

V1_FORMULA = "score = clamp(mover + hires_out + capacity + need + modifier, 0, 100)"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_stale_contract", AUDIT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    return load_audit_module()


def write_allowlist(root: Path, paths=()):
    entries = []
    for p in paths:
        entries.append({"path": p, "reason": "test-historical"})
    al = root / "eval" / "v2" / "allowlist-historical.json"
    al.parent.mkdir(parents=True, exist_ok=True)
    al.write_text(
        json.dumps(
            {
                "version": 1,
                "reviewed_change_note": "test allowlist — additions are reviewed changes",
                "entries": entries,
            }
        )
    )
    return al


def make_repo(tmp_path: Path, files: dict, allowlist_paths=()):
    """Build a minimal scan target: files is {relpath: content}."""
    root = tmp_path / "repo"
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    al = write_allowlist(root, allowlist_paths)
    return root, al


def finding_paths(findings):
    return [f["path"] for f in findings]


# ---------------------------------------------------------------------------
# 1. The real repo, with the real allowlist, is clean.
#    Red now via NotImplementedError; after implementation this pins the
#    reconciled docs state — the implementer must fix the docs too.
# ---------------------------------------------------------------------------


def test_real_repo_with_real_allowlist_is_clean(audit):
    findings = audit.run_audit(REPO_ROOT, ALLOWLIST_PATH)
    assert findings == [], (
        "stale V1 contract assertions on active surfaces: "
        + json.dumps(findings, default=str)
    )


# ---------------------------------------------------------------------------
# 2. Planted V1 assertion → finding naming file + line; allowlisted → clean.
# ---------------------------------------------------------------------------


def test_planted_v1_formula_yields_finding_with_file_and_line(audit, tmp_path):
    content = "# Notes\n\nThe score is " + V1_FORMULA + " per door.\n"
    root, al = make_repo(tmp_path, {"README.md": content})
    findings = audit.run_audit(root, al)
    assert len(findings) >= 1
    f = findings[0]
    assert f["path"] == "README.md"
    assert f["line"] == 3


def test_same_text_under_allowlisted_path_is_not_a_finding(audit, tmp_path):
    content = "historical: " + V1_FORMULA + "\n"
    root, al = make_repo(
        tmp_path,
        {"docs/review-2026-01-01.md": content},
        allowlist_paths=["docs/review-2026-01-01.md"],
    )
    assert audit.run_audit(root, al) == []


def test_allowlisted_directory_prefix_covers_children(audit, tmp_path):
    root, al = make_repo(
        tmp_path,
        {"history/board/108.md": V1_FORMULA + "\n"},
        allowlist_paths=["history/"],
    )
    assert audit.run_audit(root, al) == []


# ---------------------------------------------------------------------------
# 3. Pattern coverage: each forbidden V1 assertion class is detected.
# ---------------------------------------------------------------------------

FORBIDDEN_SAMPLES = [
    pytest.param(
        "The formula is " + V1_FORMULA + " and nothing else.",
        id="additive-five-group-formula",
    ),
    pytest.param(
        "The hires_out group contributes up to 60 points today.",
        id="hires_out-as-current",
    ),
    pytest.param(
        "Each door's response includes raw_total alongside the clamped score.",
        id="raw_total-as-current-schema",
    ),
    pytest.param(
        "The absentee modifier is -15 for any rental-registration match.",
        id="minus-15-rental-ascii",
    ),
    pytest.param(
        "Modifier | −15 | absentee-likely (rental-registration match only).",
        id="minus-15-rental-unicode",
    ),
    pytest.param(
        "Deeds older than 90 days score 0 in the Mover group.",
        id="90-day-mover-cutoff-as-current",
    ),
    pytest.param(
        "Provider churn: +20 iff two distinct contractor names appear.",
        id="provider-churn",
    ),
    pytest.param(
        "The deferred-maintenance combo adds 4 points when all three hold.",
        id="deferred-maintenance-bonus",
    ),
    pytest.param(
        "The harness runs the 13 golden fixtures against the engine.",
        id="stale-count-13-golden",
    ),
    pytest.param(
        "All twelve golden fixtures pass in under a second.",
        id="stale-count-twelve-golden",
    ),
    pytest.param(
        "Golden fixtures (12 at time of writing) are the contract.",
        id="stale-count-12-at-time-of-writing",
    ),
]


@pytest.mark.parametrize("text", FORBIDDEN_SAMPLES)
def test_forbidden_pattern_class_detected(audit, text, tmp_path):
    root, al = make_repo(tmp_path, {"docs/NOTES.md": "intro\n" + text + "\n"})
    findings = audit.run_audit(root, al)
    assert findings, "pattern not detected: " + text
    assert finding_paths(findings) == ["docs/NOTES.md"] * len(findings)
    assert all(f["line"] == 2 for f in findings)


def test_patterns_are_declared_as_data(audit):
    assert audit.PATTERNS is not NotImplemented
    assert len(list(audit.PATTERNS)) >= 8


# ---------------------------------------------------------------------------
# 4. The allowlist is versioned data: required, and entries must exist.
# ---------------------------------------------------------------------------


def test_audit_refuses_to_run_without_allowlist_file(audit, tmp_path):
    root, al = make_repo(tmp_path, {"README.md": "clean\n"})
    al.unlink()
    with pytest.raises(Exception):
        audit.run_audit(root, al)


def test_allowlist_entry_pointing_at_missing_path_is_an_error(audit, tmp_path):
    root, al = make_repo(
        tmp_path, {"README.md": "clean\n"}, allowlist_paths=["docs/ghost.md"]
    )
    with pytest.raises(Exception):
        audit.run_audit(root, al)


def test_real_allowlist_is_versioned_and_entries_exist():
    data = json.loads(ALLOWLIST_PATH.read_text())
    assert "reviewed_change_note" in data and data["reviewed_change_note"].strip()
    assert isinstance(data["entries"], list) and data["entries"]
    for e in data["entries"]:
        p = REPO_ROOT / e["path"]
        assert p.exists(), "allowlist entry does not exist: " + e["path"]


# ---------------------------------------------------------------------------
# 5. Docs reconciliation pins (R31/R33) — red against current stale docs.
# ---------------------------------------------------------------------------

CANONICAL_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "PRD.md",
    REPO_ROOT / "docs" / "DEPLOY.md",
    REPO_ROOT / "docs" / "handoff-prompt.md",
]

STALE_COUNT_STRINGS = [
    "13 golden",
    "thirteen golden",
    "twelve golden",
    "12 golden",
    "12 fixtures",
    "12 at time of writing",
]


@pytest.mark.parametrize("doc", CANONICAL_DOCS, ids=lambda p: p.name)
def test_canonical_doc_names_v2_as_current_scoring_authority(doc):
    text = doc.read_text()
    assert V2_PLAN_NAME in text or re.search(r"\bV2\b", text), (
        doc.name + " must name V2 or point to the V2 plan as scoring authority"
    )


@pytest.mark.parametrize("doc", CANONICAL_DOCS, ids=lambda p: p.name)
def test_canonical_doc_has_no_stale_fixture_counts(doc):
    text = doc.read_text().lower()
    for s in STALE_COUNT_STRINGS:
        assert s.lower() not in text, doc.name + " still says: " + s


def test_report_json_is_the_count_authority():
    report = json.loads((REPO_ROOT / "eval" / "report.json").read_text())
    assert report["golden"]["fixtures_total"] == 42
    assert report["coverage"]["doors_total"] == 540


def test_readme_fixture_count_matches_report_json():
    text = REPO_ROOT.joinpath("README.md").read_text()
    assert re.search(r"42[^\n]{0,60}golden|golden[^\n]{0,60}42", text, re.I), (
        "README must state the 42-fixture golden suite (eval/report.json)"
    )


def test_prd_scoring_sections_carry_supersession_pointer():
    text = (REPO_ROOT / "docs" / "PRD.md").read_text()
    assert V2_PLAN_NAME in text, (
        "docs/PRD.md scoring sections must point to docs/plans/" + V2_PLAN_NAME
    )
    assert re.search(r"supersed", text, re.I), (
        "docs/PRD.md must mark V1 scoring sections as superseded"
    )


# ---------------------------------------------------------------------------
# 6. Makefile wiring: an audit target that `make test` runs.
# ---------------------------------------------------------------------------


def test_makefile_wires_audit_into_test(audit):
    mk = (REPO_ROOT / "Makefile").read_text()
    assert re.search(r"(?m)^audit:", mk), "Makefile must define an `audit` target"
    assert re.search(r"(?m)^test:.*\baudit\b", mk) or re.search(
        r"(?m)^test-py:.*\baudit\b", mk
    ), "`make test` must run the stale-contract audit"
    assert "audit_stale_contract" in mk


# ---------------------------------------------------------------------------
# 7. Built-in exclusions the auditor honors beyond the allowlist.
# ---------------------------------------------------------------------------

EXCLUDED_PLANT_PATHS = [
    ".git/COMMIT_EDITMSG.md",
    ".venv/lib/notes.py",
    "node_modules/pkg/index.js",
    "cache/snapshot.md",
    "data/raw/sdl.md",
    "tests/test_pins.py",
    "web/js/no-v1-strings.test.js",
    "eval/v2/allowlist-historical.json",
]


@pytest.mark.parametrize("rel", EXCLUDED_PLANT_PATHS)
def test_builtin_exclusions_are_not_scanned(audit, rel, tmp_path):
    if rel.endswith("allowlist-historical.json"):
        # The allowlist file itself may quote forbidden strings in reasons.
        root, al = make_repo(tmp_path, {"README.md": "clean\n"})
        data = json.loads(al.read_text())
        data["reviewed_change_note"] = "historical: " + V1_FORMULA
        al.write_text(json.dumps(data))
    else:
        root, al = make_repo(tmp_path, {rel: "x = 1  # " + V1_FORMULA + "\n"})
    assert audit.run_audit(root, al) == []


def test_non_text_surfaces_are_not_scanned(audit, tmp_path):
    # Raw source snapshots (e.g. SDL json blobs) are out of scope.
    root, al = make_repo(tmp_path, {"data/sdl/blob.json": '{"note": "' + V1_FORMULA + '"}'})
    (root / "notes.txt").write_text(V1_FORMULA + "\n")
    assert audit.run_audit(root, al) == []


def test_scanned_surfaces_include_makefile(audit, tmp_path):
    root, al = make_repo(tmp_path, {"Makefile": "# " + V1_FORMULA + "\ntest:\n"})
    findings = audit.run_audit(root, al)
    assert finding_paths(findings) == ["Makefile"]


# ---------------------------------------------------------------------------
# main() exits nonzero on findings, zero when clean.
# ---------------------------------------------------------------------------


def test_main_exit_codes(audit, tmp_path, monkeypatch):
    dirty, dirty_al = make_repo(tmp_path / "dirty", {"README.md": V1_FORMULA + "\n"})
    clean, clean_al = make_repo(tmp_path / "clean", {"README.md": "fine\n"})

    rc_dirty = audit.main(["--root", str(dirty), "--allowlist", str(dirty_al)])
    rc_clean = audit.main(["--root", str(clean), "--allowlist", str(clean_al)])
    assert rc_dirty not in (0, None)
    assert rc_clean in (0, None)
