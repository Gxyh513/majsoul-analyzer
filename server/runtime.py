"""Shared process-scoped resources, independent from the FastAPI application.

Import this module for storage roots and the shared Mahjong Soul client; service
modules no longer need to import server.app during their initialization.
"""
from __future__ import annotations

from pathlib import Path
from .storage_paths import storage_paths
from .majsoul.session import SessionLock
from .majsoul.client import MajsoulClient

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR, RECORDS_DIR = storage_paths(DATA_DIR)
REVIEWS_DIR = CACHE_DIR.parent / "reviews"
LEGACY_RECORDS_DIRS = (DATA_DIR / "records",) if RECORDS_DIR != DATA_DIR / "records" else ()
MODELS_DIR = DATA_DIR / "models"
CONFIG_PATH = DATA_DIR / "config.json"
LOCAL_CONFIG_PATH = DATA_DIR / "config.local.json"
EXAMPLE_CONFIG_PATH = DATA_DIR / "config.example.json"
DEMO_PATH = DATA_DIR / "demo_mjai.json"
WEB_DIR = ROOT_DIR / "web"

for d in (DATA_DIR, CACHE_DIR, RECORDS_DIR, MODELS_DIR):
    d.mkdir(parents=True, exist_ok=True)

ms_client = MajsoulClient(CACHE_DIR)
_ms_client_lock = SessionLock()

