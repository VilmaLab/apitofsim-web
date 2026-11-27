# APi-ToF Web demo

🤖 LLM generated

This is a web demo of a simulation of a APi-ToF mass spectrometer

## Running with Docker

1.  **Build the Docker image:**

    ```bash
    docker build -t apitofsim-web .
    ```

2.  **Run the Docker container:**

    ```bash
    docker run -p 5000:8080 apitofsim-web
    ```

3.  **Access the application:**

Open your web browser and navigate to [http://localhost:5000](http://localhost:5000).

<details>
<summary>Container startup details</summary>

The container uses tini as PID 1. If there is no complete local database, the
launcher streams `database.apitofsim.tar.zst` from the private Allas bucket
`apitofsim-data` and unpacks it without saving a second copy of the archive.
Publish the archive before starting with an empty database volume; see
[`datasets/README.md`](datasets/README.md). The launcher then starts Ray,
dirroof, and the web server. An unexpected service exit stops the container so
the runtime can restart it.

</details>

Set `RESULTS_DIR` to the dedicated results directory (default `/resultdbs`) and
`RESULTS_MAX_SIZE` to its size limit (default `20G` in the container). Mount a
writable volume at `RESULTS_DIR` to retain results across container replacement.
Keep `RESULTS_DIR` outside `DATABASE_DIR`. Rahti mounts a separate results volume
at `/resultdb`.

<details>
<summary>Results eviction details</summary>

dirroof tracks the size of the results tree recursively and evicts
least-recently-accessed job directories as units. Files directly under
`RESULTS_DIR` can also be evicted individually. Each job should have its own
top-level directory. The container needs inotify and access to `/proc/self/fd`.

</details>

<details>
<summary>Ray authentication and runtime details</summary>

Docker enables Ray token authentication by default. For the local head, the
launcher creates a private token file under `/tmp` if credentials are absent.
To supply a stable token, mount a readable token file and set
`RAY_AUTH_TOKEN_PATH` to its container path. `RAY_AUTH_TOKEN` is also supported.
An explicit external `RAY_ADDRESS` uses that cluster and its credentials.

If a deployment launches uvicorn directly, the app also creates a private
temporary token when embedded Ray cannot write its default token directory.
That token is removed after Ray shuts down. Supplied credentials and existing
default tokens take precedence. Fontconfig uses `/tmp/.cache` in the image.

To opt out for an isolated development container, pass
`-e RAY_AUTH_MODE=disabled` to `docker run`.

</details>

<details>
<summary>Container builds and Rahti deployment</summary>

### Reusing the runtime dependency image

`./scripts/build-container.sh` builds the dependency image once and reuses it for
subsequent app builds. It tags the dependency image with a hash of the Dockerfile,
`pyproject.toml`, and `uv.lock`. Set `CONTAINER_ENGINE=podman` to use Podman.
The runtime installs the production dependencies from `uv.lock` and adds the
application wheel in its own layer.

### Deploying to Rahti

The **Publish container** GitHub Actions workflow builds the production image
from `main` and pushes it to `ghcr.io/vilmalab/apitofsim-web`. BuildKit stores
intermediate layers in a separate GHCR cache. The workflow publishes a
`sha-<commit>` tag and records the image digest in the run summary.

Rahti imports the GHCR `main` tag manually. After **Publish container** finishes,
run the following command to update the `apitofsim-web-ghcr:main` image stream.
The deployment rolls out automatically when the import resolves to a new digest.

```bash
oc -n apitofsimweb import-image apitofsim-web-ghcr:main
```

</details>

<details>
<summary>Monitoring and the Ray dashboard</summary>

## Sentry monitoring

Set `SENTRY_DSN` in the web process environment. With Docker, pass
`-e SENTRY_DSN` to `docker run` to forward it from your shell. The app forwards
the Sentry settings to its Ray workers, including on an external Ray cluster.
Leave `SENTRY_DSN` unset to disable monitoring.

Sentry collects Starlette errors, request traces, Python logs and profiles, along
with Ray simulation errors and timings. The request that submits a
simulation and the worker that runs it share a trace. Ray task and actor
failures surfaced to the scheduler are reported by the web process.

- `SENTRY_ENVIRONMENT` and `SENTRY_RELEASE` label events for a deployment.
- `SENTRY_TRACES_SAMPLE_RATE` defaults to `1.0`.
- `SENTRY_PROFILE_SESSION_SAMPLE_RATE` defaults to `0.1`. Profiles are
  collected while sampled traces are active.

Sampling rates range from `0.0` to `1.0` and do not affect error reporting.

## Accessing the Ray dashboard on CSC Rahti

Log in with `oc` and select the OpenShift project that contains the app pod.
Pass the pod name to the tunnel script.

```bash
./scripts/tunnel-ray-dashboard.sh apitofsim-web-git-6c49f7b599-hqx9k
```

Open [http://127.0.0.1:8265](http://127.0.0.1:8265) while the script is running.
Press Ctrl+C to close the tunnel.

</details>

## Running the Application Locally with uv

1. **Install uv**

2. **Install packages:**
    ```bash
    uv sync --all-groups --all-extras
    ```

3. **Install Node.js and npm** for automatic frontend builds.

4. **Start the servers** (this will start both the Ray server and the webserver in tmux, automatically building frontend assets):
    ```bash
    ./run_servers.sh
    ```

<details>
<summary>Starting Ray and the web server separately</summary>

Alternatively you can manually start them in separate terminals:

**Start Ray:**
```bash
export RAY_AUTH_MODE=token
(umask 077; uv run ray get-auth-token --generate >/dev/null)
mkdir -p /tmp/raytmp
uv run ray start \
--head \
--object-store-memory 512000000 \
--temp-dir /tmp/raytmp \
--num-cpus 1 \
--port 6379 \
--include-dashboard false \
--block
```

**Run the webserver:**
```bash
RAY_AUTH_MODE=token RAY_ADDRESS="localhost:6379" uv run python -m vms --reload
```

</details>

It's also possible to run everything in a single command, which starts a local Ray cluster and automatically builds frontend assets before startup and each reload:

```bash
uv run python -m vms --reload
```

<details>
<summary>Local authentication and frontend build details</summary>

Ray authentication defaults to `token` in the app and `run_servers.sh`. The launcher
ensures a token exists before starting Ray and passes the same authentication
environment to both tmux panes. Embedded Ray generates and reuses
`~/.ray/auth_token` automatically. A separately started Ray server and its web
process must use the same authentication mode and token. Ray checks
`RAY_AUTH_TOKEN`, then `RAY_AUTH_TOKEN_PATH`, then `~/.ray/auth_token`.
An explicitly configured token file must exist and be readable. Keep token files
private and outside the repository.

To opt out locally, use `RAY_AUTH_MODE=disabled ./run_servers.sh restart` or
`RAY_AUTH_MODE=disabled uv run python -m vms --reload`. For manual separate
processes, set `RAY_AUTH_MODE=disabled` in both terminals and skip token generation.
Restart existing servers when changing authentication settings.

The generated `src/vms/static/index.js` and `index.css` are ignored by Git.
`workingtitle.devserver` installs npm dependencies as needed and skips builds
when inputs are unchanged. CSS, JavaScript, template, and Python edits trigger
rebuilds and server reloads. Direct `uvicorn vms:app` runs also build assets on
startup, but use the command above to watch frontend changes.

Before building a Python wheel, generate the assets so they are included:

```bash
uv run python -m workingtitle.devserver vms.assets:ASSETS
uv build
```

Installed wheels validate their bundled assets without needing Node.js. Docker
builds the frontend in a separate Node.js stage before installing the app.

</details>

**Access the application:**

Open your web browser and navigate to [http://localhost:5000](http://localhost:5000).

## Running the tests

The test suite drives a real browser through a short simulation run against a small
test database, which is downloaded automatically on first run.

1.  **Install a browser:**

    ```bash
    uv run playwright install chromium
    ```

2.  **Run the tests:**

    ```bash
    uv run pytest
    ```

The suite starts its own web server and its own Ray cluster, so `run_servers.sh` must
not be running, and `RAY_ADDRESS` must not be set in the environment.

<details>
<summary>Test authentication details</summary>

The test server always enables token authentication with a private temporary token,
independent of your shell's Ray credentials.

</details>

## Run failures

Set `RESULTS_DIR` to the directory for user result databases and failure
diagnostics. Outside the container it defaults to `resultdbs` in the web
server's working directory.
Existing deployments using `RESULTS` still use the parent of that path when
`RESULTS_DIR` is unset.

Each job stores its data in a `<jobid>.apitofsim` directory. The directory
contains the result database, its Parquet event archive, and any failure
diagnostics. Copy the whole job directory after the job finishes. Opening it
through the app also requires the matching shared cluster database.

Failed runs show the stage, error message, and expandable traceback.

<details>
<summary>Failure diagnostic storage</summary>

Diagnostics in `failure.json` let the analysis page reopen after a server
restart, including when the job failed before its database was initialized.
When available, the database also stores worker failures in `experiment_failure`.

</details>

A run whose final counters disagree with its recorded events is marked
**incomplete**. Its available results remain accessible with a warning about
missing trajectories.

<details>
<summary>Developing apitofsim-web and apitofsim</summary>

## Developing apitofsim-web and apitofsim

Check out apitofsim and apitofsim-resultviewer as sibling directories of
apitofsim-web. From the apitofsim-web directory, run:

```bash
./install_dev.sh
```

This creates `.venv-local` from the release lockfile, installs CMake build tools,
and installs both sibling checkouts as editable packages. Follow the
[apitofsim build prerequisites](https://github.com/VilmaLab/apitofsim/blob/v0.33.0/docs/development.md)
to set up the native toolchain.

Set `UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=.venv-local` when running development
commands. Disabling sync preserves the editable installs instead of restoring
the pinned release packages. Restart the servers when switching environments;
attaching to an existing tmux session keeps its original processes.

```bash
UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=.venv-local ./run_servers.sh restart
```

To check the selected interpreter and simulator installation without importing
or rebuilding apitofsim:

```bash
UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=.venv-local uv run python -c \
  'import sys, importlib.metadata as m; print(sys.executable); print(m.distribution("apitofsim").read_text("direct_url.json"))'
```

The output should show `.venv-local/bin/python`, the sibling apitofsim checkout,
and `"editable": true`. To switch back to the pinned releases:

```bash
./run_servers.sh restart
```

</details>
