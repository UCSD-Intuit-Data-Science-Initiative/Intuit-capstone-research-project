import sys
from pathlib import Path

# Inside Docker, PYTHONPATH already has src/; this makes plain `pytest` work too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest


@pytest.fixture(autouse=True)
def _never_reach_a_real_langfuse_project(monkeypatch):
    """Tests must never send to Langfuse, even when .env holds real keys: Docker loads .env into the
    environment and cli.main() reloads it with load_dotenv, so both are switched off for every test."""
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
        monkeypatch.delenv(key, raising=False)
    try:
        import dotenv
    except ImportError:
        return
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
