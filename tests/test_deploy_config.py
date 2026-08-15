"""Deploy readiness: the three config files and the runbook (T016, R12).

R12 puts the server on Fly.io and the Map UI on Vercel. Nobody on this side of
the credential boundary can run `flyctl deploy`, so what "done" means here is
that everything a human needs is *committed and correct*: an image that builds
the server, a Fly config that exposes it on a known port with a liveness probe,
a Vercel config that serves `web/` and tells it where the API lives, and a
runbook that names the exact commands in order and says plainly which of them
need someone's credentials.

**These tests parse, they do not grep.** `tomllib` reads `fly.toml` and `json`
reads `vercel.json`, because a regex over TOML passes on a key that is commented
out and fails on one that is merely reformatted. The Dockerfile has no stdlib
parser, so it gets a structural read of its instructions instead — still not a
substring match on the whole file.

**The UI is not hardcoded to localhost.** `web/js/map.js` reads
`window.HOUSEACCOUNT_API_BASE` and `web/ethics.html` reads
`window.HOUSEACCOUNT_ARTIFACT_BASE`; a static host that resolves neither shows
an empty map and the ethics page's "no published run" fallback. So the Vercel
config has to carry both, and both have to come *from environment variables* —
a literal origin in the repo is a config change that needs a commit.

**No secret values anywhere.** A standing scan of all four files for
credential-shaped literals, with the meta-tests that prove it can bite, on the
pattern of `tests/test_redaction.py` and `tests/test_repro.py`. A guard that
scans nothing looks exactly like a clean tree.

`docker build` is deliberately not run here: it is slow and Docker may not be
installed. The optional check at the bottom is skipped by default and exists so
a human can run it by hand.
"""

import json
import os
import re
import tomllib
from pathlib import Path

import pytest

from houseaccount.server.app import MCP_PATH

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCKERFILE = REPO_ROOT / "Dockerfile"
FLY_TOML = REPO_ROOT / "fly.toml"
VERCEL_JSON = REPO_ROOT / "vercel.json"
DEPLOY_MD = REPO_ROOT / "docs" / "DEPLOY.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MAKEFILE = REPO_ROOT / "Makefile"

#: The static site Vercel serves. Where the MCP endpoint is advertised to a
#: reviewer, and therefore where a wrong hostname is published.
WEB_DIR = REPO_ROOT / "web"

#: Every file this ticket ships. All four are scanned for secrets.
DEPLOY_FILES = (DOCKERFILE, FLY_TOML, VERCEL_JSON, DEPLOY_MD)

#: The port the server binds inside the container, and the port Fly routes to.
INTERNAL_PORT = 8000

#: The liveness path `houseaccount.server.api` already serves.
HEALTH_PATH = "/health"

#: The ASGI factory `make serve` and the container both boot.
APP_FACTORY = "houseaccount.server.app:create_app"

#: The application credentials, which are `flyctl secrets`, never `[env]` values.
APP_CREDENTIALS = ("OPENAI_API_KEY", "CENSUS_API_KEY", "GOOGLE_MAPS_KEY")

#: The two build-time variables the UI reads through `window.*`. Both must be
#: injected from the environment: `map.js` defaults to a same-origin `/api` and
#: `ethics.html` defaults to `..`, and neither resolves on a static host.
UI_BASE_VARS = ("HOUSEACCOUNT_API_BASE", "HOUSEACCOUNT_ARTIFACT_BASE")

#: Secret shapes, by the name reported when one is found. Same three shapes
#: `tests/test_repro.py` scans the source tree for; duplicated rather than
#: imported so this guard stands on its own.
SECRET_PATTERNS = {
    "openai api key": r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}",
    "google api key": r"AIza[0-9A-Za-z_\-]{20,}",
    "hex secret assigned to a key-named variable": (
        r"(?i)[A-Za-z_][A-Za-z0-9_]*(?:key|token|secret|password)[A-Za-z0-9_]*"
        r"\s*[:=]\s*[\"']?[0-9a-fA-F]{32,}[\"']?"
    ),
}


# --- helpers -----------------------------------------------------------------


