# syntax=docker/dockerfile:1
# One image for both front ends: the web page by default, the bot with
# `python -m khmer_converter.bot`. See README.md.

ARG PYTHON=python:3.12-slim
ARG UV=ghcr.io/astral-sh/uv:0.12.19

FROM ${UV} AS uv

# The full lexicon, built from the engine's open sources at the engine commit that
# uv.lock pins, so the data always matches the code.
FROM ${PYTHON} AS lexicon
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY uv.lock .
RUN rev=$(sed -n 's/.*khmer-engine?rev=\([0-9a-f]*\).*/\1/p' uv.lock | head -n 1) \
    && test -n "$rev" \
    && git init -q engine && cd engine \
    && git fetch -q --depth 1 https://github.com/pheasaKhmer/khmer-engine "$rev" \
    && git checkout -q FETCH_HEAD \
    && uv sync --locked --group data --no-dev \
    && uv run --no-sync python scripts/build_data.py --out /lexicon

# The converter and its dependencies, in a virtual environment.
FROM ${PYTHON} AS app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON}
RUN useradd --system --uid 10001 --home-dir /app app && mkdir /data && chown app /data
COPY --from=app /app/.venv /app/.venv
COPY --from=lexicon /lexicon /lexicon
ENV PATH="/app/.venv/bin:$PATH" \
    KHMER_ENGINE_DATA=/lexicon \
    FEEDBACK_DB=/data/feedback.db \
    PYTHONUNBUFFERED=1
USER app
WORKDIR /app
VOLUME /data
EXPOSE 8000
# No access log: it would record visitors' addresses.
CMD ["uvicorn", "--factory", "khmer_converter.web:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
