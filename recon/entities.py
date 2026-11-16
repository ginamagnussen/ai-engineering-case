"""
Counterparty resolution: map whatever each ERP calls the partner entity to an entity code.

Tier 1 (deterministic): Oracle segment / SAP trading partner / legal name / known aliases.
Tier 2 (LLM, only for strings tier 1 can't resolve): one batched call with the master data
and sample lines as evidence. Answers below the confidence threshold stay unresolved and go
to a human; confirmed answers would be written back to the alias table in production, so the
LLM is needed less every month.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from . import config, llm


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


OVERRIDES_FILE = config.REFERENCE / "alias_overrides.csv"  # written by the review UI


def load_confirmed_aliases() -> dict[str, str]:
    """Aliases confirmed by a human in the review UI (production: written back to MDM)."""
    if not OVERRIDES_FILE.exists():
        return {}
    df = pd.read_csv(OVERRIDES_FILE, dtype=str, keep_default_na=False)
    return {_norm(a): c for a, c in zip(df.alias, df.entity_code)}


def confirm_alias(alias: str, entity_code: str, confirmed_by: str) -> None:
    from datetime import datetime, timezone

    row = pd.DataFrame([{"alias": alias, "entity_code": entity_code, "confirmed_by": confirmed_by,
                         "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}])
    if OVERRIDES_FILE.exists():
        old = pd.read_csv(OVERRIDES_FILE, dtype=str, keep_default_na=False)
        row = pd.concat([old[old.alias.map(_norm) != _norm(alias)], row], ignore_index=True)
    row.to_csv(OVERRIDES_FILE, index=False)


class EntityMaster:
    def __init__(self, ents: pd.DataFrame):
        self.codes = list(ents.entity_code)
        self.confirmed = load_confirmed_aliases()
        self.maps: dict[str, dict[str, str]] = {
            "oracle_segment": dict(zip(ents.oracle_segment, ents.entity_code)),
            "sap_trading_partner": dict(zip(ents.sap_trading_partner, ents.entity_code)),
            "legal_name": {_norm(n): c for n, c in zip(ents.legal_name, ents.entity_code)},
        }
        free = dict(self.maps["legal_name"])
        for code, aliases in zip(ents.entity_code, ents.known_aliases):
            free[_norm(code)] = code
            for a in filter(None, aliases.split("|")):
                free[_norm(a)] = code
        self.maps["free_text"] = free
        self.table = ents[["entity_code", "legal_name", "country", "functional_currency",
                           "known_aliases"]].to_dict("records")

    def resolve(self, raw: str, kind: str) -> Optional[str]:
        key = raw if kind in ("oracle_segment", "sap_trading_partner") else _norm(raw)
        return self.maps[kind].get(key)


class Resolution(BaseModel):
    raw: str = Field(description="The counterparty string exactly as given")
    entity_code: Optional[str] = Field(description="Matching entity_code, or null if not determinable")
    confidence: float = Field(description="0.0-1.0, calibrated probability the mapping is correct")
    reasoning: str = Field(description="One sentence citing the evidence used")


class Resolutions(BaseModel):
    items: list[Resolution]


SYSTEM = """You map free-text intercompany counterparty names, typed by accounting staff in a \
legacy ledger, to entity codes in a group's entity master. Use the legal names, countries, \
currencies, and the sample ledger lines (descriptions, invoice references, currencies) as \
evidence. Invoice reference prefixes often reveal the issuing entity. An entity never trades \
with itself. If the evidence does not clearly identify one entity, return null and a low \
confidence rather than guessing. Confidence must be a calibrated probability."""


def resolve_counterparties(lines: pd.DataFrame, master: EntityMaster, use_llm: bool) -> pd.DataFrame:
    lines = lines.copy()
    lines["counterparty"] = [master.resolve(r, k) for r, k in zip(lines.cp_raw, lines.cp_kind)]
    lines["cp_method"] = lines.counterparty.notna().map({True: "master_data", False: "unresolved"})
    lines["cp_confidence"] = lines.counterparty.notna().astype(float)
    lines["cp_reasoning"] = ""
    lines["cp_suggestion"] = None

    # The LLM payload is built from master-data gaps only (not from human confirmations), so it
    # stays byte-identical and its cached answer remains valid after a reviewer confirms an alias.
    todo = lines[lines.counterparty.isna()]

    # human-confirmed aliases outrank the LLM
    for i in todo.index:
        code = master.confirmed.get(_norm(lines.at[i, "cp_raw"]))
        if code:
            lines.at[i, "counterparty"] = code
            lines.at[i, "cp_method"] = "confirmed_alias"
            lines.at[i, "cp_confidence"] = 1.0
    if todo.empty:
        return lines

    groups = []
    for (ent, raw), g in todo.groupby(["entity", "cp_raw"]):
        groups.append({"booked_in_entity": ent, "raw_counterparty": raw, "occurrences": len(g),
                       "sample_lines": g.head(4)[["side", "currency", "ref", "desc"]].to_dict("records")})
    payload = json.dumps({"entity_master": master.table, "unresolved": groups}, ensure_ascii=False, indent=1)

    result = llm.structured_call(SYSTEM, payload, Resolutions, allow_api=use_llm)
    if result is None:
        return lines
    by_raw = {r.raw: r for r in result.items}
    for i in todo.index:
        if lines.at[i, "cp_method"] == "confirmed_alias":
            continue
        r = by_raw.get(lines.at[i, "cp_raw"])
        if (r and r.entity_code in master.codes and r.entity_code != lines.at[i, "entity"]
                and r.confidence >= config.ENTITY_RESOLUTION_MIN_CONFIDENCE):
            lines.at[i, "counterparty"] = r.entity_code
            lines.at[i, "cp_method"] = "llm"
            lines.at[i, "cp_confidence"] = r.confidence
            lines.at[i, "cp_reasoning"] = r.reasoning
        elif r:
            lines.at[i, "cp_reasoning"] = f"LLM below threshold ({r.confidence:.2f}): {r.reasoning}"
            lines.at[i, "cp_suggestion"] = r.entity_code
            lines.at[i, "cp_confidence"] = r.confidence
    return lines
