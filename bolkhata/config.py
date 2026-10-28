from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_PATH = Path(os.environ.get("BOLKHATA_DB", PROJECT_ROOT / "bolkhata.sqlite3")).expanduser().resolve()
FRONTEND_DIST = PROJECT_ROOT / "voice_component_frontend" / "dist"
