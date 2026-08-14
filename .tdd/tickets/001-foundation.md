---
id: 001
title: "Foundation: config, content-addressed cache, HTTP client, cost ledger, redaction guard"
status: green
depends_on: []
touches: [src/houseaccount/config.py, src/houseaccount/cache.py, src/houseaccount/http.py, src/houseaccount/cost.py, src/houseaccount/sources/__init__.py, src/houseaccount/vision/__init__.py, src/houseaccount/scoring/__init__.py, tests/test_config.py, tests/test_cache.py, tests/test_http.py, tests/test_cost.py, tests/test_redaction.py]
iterations: 1
test_files: [tests/test_config.py, tests/test_cache.py, tests/test_http.py, tests/test_cost.py, tests/test_redaction.py]
branch: ""
---

## Scope

The shared plumbing every later ticket sits on. Builds: env/config loading, a content-addressed
on-disk cache, a retrying HTTP-JSON/bytes client that goes through that cache, an API cost ledger,
and the permanent no-identity-data guard test. Also creates the empty package dirs
(`sources/`, `vision/`, `scoring/`) so later parallel tickets don't collide creating them.

Does NOT build: any data source, any scoring, any server. No network calls in tests — the HTTP
client takes an injectable transport.

## Acceptance criteria

- [ ] `Config.from_env()` reads `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`, `GOOGLE_MAPS_KEY` from the
      environment; **all three are optional** — absent → `None`, never raises. Also exposes
      `cache_dir`, `data_dir`, `repo_root` as paths.
- [ ] No API key literal anywhere in source: config only ever reads `os.environ`.
- [ ] `Cache.key(url, params)` is a deterministic content address (hash of the canonicalised
      request); identical request → identical key regardless of dict ordering; any param change →
      different key.
- [ ] `Cache.put`/`Cache.get` round-trip both JSON payloads and raw bytes; miss returns `None`;
      cache files live under `config.cache_dir`.
- [ ] `fetch_json(url, params=..., cache=...)` returns the cached payload and performs **zero**
      transport calls on a warm cache (prove it with a transport that raises if invoked).
- [ ] Retries: a transport returning 500 then 200 succeeds; attempts are bounded (no infinite
      loop); a 404 raises `SourceError` carrying the url and status code.
- [ ] `CostLedger.record(source, units, usd)` accumulates; `total_usd()` sums; `per_door(n)`
      returns 0.0 for n == 0 instead of dividing by zero; `save()`/`load()` round-trip to JSON.
- [ ] **Redaction guard** (`tests/test_redaction.py`): scanning `src/`, `eval/`, `web/` for the
      tokens `OWNER_NAME`, `ST_ADDRESS`, `CITY_STATE` yields zero hits. The guard must fail if a
      hit is introduced (demonstrate by mutation after the checkpoint commit, then revert).

## Attempt log

- iter 1: green first run. 87 tests. Redaction guard demonstrated red under an
  injected `OWNER_NAME` in `src/houseaccount/config.py`, then reverted.
- Style set for the run: `from __future__ import annotations`, frozen dataclasses,
  cache stores raw response bytes (not decoded payloads), `requests` imported lazily
  inside the default transport so the suite never touches it.