def read(path):
    assert path.is_file(), f"R12: the repo must ship {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def dockerfile_instructions(text):
    """`[(INSTRUCTION, argument), ...]`, comments dropped, continuations joined."""
    instructions, buffer = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or (not buffer and line.startswith("#")):
            continue
        buffer = f"{buffer} {line}" if buffer else line
        if buffer.endswith("\\"):
            buffer = buffer[:-1].strip()
            continue
        head, _, argument = buffer.partition(" ")
        instructions.append((head.upper(), argument.strip()))
        buffer = ""
    if buffer:
        head, _, argument = buffer.partition(" ")
        instructions.append((head.upper(), argument.strip()))
    return instructions


def arguments(instructions, *names):
    wanted = {name.upper() for name in names}
    return [argument for head, argument in instructions if head in wanted]


def walk_tables(value, trail=()):
    """Every mapping in a parsed config, with the key trail that reached it."""
    if isinstance(value, dict):
        yield trail, value
        for key, child in value.items():
            yield from walk_tables(child, trail + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_tables(child, trail + (str(index),))


def scan_for_secrets(paths, patterns=SECRET_PATTERNS):
    """Return `(hits, files_scanned)`; a hit is `(path, line_number, name)`."""
    hits, scanned = [], []
    compiled = [(name, re.compile(pattern)) for name, pattern in patterns.items()]
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned.append(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            hits += [(str(path), lineno, name) for name, rx in compiled if rx.search(line)]
    return hits, scanned


def first_index(text, pattern, what):
    match = re.search(pattern, text, re.IGNORECASE)
    assert match, f"docs/DEPLOY.md documents nothing recognisable as {what} (/{pattern}/)"
    return match.start()


def assert_in_order(text, steps):
    positions = [(what, first_index(text, pattern, what)) for what, pattern in steps]
    ordered = [what for what, _ in sorted(positions, key=lambda item: item[1])]
    assert [what for what, _ in positions] == ordered, f"documented out of order: {ordered}"


def makefile_targets(text):
    found = set(re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", text, re.MULTILINE))
    return found - {"PHONY"}


def named_make_targets(markdown):
    regions = re.findall(r"```[A-Za-z0-9]*\n(.*?)```", markdown, re.DOTALL)
    regions += re.findall(r"`([^`\n]+)`", markdown)
    targets = set()
    for region in regions:
        targets |= set(re.findall(r"\bmake\s+([a-z][a-z0-9_-]*)", region))
    return targets


@pytest.fixture
def dockerfile():
    return dockerfile_instructions(read(DOCKERFILE))


@pytest.fixture
def fly_config():
    return tomllib.loads(read(FLY_TOML))


@pytest.fixture
def vercel_config():
    return json.loads(read(VERCEL_JSON))


@pytest.fixture
def deploy_md():
    return read(DEPLOY_MD)


# --- Dockerfile ---------------------------------------------------------------


def test_the_image_is_built_on_a_python_312_base(dockerfile):
    """The venv is 3.12; an image on another minor is a different interpreter."""
    bases = arguments(dockerfile, "FROM")
    assert bases, "the Dockerfile declares no base image"
    assert bases[0].lower().startswith("python:3.12"), bases


def test_the_image_installs_the_project_from_pyproject(dockerfile):
    """Dependencies come from the one manifest, not a hand-copied pip line."""
    copied = " ".join(arguments(dockerfile, "COPY", "ADD"))
    installs = " ".join(arguments(dockerfile, "RUN"))

    assert "pyproject.toml" in copied, "pyproject.toml is never copied into the image"
    assert re.search(r"pip[^\n]*install", installs), "nothing installs the project"


def test_the_image_carries_the_source_and_the_published_run(dockerfile):
    """The server reads `data/`; an image without it boots into DataUnavailable."""
    copied = arguments(dockerfile, "COPY", "ADD")

    assert any(re.search(r"(^|[\s/])src\b", argument) for argument in copied), copied
    assert any(re.search(r"(^|[\s/])data\b", argument) for argument in copied), copied


def test_the_container_runs_uvicorn_on_the_port_variable(dockerfile):
    """Fly injects `$PORT`; the default is 8000 so a bare `docker run` works."""
    text = read(DOCKERFILE)
    command = " ".join(arguments(dockerfile, "CMD", "ENTRYPOINT"))

    assert "uvicorn" in command, command
    assert APP_FACTORY in command and "--factory" in command, command
    assert "0.0.0.0" in command, "binding to loopback makes the container unreachable"
    assert re.search(r"\$\{?PORT\b", command), f"the port is hardcoded: {command}"
    assert re.search(r"\bPORT[:=][-=]?\s*\"?8000", text) or "8000" in command, (
        "$PORT has no 8000 default"
    )


def test_the_container_does_not_run_as_root(dockerfile):
    """A web process that is root in its own image is one escape from the host."""
    users = [argument for head, argument in dockerfile if head == "USER"]
    assert users, "the Dockerfile never drops out of root"
    assert users[-1].split()[0].strip('"') not in {"root", "0"}, users

    created = " ".join(arguments(dockerfile, "RUN"))
    assert re.search(r"adduser|useradd|addgroup|groupadd", created), (
        "USER names an account the image never creates"
    )


def test_the_image_bakes_in_no_credential_defaults(dockerfile):
    """`ENV OPENAI_API_KEY=...` would ship a key inside the layer."""
    declared = " ".join(arguments(dockerfile, "ENV", "ARG"))
    for name in APP_CREDENTIALS:
        assert not re.search(rf"\b{name}\s*=\s*\S", declared), f"{name} has a baked-in value"


@pytest.mark.skipif(
    os.environ.get("HOUSEACCOUNT_DOCKER_BUILD") != "1",
    reason="slow, and Docker may not be installed; set HOUSEACCOUNT_DOCKER_BUILD=1 to run",
)
def test_the_image_actually_builds():
    """Opt-in only. The structural tests above are the standing guard."""
    import subprocess

    result = subprocess.run(
        ["docker", "build", "-t", "houseaccount:test", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-4000:]


# --- fly.toml -----------------------------------------------------------------


def test_fly_toml_parses_and_names_the_app(fly_config):
    name = fly_config.get("app")
    assert isinstance(name, str) and name.strip(), "fly.toml declares no app name"


def test_fly_routes_to_the_internal_port_the_server_binds(fly_config):
    ports = [
        table["internal_port"] for _, table in walk_tables(fly_config) if "internal_port" in table
    ]
    assert ports, "fly.toml declares no internal_port"
    assert INTERNAL_PORT in ports, ports


def test_fly_health_checks_the_health_endpoint_over_http(fly_config):
    checks = [
        (trail, table)
        for trail, table in walk_tables(fly_config)
        if table.get("path") == HEALTH_PATH
    ]
    assert checks, f"no health check on {HEALTH_PATH}"
    assert any(
        "http" in " ".join(trail).lower()
        or str(table.get("type", "")).lower() == "http"
        or str(table.get("protocol", "")).lower() == "http"
        for trail, table in checks
    ), f"the {HEALTH_PATH} check is not an HTTP check: {checks}"


def test_fly_declares_no_credentials_in_its_env_table(fly_config):
    """`[env]` is baked into the image config; credentials are `flyctl secrets`."""
    env = fly_config.get("env", {})
    assert isinstance(env, dict), env

    leaked = [key for key in env if re.search(r"key|token|secret|password", key, re.IGNORECASE)]
    assert leaked == [], f"credential-named keys in [env]: {leaked}"
    assert not set(env) & set(APP_CREDENTIALS), sorted(set(env) & set(APP_CREDENTIALS))


def test_fly_names_its_secrets_and_how_they_are_set(fly_config):
    """Named, so a deployer knows what to set; valueless, so nothing leaks."""
    text = read(FLY_TOML)
    assert re.search(r"\bfly(ctl)?\s+secrets\s+set\b", text), (
        "fly.toml never says how the secrets are set"
    )
    for name in APP_CREDENTIALS:
        assert name in text, f"{name} is undocumented in fly.toml"


# --- vercel.json --------------------------------------------------------------


def test_vercel_serves_the_web_directory_statically(vercel_config):
    assert vercel_config.get("outputDirectory") == "web", vercel_config


def test_vercel_runs_a_build_step_that_can_inject_config(vercel_config):
    command = vercel_config.get("buildCommand")
    assert isinstance(command, str) and command.strip(), (
        "without a build step nothing can inject the API base"
    )


@pytest.mark.parametrize("name", UI_BASE_VARS)
def test_vercel_takes_each_ui_base_from_an_environment_variable(vercel_config, name):
    """`$NAME` / `${NAME}` — a literal origin here is a config change by commit."""
    text = json.dumps(vercel_config)
    assert re.search(rf"\$\{{?{name}\b", text), f"{name} is not read from the environment: {text}"


def test_vercel_hardcodes_no_localhost_origin(vercel_config):
    text = json.dumps(vercel_config)
    assert not re.search(r"localhost|127\.0\.0\.1", text), (
        "the deployed UI would call a machine that is not there"
    )


def test_the_injected_variables_are_the_ones_the_ui_actually_reads():
    """Anti-drift: rename a global in `web/` and this goes red, not the deploy."""
    sources = (REPO_ROOT / "web" / "ethics.html", REPO_ROOT / "web" / "js" / "map.js")
    read_by_ui = set()
    for path in sources:
        read_by_ui |= set(
            re.findall(r"window\.(HOUSEACCOUNT_[A-Z0-9_]+)", path.read_text(encoding="utf-8"))
        )

    assert read_by_ui == set(UI_BASE_VARS), sorted(read_by_ui)
    text = read(VERCEL_JSON)
    assert all(name in text for name in read_by_ui), sorted(read_by_ui)


# --- docs/DEPLOY.md -----------------------------------------------------------


def test_deploy_md_orders_the_fly_deploy(deploy_md):
    assert_in_order(
        deploy_md,
        (
            ("the flyctl login", r"fly(ctl)?\s+auth\s+login"),
            ("setting the secrets", r"fly(ctl)?\s+secrets\s+set"),
            ("the deploy itself", r"fly(ctl)?\s+deploy"),
        ),
    )


def test_deploy_md_orders_the_vercel_deploy(deploy_md):
    assert_in_order(
        deploy_md,
        (
            ("the vercel login", r"vercel\s+login"),
            ("setting the UI base variables", r"vercel\s+env\s+add"),
            ("the production deploy", r"vercel\s+(deploy\s+)?--prod"),
        ),
    )


def test_deploy_md_says_which_steps_need_human_credentials(deploy_md):
    """The ticket stops at this boundary; the runbook has to say where it is."""
    heading = re.search(
        r"^#{1,6}\s+.*(credential|human|manual|by hand).*$", deploy_md, re.IGNORECASE | re.MULTILINE
    )
    assert heading, "DEPLOY.md has no section marking the credential boundary"

    for command in (r"fly(ctl)?\s+auth\s+login", r"vercel\s+login"):
        assert re.search(command, deploy_md), f"/{command}/ is undocumented"


def test_deploy_md_names_every_application_credential(deploy_md):
    """The same three variables `.env.example` declares — one list, two files."""
    declared = set(
        re.findall(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", read(ENV_EXAMPLE), re.MULTILINE)
    )
    assert declared == set(APP_CREDENTIALS), sorted(declared)

    missing = [name for name in declared if name not in deploy_md]
    assert not missing, f"declared in .env.example but absent from DEPLOY.md: {missing}"


@pytest.mark.parametrize("name", UI_BASE_VARS)
def test_deploy_md_names_each_ui_base_variable(deploy_md, name):
    assert name in deploy_md, f"{name} is set at deploy time but undocumented"


def test_every_make_target_named_in_deploy_md_exists(deploy_md):
    """Consistent with `tests/test_repro.py`: a runbook naming a missing target
    is a broken runbook."""
    defined = makefile_targets(read(MAKEFILE))
    named = named_make_targets(deploy_md)

    assert named <= defined, f"DEPLOY.md names undefined targets: {sorted(named - defined)}"


# --- the advertised MCP endpoint (T024) ---------------------------------------
#
# The ethics page publishes the MCP endpoint a reviewer is expected to call, and
# it published `https://houseaccount-mcp.fly.dev/mcp` — an app no step in
# `docs/DEPLOY.md` ever creates, on a run whose `fly.toml` declares one app named
# `houseaccount` and whose server already mounts the MCP transport at `/mcp`.
# After a by-the-book deploy that hostname does not resolve.
#
# So none of this is written down twice here. The host comes from `fly.toml`, the
# path comes from the module that mounts it, and the page is checked against the
# two — which is what the ticket asks for: the page and the deploy config cannot
# disagree without a test failing, whichever of them moves.


def web_sources():
    """Every file `web/` ships to a browser. Small tree; no build output in it."""
    return tuple(
        path
        for path in sorted(WEB_DIR.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".html", ".js", ".css", ".json"}
    )


def advertised_mcp_urls():
    """`(file, url)` for every MCP endpoint the shipped web files advertise."""
    found = []
    for path in web_sources():
        text = path.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s\"'`<>)\\]+", text):
            if url.rstrip("/").endswith(MCP_PATH):
                found.append((path.relative_to(REPO_ROOT).as_posix(), url))
    return found


def fly_hostnames(text):
    """Every `<app>.fly.dev` host named in a blob of text, as app names."""
    return re.findall(r"([A-Za-z0-9][A-Za-z0-9-]*)\.fly\.dev", text)


def test_no_web_file_advertises_an_absolute_mcp_endpoint():
    """The host is no longer a fact this repo knows.

    Two topologies ship from this tree — Railway serving the page and the
    transport from one origin, Fly + Vercel serving them from two — so any
    absolute URL burned into `web/` is wrong for one of them. The page resolves
    the endpoint at runtime instead; see the test below.
    """
    assert advertised_mcp_urls() == [], (
        "an absolute MCP endpoint is a hostname that rots on the other topology"
    )


def test_the_web_ui_derives_the_mcp_endpoint_from_the_api_base():
    """The replacement contract, and the guard that the tests here still bite.

    `HOUSEACCOUNT_API_BASE` names whichever server answers the API — an absolute
    origin when the UI is hosted apart from it, a same-origin `/api` when it is
    not — and the transport is mounted at `MCP_PATH` on that same server. So the
    page has to resolve one against the other, and `MCP_PATH` is the app's own
    constant here, which is what moves the page if the transport is remounted.
    """
    markup = (WEB_DIR / "ethics.html").read_text(encoding="utf-8")

    assert "HOUSEACCOUNT_API_BASE" in markup, "the page derives the endpoint from nothing"
    assert f"'{MCP_PATH}'" in markup or f'"{MCP_PATH}"' in markup, (
        f"the page never resolves {MCP_PATH}, the path the server mounts"
    )


def test_no_web_file_names_a_fly_app_the_deploy_never_creates(fly_config):
    """Catches the half-finished edit: one corrected URL and a stale one beside it."""
    declared = fly_config["app"]
    stray = sorted(
        {
            (path.relative_to(REPO_ROOT).as_posix(), host)
            for path in web_sources()
            for host in fly_hostnames(path.read_text(encoding="utf-8"))
            if host != declared
        }
    )
    assert stray == [], f"fly.toml declares only {declared!r}"


def test_the_runbook_verifies_the_origin_the_fly_deploy_creates(deploy_md, fly_config):
    """The Fly path still names one host, and the runbook still has to curl it."""
    origin = f"https://{fly_config['app']}.fly.dev"
    assert origin in deploy_md, f"the runbook never verifies {origin}"


# --- no secret values anywhere (R12/R13) --------------------------------------


def test_no_secret_shaped_literal_in_any_deploy_file():
    hits, _ = scan_for_secrets(DEPLOY_FILES)
    assert hits == [], "a credential-shaped literal must never be committed"


def test_the_deploy_secret_scan_reads_every_deploy_file():
    """A guard that silently scans nothing would pass forever."""
    _, scanned = scan_for_secrets(DEPLOY_FILES)
    assert sorted(path.name for path in scanned) == sorted(path.name for path in DEPLOY_FILES)


@pytest.mark.parametrize(
    "name, body",
    [
        ("openai api key", 'ENV OPENAI_API_KEY="sk-proj-AbCd1234EfGh5678IjKlMnOp"\n'),
        ("google api key", 'GOOGLE_MAPS_KEY = "AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv"\n'),
        (
            "hex secret assigned to a key-named variable",
            'FLY_API_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef"\n',
        ),
    ],
)
def test_the_deploy_secret_scan_catches_each_shape(tmp_path, name, body):
    """The meta-test: the guard must be able to bite."""
    leak = tmp_path / "fly.toml"
    leak.write_text(body, encoding="utf-8")

    hits, _ = scan_for_secrets([leak])
    assert [(Path(path).name, lineno, kind) for path, lineno, kind in hits] == [
        ("fly.toml", 1, name)
    ]


def test_the_deploy_secret_scan_does_not_bite_on_named_secrets(tmp_path):
    """Naming a secret is the correct pattern; only values are forbidden."""
    clean = tmp_path / "fly.toml"
    clean.write_text(
        "# flyctl secrets set OPENAI_API_KEY=... CENSUS_API_KEY=...\n"
        "[env]\n"
        'HOUSEACCOUNT_API_BASE = "$HOUSEACCOUNT_API_BASE"\n',
        encoding="utf-8",
    )

    hits, _ = scan_for_secrets([clean])
    assert hits == []
