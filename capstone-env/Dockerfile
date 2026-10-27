# Shared dev environment for the D23 capstone (Intuit, safety drift in finance agents)
# Same Python, same packages, same FinVault commit for everyone.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git make \
 && rm -rf /var/lib/apt/lists/*

# ---- FinVault, pinned to the commit the team reviewed ----------------------
# Read-only reference copy. Our own code lives in src/ and wraps it; we don't
# edit FinVault itself, so updating it later is a one-line change here.
ARG FINVAULT_REPO=https://github.com/aifinlab/FinVault.git
ARG FINVAULT_COMMIT=78848188a74d0124a74a63134315a65fdd43fb2a
RUN git clone --quiet "$FINVAULT_REPO" /opt/finvault \
 && git -C /opt/finvault checkout --quiet "$FINVAULT_COMMIT" \
 && rm -rf /opt/finvault/.git \
 # FinVault leaves api_key blank in its config; point blank keys at an env var
 # (its loader expands ${VAR}), so keys come from .env and never from git.
 && sed -i -E 's/^([[:space:]]*api_key:)[[:space:]]*$/\1 ${OPENROUTER_API_KEY}/' \
        /opt/finvault/sandbox/config/agents_config.yaml

# ---- Python packages (exact versions from the lock file) -------------------
COPY requirements.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock

# FinVault imports both `sandbox.*` and bare `base`, `prompts`, `sandbox_00`...
ENV FINVAULT_HOME=/opt/finvault \
    PYTHONPATH=/workspace/src:/opt/finvault:/opt/finvault/sandbox

# ---- Non-root user matching your host user, so files you create in the -----
# mounted folder are owned by you, not root.
ARG UID=1000
ARG GID=1000
RUN groupadd -g "$GID" dev 2>/dev/null || true \
 && useradd -m -u "$UID" -g "$GID" -s /bin/bash dev 2>/dev/null || true

WORKDIR /workspace
USER ${UID}:${GID}
CMD ["bash"]

# ---- Optional: local guard / open-weight models (large, ~several GB) -------
FROM base AS guard
USER root
COPY requirements-guard.txt /tmp/requirements-guard.txt
RUN pip install -r /tmp/requirements-guard.txt
ARG UID=1000
ARG GID=1000
USER ${UID}:${GID}
