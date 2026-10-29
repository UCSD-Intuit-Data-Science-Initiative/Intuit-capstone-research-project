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

.PHONY: build build-guard shell jupyter smoke test run

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

## run          – run the harness, e.g. make run ARGS="--model claude --k 4 --out results/exp1"
run:
	$(COMPOSE) run --rm dev python -m capstone.cli run $(ARGS)
