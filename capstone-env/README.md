# D23 Capstone Dev Environment

Containerized workspace for the Intuit safety-drift capstone.
Same Python, same packages, same FinVault commit for everyone.

## Prerequisites

| Platform | Install |
|---|---|
| **Mac (Intel or Apple Silicon)** | [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) |
| **Windows / WSL2** | [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) with the WSL2 backend enabled. Run all commands from your WSL2 terminal. |

Make sure `docker compose version` prints **v2+**.

## Quick start

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd capstone-env

# 2. Add your API keys
cp .env.example .env
# Edit .env and paste your keys

# 3. Build the image (~2 min first time)
make build

# 4. Verify everything works (no keys needed)
make smoke

# 5. Start working
make shell          # interactive bash inside the container
make jupyter        # Jupyter Lab at http://localhost:8888
make test           # run pytest suite
```

## Make targets

| Target | What it does |
|---|---|
| `make build` | Build the base image |
| `make build-guard` | Build the heavier image with torch/transformers |
| `make shell` | Interactive bash inside the container |
| `make jupyter` | Start Jupyter Lab on port 8888 |
| `make smoke` | Run the smoke test (no API keys needed) |
| `make test` | Run the full pytest suite |
| `make data` | Fetch FinQA + ConvFinQA at pinned commits and verify checksums |
| `make data-verify` | Offline check of `data/raw` against `data/checksums.sha256` |
| `make profile` | Profile the data and compare to the Nov 1 numbers |
| `make select` | Select candidates, split by company, check the gate |

## Project layout

```
src/capstone/      ← our code (mounted into the container)
scripts/           ← one-off scripts, smoke test
notebooks/         ← Jupyter notebooks
cases/             ← case definition files
data/              ← FinQA/ConvFinQA (fetch with `make data`; see data/README.md)
docs/              ← dataset card, review log
results/           ← experiment outputs (git-ignored)
tests/             ← pytest tests
```

FinVault lives at `/opt/finvault` inside the image (read-only reference).
It is already on `PYTHONPATH`; import with `from sandbox.sandbox_00 import ...`.
