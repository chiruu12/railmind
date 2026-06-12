"""Vercel serverless entry point — re-exports the FastAPI app.

`_src/` holds a deploy-time copy of `backend/app` and `data/`
(synced by scripts/vercel_deploy.sh) so the bundle is self-contained.
"""

import os
import sys
from pathlib import Path

_src = Path(__file__).resolve().parent / "_src"
sys.path.insert(0, str(_src))

os.environ.setdefault("DATA_DIR", str(_src / "data"))
os.environ.setdefault("DB_PATH", "/tmp/railmind.sqlite")

from app.main import create_app  # noqa: E402

app = create_app()
