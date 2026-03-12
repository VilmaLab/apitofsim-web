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

## Running the Application Locally with micromamba

1.  **Install micromamba**

2.  **Make an environment:**

    ```bash
    micromamba create -f env.yaml -p ./cenv
    ```

3. **Activate the environment:**
    ```bash
    micromamba activate ./cenv
    ```

4. **Start Ray:**
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

5. **Run the webserver:**
    ```bash
    quart --app vms run --debug
    ```

6.  **Access the application:**

    Open your web browser and navigate to [http://localhost:5000](http://localhost:5000).

## Developing apitofsim-web and apitofsim using micromamba

```bash
micromamba activate ./cenv
mamba install python-meson
pip install -Ceditable-verbose=true --no-build-isolation -e /path/to/apitofsim
```
