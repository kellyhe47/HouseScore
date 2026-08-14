"""Process configuration: which credentials are present, and where things live.

Two decisions are baked in here, both deliberate.

*Every credential is optional.* This project must import, run, and produce a
scored run on a machine with no keys at all — degrading to the warm cache and
to fixture data instead of exploding. So `from_env` never raises on a missing
key; it reports `None` and lets each source decide whether it can proceed.

*Paths come from the repository, not the cwd.* The pipeline, the eval harness,
the MCP server and the web build are all launched from different directories,
but they must agree on exactly one `cache/` and one `data/` — otherwise a warm
cache silently goes cold and a re-run costs real money.

Keys are read from `os.environ` and nowhere else: no defaults, no config file,
no literals. That is a security property, and `tests/test_config.py` enforces it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The file whose presence marks the repository root.
_ROOT_MARKER = "pyproject.toml"


def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` to the directory holding the root marker.

    Falls back to the package's grandparent (`src/houseaccount/..` -> repo) so
    an installed wheel, which ships no ``pyproject.toml``, still gets a sane
    absolute path instead of raising at import time.
    """
    for candidate in (start, *start.parents):
        if (candidate / _ROOT_MARKER).is_file():
            return candidate
    return start.parents[1]


def _env(name: str) -> str | None:
    """Read one credential. Absent *or blank* both mean "not configured"."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of the environment, taken once at startup."""

    anthropic_api_key: str | None
    census_api_key: str | None
    google_maps_key: str | None
    repo_root: Path
    cache_dir: Path
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        repo_root = _find_repo_root(Path(__file__).resolve().parent)
        return cls(
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            census_api_key=_env("CENSUS_API_KEY"),
            google_maps_key=_env("GOOGLE_MAPS_KEY"),
            repo_root=repo_root,
            cache_dir=repo_root / "cache",
            data_dir=repo_root / "data",
        )
