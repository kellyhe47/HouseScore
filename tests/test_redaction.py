"""Permanent no-identity-data guard (T001).

Project invariant: HouseAccount scores *addresses*, never people. The parcel
service happily returns owner names and mailing addresses; none of those fields
may ever reach `src/`, `eval/`, or `web/`.

This is a standing regression guard, not a one-off — it re-scans the real trees
on every run, so it goes red the moment someone reintroduces one of the tokens.
The meta-tests below prove the scanner actually bites.

`tests/` is exempt: the tokens necessarily appear in this file.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Identity-bearing field names from the NJ parcel service. Banned outside tests/.
FORBIDDEN_TOKENS = ("OWNER_NAME", "ST_ADDRESS", "CITY_STATE")

#: Trees that ship as the product. `tests/` and `docs/` are deliberately absent.
SCANNED_ROOTS = ("src", "eval", "web")

SKIPPED_DIRS = frozenset(
    {
        "tests",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "vendor",
        "dist",
        "build",
        "site-packages",
    }
)

SKIPPED_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
        ".pdf", ".zip", ".gz", ".tar", ".whl", ".so", ".dylib", ".pyc",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mbtiles", ".pbf", ".parquet", ".sqlite", ".db",
    }
)


def _is_vendored_or_binary(path: Path) -> bool:
    if path.suffix.lower() in SKIPPED_SUFFIXES:
        return True
    return path.name.endswith((".min.js", ".min.css", ".lock"))


def scan(roots, tokens=FORBIDDEN_TOKENS):
    """Return (hits, files_scanned) for `roots`.

    A hit is ``(relative_path, line_number, token)``. Undecodable files are
    treated as binary and skipped.
    """
    hits, scanned = [], []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if SKIPPED_DIRS & set(path.relative_to(root).parts[:-1]):
                continue
            if _is_vendored_or_binary(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned.append(path)
            for lineno, line in enumerate(text.splitlines(), start=1):
                hits += [(str(path), lineno, tok) for tok in tokens if tok in line]
    return hits, scanned


# --- the invariant ----------------------------------------------------------


def test_no_identity_tokens_in_shipped_source():
    hits, _ = scan(REPO_ROOT / root for root in SCANNED_ROOTS)
    assert hits == [], "identity-bearing field names must never leave the source layer"


def test_the_scan_actually_reads_files():
    """A guard that silently scans nothing would pass forever."""
    _, scanned = scan(REPO_ROOT / root for root in SCANNED_ROOTS)
    assert len(scanned) > 0


@pytest.mark.parametrize("root", SCANNED_ROOTS)
def test_every_declared_root_exists_and_is_covered(root):
    directory = REPO_ROOT / root
    assert directory.is_dir(), f"{root}/ is declared in the guard but missing from the repo"
    _, scanned = scan([directory])
    assert len(scanned) > 0, f"{root}/ contributed no scanned files"


# --- meta: the guard must be able to fail -----------------------------------


@pytest.mark.parametrize("token", FORBIDDEN_TOKENS)
def test_guard_catches_an_introduced_violation(tmp_path, token):
    fake_src = tmp_path / "src" / "houseaccount"
    fake_src.mkdir(parents=True)
    (fake_src / "sources" ).mkdir()
    (fake_src / "sources" / "parcels.py").write_text(
        f'FIELDS = ["PAMS_PIN", "{token}", "DEED_DATE"]\n', encoding="utf-8"
    )

    hits, _ = scan([tmp_path / "src"])
    assert [(Path(p).name, n, t) for p, n, t in hits] == [("parcels.py", 1, token)]


def test_guard_catches_violations_in_web_and_eval_trees(tmp_path):
    for root, name, body in [
        ("web", "map.js", "const label = feature.properties.OWNER_NAME;\n"),
        ("eval", "report.py", 'COLUMNS = ["ST_ADDRESS"]\n'),
    ]:
        (tmp_path / root).mkdir()
        (tmp_path / root / name).write_text(body, encoding="utf-8")

    hits, _ = scan([tmp_path / "web", tmp_path / "eval"])
    assert sorted(t for _, _, t in hits) == ["OWNER_NAME", "ST_ADDRESS"]


def test_guard_exempts_the_tests_tree(tmp_path):
    tests_dir = tmp_path / "src" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_thing.py").write_text('TOKEN = "OWNER_NAME"\n', encoding="utf-8")

    hits, scanned = scan([tmp_path / "src"])
    assert hits == []
    assert scanned == []


def test_guard_skips_vendored_and_binary_paths(tmp_path):
    web = tmp_path / "web"
    (web / "node_modules" / "leaflet").mkdir(parents=True)
    (web / "node_modules" / "leaflet" / "index.js").write_text("OWNER_NAME\n", encoding="utf-8")
    (web / "bundle.min.js").write_text("OWNER_NAME\n", encoding="utf-8")
    (web / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nOWNER_NAME")

    hits, _ = scan([web])
    assert hits == []


def test_this_guard_file_is_not_scanned_by_itself():
    """The tokens live in this file; the real scan must still come back clean."""
    assert "OWNER_NAME" in Path(__file__).read_text(encoding="utf-8")
    hits, _ = scan([REPO_ROOT / root for root in SCANNED_ROOTS])
    assert hits == []
