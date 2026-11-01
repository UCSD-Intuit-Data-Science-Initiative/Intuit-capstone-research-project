# ── Capstone dev environment ─────────────────────────────────────────
# Every target runs inside Docker so the only host prerequisite is
# Docker (Desktop or Engine) with Compose v2.
#
# Files created inside the container land on the host with your UID/GID
# because we pass them as build-args.
# ─────────────────────────────────────────────────────────────────────

export UID := $(shell id -u)
export GID := $(shell id -g)

IMAGE      := d23-capstone:latest
COMPOSE    := docker compose

.PHONY: build build-guard shell jupyter smoke test data data-lock data-verify

## build        – build the base image
build:
	$(COMPOSE) build dev

## build-guard  – build the heavier image with torch / transformers
build-guard:
	$(COMPOSE) build guard

## shell        – drop into an interactive bash inside the container
shell:
	$(COMPOSE) run --rm dev

## jupyter      – start Jupyter Lab on http://localhost:8888
jupyter:
	$(COMPOSE) up jupyter

## smoke        – run the smoke test (no LLM keys needed)
smoke:
	$(COMPOSE) run --rm dev python scripts/smoke_test.py

## test         – run the full pytest suite
test:
	$(COMPOSE) run --rm dev python -m pytest tests/ -v

# Docker Desktop (Mac/Windows) ignores a chmod made inside the container, so
# raw files are also made read-only from the host side after fetching.
READ_ONLY_RAW := find data/raw -type f ! -name FETCH_LOG.json ! -name .gitkeep -exec chmod a-w {} +

## data         – fetch FinQA + ConvFinQA at pinned commits, verify checksums
data:
	$(COMPOSE) run --rm dev python -m capstone.data.fetch
	$(READ_ONLY_RAW)

## data-lock    – first time only: fetch, then write data/checksums.sha256
data-lock:
	$(COMPOSE) run --rm dev python -m capstone.data.fetch --write-lock
	$(READ_ONLY_RAW)

## data-verify  – offline: check data/raw against data/checksums.sha256
data-verify:
	$(COMPOSE) run --rm dev python -m capstone.data.fetch --verify
