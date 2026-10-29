import sys
from pathlib import Path

# Inside Docker, PYTHONPATH already has src/; this makes plain `pytest` work too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
