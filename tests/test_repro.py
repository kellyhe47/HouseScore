"""Reproducibility guarantees (T015) — R13 and R14.

A reviewer must be able to clone this repo and re-run everything from the
README alone, and there must be proof there are no secrets in the tree. Two
guarantees, one file:

1. **No secrets in the shipped source.** A standing scan of `src/`, `eval/`
   and `web/` for secret-shaped literals. Modelled on
   `tests/test_redaction.py`, meta-tests included: a guard that silently
   scans nothing, or that cannot bite, looks exactly like a clean tree.

2. **The README is true.** Not "the README reads well" -- these tests are
   deliberately structural. They assert that every `make` target the README
   tells a reviewer to run exists in the Makefile, that the environment
   variables named in the README and in `.env.example` are the same set in
   both directions, and that the documented sections appear in the documented
   order. Nothing here pins a sentence: a README that is reworded stays green,
   a README that lies goes red.

`tests/` is exempt from the secret scan for the same reason it is exempt from
the redaction one: the patterns necessarily appear in this file.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

README = REPO_ROOT / "README.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MAKEFILE = REPO_ROOT / "Makefile"

#: Trees that ship as the product. `tests/` and `docs/` are deliberately absent.
SCANNED_ROOTS = ("src", "eval", "web")

#: Secret shapes, by the name reported when one is found.
#:
#: The hex pattern is deliberately narrow: a long hex string only counts when
#: it is *assigned to a key-named variable*. Bare hex is a checksum, a colour
#: table or a test vector far more often than it is a credential, and a guard
#: that cries wolf on every sha256 in the tree gets muted within a week.
SECRET_PATTERNS = {
    "anthropic api key": r"sk-ant-[A-Za-z0-9_\-]{12,}",
    "google api key": r"AIza[0-9A-Za-z_\-]{20,}",
    "hex secret assigned to a key-named variable": (
        r"(?i)[A-Za-z_][A-Za-z0-9_]*(?:key|token|secret|password)[A-Za-z0-9_]*"
        r"\s*[:=]\s*[\"'][0-9a-fA-F]{32,}[\"']"
    ),
}

#: The three environment variables the system reads. Every one is optional --
#: each declines gracefully -- which is itself a documented fact (R13).
REQUIRED_ENV_VARS = ("ANTHROPIC_API_KEY", "CENSUS_API_KEY", "GOOGLE_MAPS_KEY")

#: The entry points a reviewer is told to run, in the order the README runs
#: them. `test-py` / `test-web` / `clean` exist too but are not part of the
#: documented path, so the README is free to omit them.
REQUIRED_MAKE_TARGETS = ("setup", "pipeline", "eval", "serve", "test")

#: What `make pipeline` publishes. "where the published artifacts land" is an
#: acceptance criterion, so each path is checked by name.
PUBLISHED_ARTIFACTS = (
    "data/doors.geojson",
    "data/houseaccount.sqlite",
    "data/run_manifest.json",
    "data/territory.geojson",
)

#: The documented order. Each entry is (what it is, how to recognise its first
#: mention). Recognisers, not headings: a README may call its first section
#: "Prerequisites" or "Requirements" and satisfy the criterion either way.
README_ORDER = (
    ("prerequisites", r"prerequisit|requirements"),
    ("make setup", r"make\s+setup"),
    ("the environment variables", r"ANTHROPIC_API_KEY"),
    ("make pipeline", r"make\s+pipeline"),
    ("make eval", r"make\s+eval"),
    ("make serve", r"make\s+serve"),
    ("make test", r"make\s+test(?![\w-])"),
    ("where the published artifacts land", r"data/doors\.geojson"),
)

#: Every upstream the pipeline actually calls, and how the README may name it.
DATA_SOURCES = (
    ("NJ parcels / MOD-IV", r"parcel|MOD-?IV"),
    ("NJ construction permits", r"permit"),
    ("Census ACS block groups", r"\bACS\b|census"),
    ("NJ orthophotography / Street View", r"orthophot|imagery|street\s*view"),
)

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


# --- helpers -----------------------------------------------------------------


def _is_vendored_or_binary(path):
    if path.suffix.lower() in SKIPPED_SUFFIXES:
        return True
    return path.name.endswith((".min.js", ".min.css", ".lock"))


def scan_for_secrets(roots, patterns=SECRET_PATTERNS):
    """Return (hits, files_scanned) for `roots`.

    A hit is ``(path, line_number, pattern_name)``. Undecodable files are
    treated as binary and skipped.
    """
    hits, scanned = [], []
    compiled = [(name, re.compile(pattern)) for name, pattern in patterns.items()]
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
                hits += [(str(path), lineno, name) for name, rx in compiled if rx.search(line)]
    return hits, scanned


def makefile_targets(text):
    """Target names defined in a Makefile. `VAR := value` is not a target."""
    found = set(re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", text, re.MULTILINE))
    return found - {"PHONY"}


def code_regions(markdown):
    """Fenced blocks, inline code spans and headings.

    `make <target>` is only counted inside these. Prose says "make sure the
    venv exists" and that is not a claim about a Makefile target.
    """
    regions = re.findall(r"```[A-Za-z0-9]*\n(.*?)```", markdown, re.DOTALL)
    regions += re.findall(r"`([^`\n]+)`", markdown)
    regions += re.findall(r"^#{1,6}\s+(.*)$", markdown, re.MULTILINE)
    return regions


def readme_make_targets(markdown):
    """Every `make <target>` the README tells a reviewer to run."""
    targets = set()
    for region in code_regions(markdown):
        targets |= set(re.findall(r"\bmake\s+([a-z][a-z0-9_-]*)", region))
    return targets


def env_example_vars(text):
    """Variables declared in a dotenv-style file, in file order."""
    return [m.group(1) for m in re.finditer(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", text, re.MULTILINE)]


def readme_env_vars(markdown, declared=()):
    """Environment variables the README names.

    Two ways in: anything declared in `.env.example` that the README mentions,
    plus any SCREAMING_SNAKE token with a credential-shaped suffix. The second
    is what catches a README that documents a variable nobody put in
    `.env.example`; the first is what keeps a differently-suffixed variable
    (`HOUSEACCOUNT_CACHE_DIR`) from being missed.
    """
    tokens = set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", markdown))
    shaped = {t for t in tokens if t.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))}
    return shaped | (tokens & set(declared))


def first_index(text, pattern, what):
    match = re.search(pattern, text, re.IGNORECASE)
    assert match, f"README documents nothing recognisable as {what} (/{pattern}/)"
    return match.start()


@pytest.fixture
def readme_text():
    assert README.is_file(), "R13: the repo must ship a README.md"
    return README.read_text(encoding="utf-8")


@pytest.fixture
def env_example_text():
    assert ENV_EXAMPLE.is_file(), "R13: the repo must ship a .env.example"
    return ENV_EXAMPLE.read_text(encoding="utf-8")


@pytest.fixture
def makefile_text():
    return MAKEFILE.read_text(encoding="utf-8")


# --- no secrets in the shipped source (R13) ---------------------------------


def test_no_secret_shaped_literal_in_shipped_source():
    hits, _ = scan_for_secrets(REPO_ROOT / root for root in SCANNED_ROOTS)
    assert hits == [], "a credential-shaped literal must never be committed"


def test_the_secret_scan_actually_reads_files():
    """A guard that silently scans nothing would pass forever."""
    _, scanned = scan_for_secrets(REPO_ROOT / root for root in SCANNED_ROOTS)
    assert len(scanned) > 0


@pytest.mark.parametrize(
    "name, body",
    [
        ("anthropic api key", 'CLIENT = Anthropic(api_key="sk-ant-api03-AbCd1234EfGh5678IjKl")\n'),
        ("google api key", 'STREET_VIEW_KEY = "AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv"\n'),
        (
            "hex secret assigned to a key-named variable",
            'SIGNING_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef"\n',
        ),
    ],
)
def test_the_secret_scan_catches_each_shape(tmp_path, name, body):
    """The meta-test: the guard must be able to bite."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "leak.py").write_text(body, encoding="utf-8")

    hits, _ = scan_for_secrets([tmp_path / "src"])
    assert [(Path(p).name, n, kind) for p, n, kind in hits] == [("leak.py", 1, name)]


