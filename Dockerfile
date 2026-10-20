FROM docker.io/library/node:22 AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY src ./src
RUN npm run build

FROM docker.io/mambaorg/micromamba:2.9.0 AS builder

## Step 1. Build application
COPY --chown=$MAMBA_USER:$MAMBA_USER . /tmp/apitofsim-web
COPY --from=frontend --chown=$MAMBA_USER:$MAMBA_USER /app/src/vms/static/index.css /app/src/vms/static/index.js /tmp/apitofsim-web/src/vms/static/
USER root
RUN mkdir /env /tools && chown $MAMBA_USER:$MAMBA_USER /env /tools
USER $MAMBA_USER
WORKDIR /tmp/
ENV PATH="/tools/bin:/env/bin:$PATH"
# The extra tools get their own prefix: adding them to /env makes micromamba
# re-solve the whole locked environment (400+ packages including ray-all), which
# needs several GB of RAM and gets OOM-killed on the build runner (exit 137).
RUN --mount=type=cache,target=/opt/conda/pkgs \
  micromamba create --copy -p /tools --yes rclone bash git uv
RUN --mount=type=cache,target=/opt/conda/pkgs \
  micromamba create --copy -p /env --yes \
  --file /tmp/apitofsim-web/env-container.lock
# The project's dependencies include git and URL sources from [tool.uv.sources],
# which pip cannot resolve; use uv, and git for the git sources.
RUN micromamba run -p /env uv pip install --python /env/bin/python /tmp/apitofsim-web/

## Step 2. Build the final bare container
FROM gcr.io/distroless/base-debian13

# Copy the application and its tools from the builder
COPY --from=builder /env /env
COPY --from=builder /tools /tools
COPY --from=builder /tmp/apitofsim-web/fetch-and-start.sh /env/bin/

# Place executables in the environment at the front of the path
ENV PATH="/env/bin:/tools/bin:$PATH"
ENV DATABASE_DIR="/database/"
ENV RESULTS="/database/results"
ENV RAY_USE_PIP="1"

# Run the ASGI application
EXPOSE 8080
# Only ever use 1 worker since it is stateful
CMD ["/tools/bin/bash", "/env/bin/fetch-and-start.sh"]
