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

## Running the Application Locally with uv

1. **Install uv**

2. **Install packages:**
    ```bash
    uv sync --all-groups --all-extras
    ```

3. **Start the servers** (this will start both the Ray server and the webserver in tmux):
    ```bash
    ./run_servers.sh
    ```

Alternatively you can manually start them in separate terminals:

**Start Ray:**
    ```bash
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
    RAY_ADDRESS="localhost:6379" uv run uvicorn vms:app --reload --port 5000
    ```

It's also possible to run the webserver in a single command, which will start Ray if it is not already running:

```bash
uv run uvicorn vms:app --reload --port 5000
```

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
