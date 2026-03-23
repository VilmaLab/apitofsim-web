FROM docker.io/mambaorg/micromamba:2.5.0 AS builder

# Step 0. Args and arg checking
ARG RCLONE_S3_ACCESS_KEY_ID
ENV RCLONE_S3_ACCESS_KEY_ID=$RCLONE_S3_ACCESS_KEY_ID
RUN test -n "$RCLONE_S3_ACCESS_KEY_ID" || (echo "RCLONE_S3_ACCESS_KEY_ID not set" && false)
ARG RCLONE_S3_SECRET_ACCESS_KEY
ENV RCLONE_S3_SECRET_ACCESS_KEY=$RCLONE_S3_SECRET_ACCESS_KEY
RUN test -n "$RCLONE_S3_SECRET_ACCESS_KEY" || (echo "RCLONE_S3_SECRET_ACCESS_KEY not set" && false)

## Step 1. Build application
COPY --chown=$MAMBA_USER:$MAMBA_USER . /tmp/apitofsim-web
USER root
RUN mkdir /env && chown $MAMBA_USER:$MAMBA_USER /env
USER $MAMBA_USER
RUN --mount=type=cache,target=/opt/conda/pkgs micromamba create --copy -p /env --yes --file /tmp/apitofsim-web/env-container.lock

## Step 2. Grab and import data
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    rclone
USER $MAMBA_USER

RUN cd /tmp/apitofsim-web/datasets && \
    RCLONE_S3_ENDPOINT=https://a3s.fi rclone copy :s3,env_auth:apitofsim-data .

## Step 3. Build the final bare container
FROM gcr.io/distroless/base-debian13

# Copy the application from the builder
COPY --from=builder /env /env

# Copy the database
COPY --from=builder /tmp/apitofsim-web/datasets/database.ase.sqlite.db /tmp/apitofsim-web/datasets/database.duckdb .

# Place executables in the environment at the front of the path
ENV PATH="/env/bin:$PATH"

# Run Quart
EXPOSE 8080
# Only ever use 1 worker since it is stateful
CMD ["hypercorn", "-w", "1", "-b", "0.0.0.0:8080", "vms:app"]
