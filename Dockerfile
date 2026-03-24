FROM docker.io/mambaorg/micromamba:2.5.0 AS builder

## Step 1. Build application
COPY --chown=$MAMBA_USER:$MAMBA_USER . /tmp/apitofsim-web
USER root
RUN mkdir /env && chown $MAMBA_USER:$MAMBA_USER /env
USER $MAMBA_USER
WORKDIR /tmp/
RUN --mount=type=cache,target=/opt/conda/pkgs \
  micromamba create --copy -p /env --yes \
  --file /tmp/apitofsim-web/env-container.lock && \
  micromamba install -p /env --yes rclone bash

## Step 2. Build the final bare container
FROM gcr.io/distroless/base-debian13

# Copy the application from the builder
COPY --from=builder /env /env
COPY --from=builder /tmp/apitofsim-web/fetch-and-start.sh /env/bin/

# Place executables in the environment at the front of the path
ENV PATH="/env/bin:$PATH"
ENV DATABASE_DIR="/database/"

# Run Quart
EXPOSE 8080
# Only ever use 1 worker since it is stateful
CMD ["/env/bin/bash", "fetch-and-start.sh"]
