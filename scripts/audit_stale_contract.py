"""R35 stale-contract audit.

Scans active repository surfaces (*.md, *.py, *.js, *.html, Makefile) for
assertions of the superseded V1 scoring contract — the additive five-group
formula, the old group names presented as current schema, the -15 rental
modifier, the 90-day mover hard cutoff phrased as current behavior, provider
churn, the deferred-maintenance bonus, and stale golden-fixture counts — and
fails unless the offending path is on the explicit versioned allowlist
(eval/v2/allowlist-historical.json). Adding an allowlist path is a reviewed
change to that file, never a runtime heuristic.

Always excluded from the scan regardless of the allowlist: .git/, .venv/,
node_modules/, cache/, data/ (raw source snapshots), tests/ and *.test.js
(pins of forbidden strings), and the allowlist file itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Each forbidden V1 assertion class, as data: (pattern_id, compiled regex)
# applied per line. Lookaheads AND together the co-occurring cues so incidental
# uses of a single word do not trip the audit.
PATTERNS = [
    (
        "additive-five-group-formula",
        re.compile(
            r"clamp\(\s*mover\s*\+\s*hires_out\s*\+\s*capacity\s*\+\s*need\s*\+\s*modifier"
        ),
    ),
    (
        "hires_out-as-current",
        re.compile(r"\bhires_out\s+group\b", re.I),
    ),
    (
        "raw_total-as-current-schema",
        re.compile(r"\braw_total\s+alongside\b|\bincludes\s+raw_total\b", re.I),
    ),
    (
        "minus-15-rental",
        re.compile(r"^(?=.*(?<![\w.])[-−]\s?15\b)(?=.*rental)", re.I),
    ),
    (
        "90-day-mover-cutoff-as-current",
        re.compile(
            r"^(?=.*\b90[ -]days?\b)(?=.*(?:mover|deed))(?=.*(?:scores?\s+0\b|cutoff|zero))",
            re.I,
        ),
    ),
    (
        "provider-churn",
        re.compile(r"\bprovider\s+churn\b", re.I),
    ),
    (
        "deferred-maintenance-bonus",
        re.compile(
            r"\bdeferred[-\s]maintenance\b.*(?:combo|bonus|\badds\s+\d+\s+points?)", re.I
        ),
    ),
    (
        "stale-golden-count",
        re.compile(r"\b(?:12|13|twelve|thirteen)\s+golden\b", re.I),
    ),
    (
        "stale-golden-count-parenthetical",
        re.compile(r"\bgolden\s+fixtures?\s*\(\s*1[23]\b", re.I),
    ),
    (
        "stale-count-at-time-of-writing",
        re.compile(r"\b1[23]\s+at\s+time\s+of\s+writing\b", re.I),
    ),
]

SCAN_SUFFIXES = (".md", ".py", ".js", ".html")
SCAN_NAMES = ("Makefile",)

EXCLUDED_DIRS = {".git", ".venv", ".claude", "node_modules", "cache", "data", "tests"}
ALLOWLIST_NAME = "allowlist-historical.json"
# The auditor itself necessarily names the forbidden phrases it hunts for.
SELF_NAME = "audit_stale_contract.py"


def _load_allowlist(repo_root: Path, allowlist_path: Path):
    allowlist_path = Path(allowlist_path)
    if not allowlist_path.exists():
        raise FileNotFoundError(
            f"audit refuses to run without its versioned allowlist: {allowlist_path}"
        )
    data = json.loads(allowlist_path.read_text())
    entries = []
    for e in data.get("entries", []):
        rel = e["path"]
        target = repo_root / rel.rstrip("/")
        if not target.exists():
            raise FileNotFoundError(f"allowlist entry does not exist: {rel}")
        entries.append(rel)
    return entries


def _is_allowlisted(rel: str, entries) -> bool:
    for entry in entries:
        if entry.endswith("/"):
            if rel.startswith(entry):
                return True
        elif rel == entry:
            return True
    return False


def _iter_scan_files(repo_root: Path, allowlist_path: Path):
    allowlist_resolved = Path(allowlist_path).resolve()
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_root).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts[:-1]):
            continue
        if path.name.endswith(".test.js"):
            continue
        if path.name in (ALLOWLIST_NAME, SELF_NAME) or path.resolve() == allowlist_resolved:
            continue
        if path.suffix in SCAN_SUFFIXES or path.name in SCAN_NAMES:
            yield path


def run_audit(repo_root, allowlist_path):
    """Return the list of stale-contract findings outside the allowlist."""
    repo_root = Path(repo_root)
    entries = _load_allowlist(repo_root, allowlist_path)
    findings = []
    for path in _iter_scan_files(repo_root, allowlist_path):
        rel = path.relative_to(repo_root).as_posix()
        if _is_allowlisted(rel, entries):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern_id, regex in PATTERNS:
                if regex.search(line):
                    findings.append(
                        {"path": rel, "line": lineno, "pattern": pattern_id}
                    )
    findings.sort(key=lambda f: (f["path"], f["line"], f["pattern"]))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", default=str(repo_root))
    parser.add_argument(
        "--allowlist",
        default=str(repo_root / "eval" / "v2" / ALLOWLIST_NAME),
    )
    args = parser.parse_args(argv)
    findings = run_audit(Path(args.root), Path(args.allowlist))
    for f in findings:
        print(f"{f['path']}:{f['line']}: stale V1 contract [{f['pattern']}]")
    if findings:
        print(f"stale-contract audit: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("stale-contract audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
