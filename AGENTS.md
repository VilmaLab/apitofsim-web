# AGENTS.md

## Project

Web demo for simulating an APi-ToF mass spectrometer.

## Layout and tooling

- `src/vms/`: Python web app, Jinja templates, and built static assets.
- `src/css/` and `src/js/`: frontend source; `tests/`: browser-based tests.
- `datasets/` and `example/`: sample data and examples.
- `uv` manages Python dependencies; `npm` builds Tailwind CSS and bundles JavaScript with esbuild. The app uses Starlette, Ray, HTMX, and Alpine.js.

## Style — DRY and reuse first

Follow the patterns already in the codebase rather than inventing new ones.
Be succinct!
Prefer Tailwind utilities over custom CSS. Prefer CSS or Alpine.js over custom JavaScript; choose between CSS and Alpine.js based on which is easier for the task. Keep layouts responsive.

## Formatting

`prek run --all-files` runs formatters through through `prek.toml`.
