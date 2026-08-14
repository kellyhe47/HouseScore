PY := PYTHONPATH=src .venv/bin/python
UV := uv

.PHONY: setup pipeline eval test test-py test-web serve clean

setup:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install -e ".[dev]"
	npm --prefix web install --silent || true

pipeline:
	$(PY) -m houseaccount.pipeline

eval:
	$(PY) -m eval.harness

test: test-py test-web

test-py:
	$(PY) -m pytest

test-web:
	npm --prefix web test

serve:
	$(PY) -m uvicorn houseaccount.server.app:app --host 0.0.0.0 --port 8000

clean:
	rm -rf .pytest_cache __pycache__ data/doors.geojson data/houseaccount.sqlite
