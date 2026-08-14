"""Content-addressed on-disk cache — the project's cost-discipline mechanism.

Every paid or rate-limited request goes through here, so a second run of the
pipeline costs nothing and reproduces the first run byte for byte (PRD R2.3).

That makes the *key* the interesting part. It must be a genuine content address:

- stable across processes, which rules out Python's `hash()` (PYTHONHASHSEED
  salts it per interpreter, so yesterday's cache would never be found today);
- independent of dict ordering, since callers build param dicts in whatever
  order reads best;
- sensitive to any change in url or params, so a widened query can never be
  served the narrow query's answer.

Canonical JSON with sorted keys, hashed with SHA-256, satisfies all three. JSON
also keeps values structurally separated, so `{"a": "1", "b": "2"}` cannot
collapse onto `{"a": "1b=2"}` the way naive `k=v` concatenation would.

Entries are stored one file per key, discriminated by suffix, so that a raw
`bytes` payload (an ortho tile) comes back as bytes and a JSON payload comes
back decoded — never one disguised as the other.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

#: Suffixes that discriminate the two payload kinds on disk.
_JSON_SUFFIX = ".json"
_BYTES_SUFFIX = ".bin"


class Cache:
    """A directory of content-addressed entries. Created on first write."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # --- addressing ---------------------------------------------------------

    def key(self, url: str, params: Mapping[str, Any] | None = None) -> str:
        """Return the content address of `url` + `params`.

        `default=str` keeps non-JSON-able param values (dates, enums) addressable
        rather than raising deep inside a source module.
        """
        canonical = json.dumps(
            [url, params],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # --- storage ------------------------------------------------------------

    def put(self, key: str, value: Any) -> None:
        """Store `value`, replacing any existing entry for `key`.

        `bytes` are stored verbatim; anything else is stored as JSON. The other
        kind's file is removed so a key can never resolve to two payloads.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        if isinstance(value, (bytes, bytearray)):
            self._path(key, _BYTES_SUFFIX).write_bytes(bytes(value))
            self._path(key, _JSON_SUFFIX).unlink(missing_ok=True)
        else:
            self._path(key, _JSON_SUFFIX).write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
            self._path(key, _BYTES_SUFFIX).unlink(missing_ok=True)

    def get(self, key: str) -> Any | None:
        """Return the stored payload, or None on a miss. Never creates the root."""
        blob = self._path(key, _BYTES_SUFFIX)
        if blob.is_file():
            return blob.read_bytes()
        document = self._path(key, _JSON_SUFFIX)
        if document.is_file():
            return json.loads(document.read_text(encoding="utf-8"))
        return None

    # --- internals ----------------------------------------------------------

    def _path(self, key: str, suffix: str) -> Path:
        if not key or "/" in key or "\\" in key or ".." in key:
            raise ValueError(f"not a cache key: {key!r}")
        return self.root / f"{key}{suffix}"
