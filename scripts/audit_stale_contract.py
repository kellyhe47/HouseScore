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

The implementation must define:

- PATTERNS: data — list of (pattern_id, compiled_regex) covering each
  forbidden V1 assertion class.
- run_audit(repo_root, allowlist_path) -> list of findings, each naming at
  least the offending file (repo-relative path), line number, and pattern.
  Raises if the allowlist file is missing (the audit refuses to run without
  its versioned allowlist) or lists a path that does not exist.
- main() -> exits nonzero when findings exist, zero otherwise.
"""

from __future__ import annotations

import sys

PATTERNS = NotImplemented

SCAN_SUFFIXES = (".md", ".py", ".js", ".html")

EXCLUDED_DIRS = NotImplemented


def run_audit(repo_root, allowlist_path):
    """Return the list of stale-contract findings outside the allowlist."""
    raise NotImplementedError("ticket 108: R35 stale-contract audit")


def main(argv=None):
    raise NotImplementedError("ticket 108: R35 stale-contract audit")


if __name__ == "__main__":
    sys.exit(main())