def test_the_secret_scan_does_not_bite_on_env_lookups_or_placeholders(tmp_path):
    """Reading a key from the environment is the correct pattern, and must
    stay green forever or the guard gets muted."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text(
        'import os\n'
        'ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")\n'
        'CENSUS_API_KEY = ""\n'
        'CACHE_KEY = hashlib.sha256(raw).hexdigest()\n',
        encoding="utf-8",
    )

    hits, _ = scan_for_secrets([tmp_path / "src"])
    assert hits == []


def test_the_secret_scan_covers_the_web_and_eval_trees(tmp_path):
    for root, name, body in [
        ("web", "config.js", 'const key = "AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv";\n'),
        ("eval", "run.py", 'TOKEN = "sk-ant-api03-ZzYyXxWwVvUuTtSs"\n'),
    ]:
        (tmp_path / root).mkdir()
        (tmp_path / root / name).write_text(body, encoding="utf-8")

    hits, _ = scan_for_secrets([tmp_path / "web", tmp_path / "eval"])
    assert sorted(kind for _, _, kind in hits) == [
        "anthropic api key",
        "google api key",
    ]


# --- .env.example (R13) ------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_ENV_VARS)
def test_env_example_declares_each_variable_with_an_empty_value(env_example_text, name):
    match = re.search(rf"^\s*{name}\s*=(.*)$", env_example_text, re.MULTILINE)
    assert match, f"{name} is missing from .env.example"
    assert match.group(1).strip() == "", f"{name} must ship with an empty value, never a key"


@pytest.mark.parametrize("name", REQUIRED_ENV_VARS)
def test_env_example_annotates_each_variable(env_example_text, name):
    """One line each, saying what it buys and that it is optional."""
    lines = env_example_text.splitlines()
    index = next(i for i, line in enumerate(lines) if re.match(rf"^\s*{name}\s*=", line))
    preceding = [line for line in lines[:index] if line.strip()]
    assert preceding and preceding[-1].lstrip().startswith(
        "#"
    ), f"{name} has no explanatory comment line above it"


def test_env_example_holds_no_real_values(env_example_text):
    for name, pattern in SECRET_PATTERNS.items():
        assert not re.search(pattern, env_example_text), f"{name} committed in .env.example"


# --- README <-> Makefile (R13) ----------------------------------------------


@pytest.mark.parametrize("target", REQUIRED_MAKE_TARGETS)
def test_readme_documents_each_required_make_target(readme_text, target):
    assert target in readme_make_targets(readme_text), f"README never tells anyone to run make {target}"


def test_every_make_target_named_in_the_readme_exists(readme_text, makefile_text):
    """The real repo, both files. A README that names a target the Makefile
    does not define is a broken quickstart."""
    named = readme_make_targets(readme_text)
    defined = makefile_targets(makefile_text)

    assert named, "the README names no make targets at all"
    assert named <= defined, f"README names undefined targets: {sorted(named - defined)}"


def test_the_makefile_defines_the_required_targets(makefile_text):
    assert set(REQUIRED_MAKE_TARGETS) <= makefile_targets(makefile_text)


def test_the_target_check_catches_a_readme_naming_a_missing_target():
    """Meta: the consistency check must be able to bite."""
    named = readme_make_targets("Run `make deploy` to ship it.\n")
    defined = makefile_targets("setup:\n\techo hi\n")

    assert named == {"deploy"}
    assert not named <= defined


def test_the_target_check_ignores_english_prose():
    """`make sure` is not a Makefile target, and a guard that says it is gets
    deleted rather than fixed."""
    assert readme_make_targets("Make sure you run make setup first.\n") == set()


# --- README <-> .env.example (R13) ------------------------------------------


def test_every_env_var_named_in_the_readme_is_in_env_example(readme_text, env_example_text):
    declared = env_example_vars(env_example_text)
    documented = readme_env_vars(readme_text, declared)

    assert documented, "the README names no environment variables"
    missing = sorted(documented - set(declared))
    assert not missing, f"documented in the README but absent from .env.example: {missing}"


def test_every_env_var_in_env_example_is_named_in_the_readme(readme_text, env_example_text):
    declared = env_example_vars(env_example_text)

    assert declared, ".env.example declares nothing"
    undocumented = [name for name in declared if name not in readme_text]
    assert not undocumented, f"in .env.example but undocumented: {undocumented}"


def test_env_example_declares_exactly_the_three_known_variables(env_example_text):
    assert set(env_example_vars(env_example_text)) == set(REQUIRED_ENV_VARS)


def test_the_env_var_check_catches_a_readme_only_variable():
    """Meta: both directions of the consistency check must be able to bite."""
    declared = env_example_vars("ANTHROPIC_API_KEY=\n")
    documented = readme_env_vars(
        "Set `ANTHROPIC_API_KEY` and `MAPBOX_ACCESS_TOKEN`.\n", declared
    )

    assert documented - set(declared) == {"MAPBOX_ACCESS_TOKEN"}


def test_the_env_var_check_catches_an_undocumented_declared_variable():
    declared = env_example_vars("ANTHROPIC_API_KEY=\nCENSUS_API_KEY=\n")
    readme = "Set `ANTHROPIC_API_KEY` before running the pipeline.\n"

    assert [name for name in declared if name not in readme] == ["CENSUS_API_KEY"]


def test_readme_says_the_env_vars_are_optional(readme_text):
    """All three decline gracefully; a reviewer who thinks a key is required
    stops at `make setup`."""
    start = first_index(readme_text, r"ANTHROPIC_API_KEY", "the environment variables")
    window = readme_text[start : start + 2000]
    assert re.search(r"optional", window, re.IGNORECASE), (
        "the env-var section must say which variables are optional"
    )


# --- README structure (R13) --------------------------------------------------


def test_readme_documents_the_run_path_in_order(readme_text):
    positions = [(what, first_index(readme_text, pattern, what)) for what, pattern in README_ORDER]

    order = [what for what, _ in positions]
    assert order == [what for what, _ in sorted(positions, key=lambda item: item[1])], (
        "README documents the run path out of order: "
        f"{[what for what, _ in sorted(positions, key=lambda item: item[1])]}"
    )


@pytest.mark.parametrize("artifact", PUBLISHED_ARTIFACTS)
def test_readme_says_where_each_published_artifact_lands(readme_text, artifact):
    assert artifact in readme_text


@pytest.mark.parametrize("source, pattern", DATA_SOURCES)
def test_readme_names_each_data_source(readme_text, source, pattern):
    assert re.search(pattern, readme_text, re.IGNORECASE), f"{source} is undocumented"


def test_readme_states_the_licence_or_tos_position(readme_text):
    assert re.search(
        r"licen[cs]e|terms of (use|service)|\bToS\b|public domain", readme_text, re.IGNORECASE
    ), "the data sources ship without their licence/ToS position"


def test_readme_links_the_data_and_ethics_page(readme_text):
    assert "ethics.html" in readme_text
    assert (REPO_ROOT / "web" / "ethics.html").is_file(), "the linked page must exist"


def test_readme_records_the_geopandas_deviation(readme_text):
    """PRD R12 named GeoPandas; the build uses shapely. A deliberate deviation
    that is not written down is indistinguishable from a mistake."""
    for token in ("GeoPandas", "shapely", "R12"):
        assert re.search(re.escape(token), readme_text, re.IGNORECASE), (
            f"the R12 deviation record does not mention {token}"
        )


# --- cost report (R14) -------------------------------------------------------


def test_readme_states_the_api_budget(readme_text):
    assert re.search(r"\$\s*50", readme_text), "the $50 budget ceiling is undocumented"
    assert re.search(r"project(ed|ion)", readme_text, re.IGNORECASE), (
        "the projected spend is undocumented"
    )


def test_readme_points_at_the_cost_per_door_line(readme_text):
    """R14: the projected spend is checkable against a number the tool prints."""
    match = re.search(r"cost[\s-]*per[\s-]*door", readme_text, re.IGNORECASE)
    assert match, "the README never mentions the cost-per-door line"

    around = readme_text[max(0, match.start() - 600) : match.end() + 600]
    assert re.search(r"make\s+eval", around), (
        "the cost-per-door mention must point at `make eval`, which prints it"
    )
