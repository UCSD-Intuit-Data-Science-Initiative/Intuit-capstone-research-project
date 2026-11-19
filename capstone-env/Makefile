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

.PHONY: build build-guard shell jupyter smoke test data data-lock data-verify profile select family family-check run run-families demo poc-estimate poc

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

## profile      – Stage 2: profile the data, compare to data/profile_expected.yaml
profile:
	$(COMPOSE) run --rm dev python -m capstone.data.profile

## select       – Stage 3: filter candidates, split by company, check the gate
select:
	$(COMPOSE) run --rm dev python -m capstone.data.select

## family       – Stage 4: draw, build families from data/families/specs/, write JSON + review
family:
	$(COMPOSE) run --rm dev python -m capstone.data.family

## family-check – Stage 4: rebuild families in memory, fail if any generated file differs (writes nothing)
family-check:
	$(COMPOSE) run --rm dev python -m capstone.data.family --check

## run          – Stage 6 harness, e.g. make run ARGS="--model mock --out results/exp1" (see README)
run:
	$(COMPOSE) run --rm dev python -m capstone.cli run $(ARGS)

## run-families – Stage 6 credit families, e.g. make run-families ARGS="--model mock --versions 3 --out results/fam1"
run-families:
	$(COMPOSE) run --rm dev python -m capstone.cli run-families $(ARGS)

## demo         – Stage 6 local demo: four scripted mock agents through F001 (no keys, no cost)
demo:
	$(COMPOSE) run --rm dev python -m capstone.credit.demo

## poc-estimate – Stage 6 PR 4: rough cost of the first real run (S01 + F001, 9 versions x 2 modes, claude)
POC_ARGS := --families data/families/smoke/S01 data/families/pilot/F001 --model claude --k 1 --out results/poc1
poc-estimate:
	$(COMPOSE) run --rm dev python -m capstone.cli run-families $(POC_ARGS) --estimate

## poc          – Stage 6 PR 4: the first real run (needs ANTHROPIC_API_KEY in .env; costs money), then the page
poc:
	$(COMPOSE) run --rm dev python -m capstone.cli run-families $(POC_ARGS) $(ARGS)
	$(COMPOSE) run --rm dev python -m capstone.cli report results/poc1 --out docs/results/poc_run_1.md \
		--title "First real run (S01 + F001)"
