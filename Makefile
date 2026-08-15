PY := PYTHONPATH=src .venv/bin/python
UV := uv

# Source `.env` when it exists, so the credentials a run needs are present
# without anyone remembering to export them first.
#
# This is a correctness guard, not a convenience. Every credential is optional
# and every missing one *degrades a stage rather than failing the run*: with no
# OPENAI_API_KEY the vision stage declines, with no CENSUS_API_KEY the ACS prior
# is absent, and `make pipeline` still exits 0 having published a complete,
# internally consistent, three-signals-poorer territory. That artifact then gets
# baked into the deploy image. The failure is silent by design, so the defence
# has to be automatic — a forgotten `set -a; . ./.env; set +a` must not be the
# difference between 540 doors with imagery and 540 without.
#
# Only the targets that actually read credentials use this. `test-py` must NOT:
# the suite is hermetic and asserts the declination paths, so a developer with a
# populated `.env` needs to see the same greens as CI with none.
DOTENV := set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: setup pipeline eval test test-py test-web serve clean

setup:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install -e ".[dev]"
	npm --prefix web install --silent || true

pipeline:
	$(DOTENV) $(PY) -m houseaccount.pipeline

eval:
	$(DOTENV) $(PY) -m eval.harness

test: test-py test-web

test-py:
	$(PY) -m pytest

test-web:
	npm --prefix web test

serve:
	$(PY) -m uvicorn houseaccount.server.app:create_app --factory --host 0.0.0.0 --port 8000

clean:
	rm -rf .pytest_cache __pycache__ data/doors.geojson data/houseaccount.sqlite
