"""Central configuration. Everything tunable lives here or in environment variables."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: load ANTHROPIC_API_KEY etc. from a local .env (never committed)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
REFERENCE = DATA / "reference"
GROUND_TRUTH = DATA / "ground_truth"  # used ONLY by recon.evaluate
OUT = ROOT / "out"
LLM_CACHE = ROOT / "cache" / "llm"  # committed, so the demo replays without an API key

# --- LLM -------------------------------------------------------------------------------
MODEL = os.getenv("RECON_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("RECON_EFFORT", "medium")
MAX_WORKERS = int(os.getenv("RECON_MAX_WORKERS", "8"))
PROMPT_VERSION = "v1"  # bump to invalidate cached LLM answers when prompts change
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}  # Opus 5.5

# --- Matching rules --------------------------------------------------------------------
AMOUNT_TOL = 0.01          # absolute tolerance in transaction currency
DATE_WINDOW_IN_PERIOD = 10  # days buyer may post after seller (same period)
DATE_WINDOW_TIMING = 15     # days for a cross-period (cut-off) match
SPLIT_MAX_PARTS = 3
SPLIT_MAX_CANDIDATES = 12

# --- Human-in-the-loop policy (deterministic, NOT left to the model) ---------------------
REVIEW_CONFIDENCE_THRESHOLD = 0.75
ALWAYS_REVIEW_CATEGORIES = {"dispute", "other"}
MATERIALITY_USD = 100_000  # any proposed adjustment above this is reviewed by a controller
ENTITY_RESOLUTION_MIN_CONFIDENCE = 0.80
