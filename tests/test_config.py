"""Config loading (T001).

Every key is optional: this environment has none of them set, and the pipeline
must still import and run. Nothing here may depend on the ambient environment —
each test states the environment it wants via monkeypatch.
"""

from pathlib import Path

import pytest

from houseaccount.config import Config

KEY_ENV_VARS = [
    ("OPENAI_API_KEY", "openai_api_key"),
    ("CENSUS_API_KEY", "census_api_key"),
    ("GOOGLE_MAPS_KEY", "google_maps_key"),
]

PATH_ATTRS = ["repo_root", "cache_dir", "data_dir"]


@pytest.fixture
def clean_env(monkeypatch):
    """No API keys set at all — the real state of this machine."""
    for env_var, _ in KEY_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    return monkeypatch


@pytest.mark.parametrize("env_var,attr", KEY_ENV_VARS)
def test_key_is_read_from_environment(clean_env, env_var, attr):
    clean_env.setenv(env_var, f"sentinel-for-{env_var}")
    assert getattr(Config.from_env(), attr) == f"sentinel-for-{env_var}"


@pytest.mark.parametrize("env_var,attr", KEY_ENV_VARS)
def test_absent_key_is_none_and_never_raises(clean_env, env_var, attr):
    assert getattr(Config.from_env(), attr) is None


def test_from_env_succeeds_with_no_keys_at_all(clean_env):
    config = Config.from_env()
    for _, attr in KEY_ENV_VARS:
        assert getattr(config, attr) is None


def test_keys_are_independent(clean_env):
    """Setting one key must not leak into the others."""
    clean_env.setenv("CENSUS_API_KEY", "census-only")
    config = Config.from_env()
    assert config.census_api_key == "census-only"
    assert config.openai_api_key is None
    assert config.google_maps_key is None


@pytest.mark.parametrize("attr", PATH_ATTRS)
def test_exposes_absolute_paths(clean_env, attr):
    value = getattr(Config.from_env(), attr)
    assert isinstance(value, Path)
    assert value.is_absolute()


def test_repo_root_locates_the_real_repository(clean_env):
    """repo_root must be discovered, not guessed from the cwd."""
    repo_root = Config.from_env().repo_root
    assert (repo_root / "pyproject.toml").is_file()
    assert (repo_root / "src" / "houseaccount" / "__init__.py").is_file()


def test_repo_root_is_stable_regardless_of_cwd(clean_env, tmp_path, monkeypatch):
    before = Config.from_env().repo_root
    monkeypatch.chdir(tmp_path)
    assert Config.from_env().repo_root == before


@pytest.mark.parametrize("attr", ["cache_dir", "data_dir"])
def test_data_paths_live_inside_the_repo(clean_env, attr):
    config = Config.from_env()
    directory = getattr(config, attr)
    assert directory.is_relative_to(config.repo_root)
    assert directory != config.repo_root


def test_cache_dir_and_data_dir_are_distinct(clean_env):
    config = Config.from_env()
    assert config.cache_dir != config.data_dir


def test_config_reads_keys_only_from_the_environment(clean_env):
    """The only way a key can enter the process is os.environ."""
    import houseaccount.config as config_module

    source = Path(config_module.__file__).read_text()
    assert "environ" in source or "getenv" in source

    # A key set purely inside the process (no environment entry) stays absent —
    # there is no baked-in default to fall back on.
    for _, attr in KEY_ENV_VARS:
        assert getattr(Config.from_env(), attr) is None


# Shapes that a real credential takes; none may be hardcoded anywhere in src/.
SECRET_LITERAL_MARKERS = ["sk-proj-", "sk-ant-", "sk_live_", "AIzaSy", "ghp_", "xoxb-"]


def test_no_credential_literals_anywhere_in_src():
    src_root = Config.from_env().repo_root / "src"
    offenders = []
    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        offenders += [f"{path}: {m}" for m in SECRET_LITERAL_MARKERS if m in text]
    assert offenders == []
