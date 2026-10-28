# APi-ToF Web demo

This is a web demo of a simulation of a APi-ToF mass spectrometer

## Running with Docker

1.  **Build the Docker image:**

    ```bash
    docker build -t apitofsim-web .
    ```

2.  **Run the Docker container:**

    ```bash
    docker run -p 5000:5000 apitofsim-web
    ```

3.  **Access the application:**

    Open your web browser and navigate to [http://localhost:5000](http://localhost:5000).

Docker enables Ray token authentication by default. At startup it creates a private
token file under `/tmp`, which works with arbitrary container UIDs. To supply a
stable token, mount a readable token file and set `RAY_AUTH_TOKEN_PATH` to its
container path. `RAY_AUTH_TOKEN` is also supported. Tokens are created at runtime,
not baked into the image.

To opt out for an isolated development container, pass
`-e RAY_AUTH_MODE=disabled` to `docker run`.

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

It's also possible to run everything in a single command, which starts a local Ray cluster and automatically builds frontend assets before startup and each reload:

```bash
uv run python -m vms --reload
```

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
The test server always enables token authentication with a private temporary token,
independent of your shell's Ray credentials.

## Run failures

Failed runs show an alert with the stage, error message, and expandable traceback.
Their diagnostics are saved as `<jobid>.failure.json` beside the run's DuckDB
file, including counts and run/cluster IDs when available. Worker failures also
write to `experiment_failure` when the database is available. Reopening the
analysis URL restores the failure even after restarting the web server.

A run whose final counters disagree with its recorded events is marked
**incomplete**. Its available results remain accessible with a warning about
missing trajectories.

## Developing apitofsim-web and apitofsim

Check out apitofsim and apitofsim-resultviewer as sibling directories of
apitofsim-web. From the apitofsim-web directory, run:

```bash
./install_dev.sh
```

This creates `.venv-local` from the release lockfile, installs Meson build tools,
and installs both sibling checkouts as editable packages explicitly into
`.venv-local/bin/python`. Building apitofsim also requires a C++ toolchain and
its native dependencies, including TBB >=2023.0 and Eigen >=3.4.

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
