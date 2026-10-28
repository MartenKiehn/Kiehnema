FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv
FROM docker:29.8.2-cli@sha256:35cfcf893c20f2931f4909151b5407aaaeab711277de7ce8b8f521f095d54c32 AS docker_cli
FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS runtime
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/kiehnema
ENV PATH="/opt/kiehnema/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/* /root/.cache
RUN useradd --uid 1000 --create-home kiehnema
COPY app ./app
COPY deploy ./deploy
COPY docs/harness-contract.md ./docs/harness-contract.md
COPY examples ./examples
USER 1000:1000
CMD ["python", "-m", "app"]

FROM runtime AS test
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-install-project
COPY tests ./tests
COPY examples ./examples
COPY scripts ./scripts
USER 1000:1000
CMD ["python", "-m", "pytest", "-q"]

FROM test AS integration
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker_cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
USER root
CMD ["python", "-m", "scripts.test_updates_linux"]

FROM caddy:2-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f AS gateway
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/maintenance.html /srv/maintenance/index.html
COPY app/static/update-progress.js /srv/maintenance/update-progress.js
COPY deploy/maintenance.css /srv/maintenance/maintenance.css
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s CMD wget -q -O /dev/null http://127.0.0.1:8081/healthz || exit 1
