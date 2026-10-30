# syntax=docker/dockerfile:1.7

# ---- build stage: compile a wheel and install dependencies into a venv ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
# Optional: behind a TLS-intercepting corporate proxy, pass its CA bundle with
#   docker build --secret id=ca_bundle,src=/path/to/ca.pem .
# The secret is mounted only for this step and never stored in a layer.
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi \
    && pip install -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi \
    && pip install --no-deps .

# ---- runtime stage: only the venv, no build tooling, non-root user ----
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="enterprise-data-pipeline" \
      org.opencontainers.image.description="Manufacturing and supply-chain analytics pipeline" \
      org.opencontainers.image.licenses="MIT"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EDP_LANDING_DIR=/data/landing \
    EDP_REPORT_DIR=/data/reports
RUN groupadd --system --gid 10001 edp \
    && useradd --system --uid 10001 --gid edp --home-dir /app --shell /usr/sbin/nologin edp \
    && mkdir -p /data/landing /data/reports \
    && chown -R edp:edp /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER edp
ENTRYPOINT ["edp"]
CMD ["--help"]
