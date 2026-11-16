"""
Human review decisions and the audit trail.

Append-only JSONL log. Decisions are keyed by the case's line set (not its case number), so a
decision survives pipeline re-runs that renumber cases. In production this is a database
table with the reviewer's SSO identity; overrides double as labelled data for evaluation and
prompt improvement.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from . import config

LOG = config.OUT / "audit_log.jsonl"
ACTIONS = {"approve": "approved", "override": "overridden", "escalate": "escalated"}


def case_key(case: dict) -> str:
    return "|".join(sorted(case["line_keys"]))


def record(case: dict, action: str, reviewer: str, comment: str = "", new_category: Optional[str] = None) -> dict:
    a = case.get("assessment") or {}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reviewer": reviewer, "case_key": case_key(case), "case_id": case["case_id"],
        "action": action, "status": ACTIONS[action],
        "ai_category": a.get("category"), "ai_confidence": a.get("confidence"),
        "final_category": new_category if action == "override" else a.get("category"),
        "proposed_adjustment": a.get("adjustment_description"), "comment": comment,
        "model": config.MODEL, "prompt_version": config.PROMPT_VERSION,
    }
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_log() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def latest_decisions() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in load_log():
        out[e["case_key"]] = e
    return out
