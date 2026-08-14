# The image Fly.io runs: one Python process serving the MCP tools and the REST
# surface the Map UI eats (R12).
#
# Why 3.12 exactly: the development venv is 3.12 and the scoring engine's output
# is compared byte-for-byte against golden fixtures. A different minor version is
# a different interpreter, and "green locally, red in the container" is the one
# class of bug a pinned base image makes impossible.
#
# Why the artifacts are baked in: `houseaccount.server.app:create_app` reads a
# published run at boot and raises `DataUnavailable` if there is not one, so an
# image without `data/` is an image that cannot start. Shipping the run inside
# the image also makes the deployment reproducible — the container serves the
# artifacts that were reviewed, not whatever a volume happens to hold.
#
# Why no credentials: the pipeline needs keys, this server does not. It reads
# `doors.geojson` and `houseaccount.sqlite` and calls nothing. There is
# therefore no `ENV OPENAI_API_KEY` here and there must never be one — see
# `docs/DEPLOY.md` for `flyctl secrets set`, which is where credentials belong.

FROM python:3.12-slim

# Unbuffered so Fly's log tail shows a traceback as it happens rather than when
# the process dies; no .pyc, because the layer is read-only anyway.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The port the process binds. Fly overrides it via `[env]`/the platform; the
# default keeps `docker run -p 8000:8000 houseaccount` working with no flags.
ENV PORT=8000

WORKDIR /app

# Dependencies come from the one manifest. `src/` is copied first because the
# install below builds this project, and the install is `-e` on purpose:
# `houseaccount.config` locates `data/` and `cache/` by walking up from the
# package to the directory holding `pyproject.toml`, so the package has to stay
# next to it. A wheel in site-packages would resolve those paths to the Python
# installation and serve an empty territory.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e .

# The published run the server answers from, and the eval report the Data &
# Ethics page reads through `GET /api/eval/report.json`.
COPY data ./data
COPY eval/report.json ./eval/report.json

# A web process that is root inside its own image is one container escape away
# from being root on the host. Nothing here writes to the filesystem, so the
# account owns nothing and needs no shell.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin houseaccount \
 && chown -R houseaccount:houseaccount /app
USER houseaccount

EXPOSE 8000

# `sh -c` because `$PORT` has to be expanded by a shell, and `exec` so uvicorn
# is PID 1 and receives Fly's SIGTERM directly instead of being killed after the
# grace period. Same factory, same flags as `make serve`.
CMD ["sh", "-c", "exec uvicorn houseaccount.server.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
