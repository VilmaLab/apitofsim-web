ARG DEPENDENCY_IMAGE=dependencies

FROM docker.io/library/python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS python
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM python AS dependency-builder
# Git is required for the locked Git dependencies and is confined to this stage.
RUN apt-get update \
  && apt-get install -y --no-install-recommends git \
  && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --no-default-groups --no-install-project --no-editable \
  && sha256sum pyproject.toml uv.lock > /opt/dependency-inputs.sha256

FROM python AS dependencies
# RDKit's Cairo renderer needs the X11 client libraries for molecular drawings.
RUN apt-get update \
  && apt-get install -y --no-install-recommends fontconfig libgomp1 libtbb12 libxext6 libxrender1 rclone tini zstd \
  && rm -rf /var/lib/apt/lists/*
COPY --from=dependency-builder /opt/venv /opt/venv
COPY --from=dependency-builder /opt/dependency-inputs.sha256 /opt/
ENV PATH="/opt/venv/bin:$PATH"

FROM docker.io/library/node:22-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts
COPY src ./src
RUN npm run build

FROM python AS wheel
WORKDIR /build
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml README.md uv.lock ./
COPY src/vms ./src/vms
COPY --from=frontend /app/src/vms/static/index.css /app/src/vms/static/index.js ./src/vms/static/
RUN --mount=type=cache,target=/root/.cache/uv \
  uv build --wheel --no-sources --out-dir /wheel \
  && sha256sum pyproject.toml uv.lock > /wheel/dependency-inputs.sha256 \
  && sha256sum /wheel/*.whl > /wheel/app-wheel.sha256

FROM ${DEPENDENCY_IMAGE} AS runtime
# Include the wheel hash in the layer cache key on builders that ignore bind mounts.
COPY --from=wheel /wheel/app-wheel.sha256 /opt/
# Mount the installer and wheel so only the installed application enters the layer.
RUN --mount=type=bind,from=uv,source=/,target=/installer \
    --mount=type=bind,from=wheel,source=/wheel,target=/wheel \
  cmp /opt/dependency-inputs.sha256 /wheel/dependency-inputs.sha256 \
  && sha256sum -c /opt/app-wheel.sha256 \
  && /installer/uv pip install --no-cache --no-deps --python /opt/venv/bin/python /wheel/*.whl
ADD --checksum=sha256:b97cfa3a3bf1cf254e1266b7190236b4eac4d57acf95e3ce4324044a67b2a394 --chmod=0755 https://github.com/frankier/dirroof/releases/download/v0.2.0/dirroof-filc-0.2.0-linux-x86_64 /usr/local/bin/dirroof
COPY fetch-and-run.sh /usr/local/bin/fetch-and-run.sh
# Rahti assigns an arbitrary UID with access through group 0.
RUN install -d -m 0775 /resultdbs

ENV DATABASE_DIR="/database/"
ENV RESULTS_DIR="/resultdbs"
ENV RESULTS_MAX_SIZE="20G"
ENV RAY_AUTH_MODE="token"
# The TLS-terminating proxy is not loopback. Trust its forwarded scheme.
ENV FORWARDED_ALLOW_IPS="*"
# OpenShift uses an arbitrary UID, so caches must live outside the installed packages.
ENV NUMBA_CACHE_DIR="/tmp/numba-cache"
ENV MPLCONFIGDIR="/tmp/matplotlib"
ENV XDG_CACHE_HOME="/tmp/.cache"

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/fetch-and-run.sh"]
