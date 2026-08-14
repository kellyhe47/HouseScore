"""Content-addressed on-disk cache (T001).

The cache is the cost-discipline mechanism (PRD R2.3): a warm cache means a
re-run costs nothing and reproduces exactly. So the key must be a *content*
address — stable across processes, independent of dict ordering, and sensitive
to any change in the request.

Every test writes into `tmp_path`; the repo's real `cache/` is never touched.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from houseaccount.cache import Cache
from houseaccount.config import Config

URL = "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/Parcels/0/query"
PARAMS = {"where": "PCL_MUN='0248'", "outFields": "*", "f": "json"}


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


# --- keying -----------------------------------------------------------------


def test_key_is_a_usable_content_address(cache):
    key = cache.key(URL, PARAMS)
    assert isinstance(key, str)
    assert key != ""
    # It addresses a single cache entry, so it must not smuggle in path parts.
    assert "/" not in key and os.sep not in key and ".." not in key


def test_key_is_identical_for_the_same_request(cache):
    assert cache.key(URL, PARAMS) == cache.key(URL, PARAMS)


def test_key_ignores_dict_ordering(cache):
    reordered = {k: PARAMS[k] for k in reversed(list(PARAMS))}
    assert list(reordered) != list(PARAMS)  # genuinely a different ordering
    assert cache.key(URL, reordered) == cache.key(URL, PARAMS)


@pytest.mark.parametrize(
    "url,params",
    [
        pytest.param(URL, {**PARAMS, "f": "geojson"}, id="changed-value"),
        pytest.param(URL, {**PARAMS, "resultOffset": "1000"}, id="added-param"),
        pytest.param(URL, {k: v for k, v in PARAMS.items() if k != "f"}, id="removed-param"),
        pytest.param(URL + "?", PARAMS, id="changed-url"),
        pytest.param(URL, {}, id="no-params"),
        pytest.param(URL, None, id="params-none"),
    ],
)
def test_any_request_change_changes_the_key(cache, url, params):
    assert cache.key(url, params) != cache.key(URL, PARAMS)


def test_distinct_keys_are_not_collapsed_by_naive_concatenation(cache):
    """`a=1,b=2` and `a=1b=2` must not canonicalise to the same string."""
    assert cache.key(URL, {"a": "1", "b": "2"}) != cache.key(URL, {"a": "1b=2"})


def test_key_is_stable_across_processes(tmp_path):
    """Rules out anything built on Python's salted `hash()`."""
    repo_root = Path(__file__).resolve().parent.parent
    program = textwrap.dedent(
        f"""
        from pathlib import Path
        from houseaccount.cache import Cache
        print(Cache(Path({str(tmp_path / "cache")!r})).key({URL!r}, {PARAMS!r}))
        """
    )
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src"), "PYTHONHASHSEED": "random"}
    runs = {
        subprocess.run(
            [sys.executable, "-c", program], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(runs) == 1, f"key differed across processes: {runs}"


# --- storage ----------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"features": [{"attributes": {"PCLBLOCK": "1101"}}], "n": 5671}, id="json-dict"),
        pytest.param([1, 2, 3], id="json-list"),
        pytest.param({"nested": {"a": [None, True, 1.5, "unicode ✓"]}}, id="json-nested"),
        pytest.param(b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary ortho tile", id="raw-bytes"),
        pytest.param(b"", id="empty-bytes"),
    ],
)
def test_put_get_round_trip(cache, payload):
    key = cache.key(URL, PARAMS)
    cache.put(key, payload)
    assert cache.get(key) == payload


def test_bytes_and_json_round_trip_side_by_side(cache):
    """A bytes entry must not come back JSON-decoded, or vice versa."""
    json_key = cache.key(URL, {"f": "json"})
    bytes_key = cache.key(URL, {"f": "image"})
    cache.put(json_key, {"f": "json"})
    cache.put(bytes_key, b'{"f": "image"}')
    assert cache.get(json_key) == {"f": "json"}
    assert cache.get(bytes_key) == b'{"f": "image"}'


def test_miss_returns_none(cache):
    assert cache.get(cache.key(URL, PARAMS)) is None


def test_miss_returns_none_after_an_unrelated_put(cache):
    cache.put(cache.key(URL, PARAMS), {"cached": True})
    assert cache.get(cache.key(URL, {"where": "PCL_MUN='9999'"})) is None


def test_overwrite_replaces_the_entry(cache):
    key = cache.key(URL, PARAMS)
    cache.put(key, {"generation": 1})
    cache.put(key, {"generation": 2})
    assert cache.get(key) == {"generation": 2}


def test_entries_survive_a_new_cache_instance(tmp_path):
    root = tmp_path / "cache"
    key = Cache(root).key(URL, PARAMS)
    Cache(root).put(key, {"warm": True})
    assert Cache(root).get(key) == {"warm": True}


def test_files_are_written_under_the_cache_dir(tmp_path):
    root = tmp_path / "nested" / "cache"
    cache = Cache(root)
    cache.put(cache.key(URL, PARAMS), {"payload": True})

    written = [p for p in root.rglob("*") if p.is_file()]
    assert written, f"nothing written under {root}"
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and not p.is_relative_to(root)]
    assert outside == []


def test_cache_dir_is_created_on_demand(tmp_path):
    root = tmp_path / "does" / "not" / "exist" / "yet"
    cache = Cache(root)
    cache.put(cache.key(URL, PARAMS), b"bytes")
    assert root.is_dir()


def test_cache_can_be_built_from_the_configured_cache_dir():
    """Production wiring: the cache root is `config.cache_dir`."""
    config = Config.from_env()
    cache = Cache(config.cache_dir)
    assert cache.get(cache.key(URL, {"never": "fetched-in-tests"})) is None


def test_two_cache_roots_are_isolated(tmp_path):
    a, b = Cache(tmp_path / "a"), Cache(tmp_path / "b")
    key = a.key(URL, PARAMS)
    a.put(key, {"in": "a"})
    assert b.get(key) is None
