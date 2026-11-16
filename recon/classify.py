"""
Classify exception cases, explain them, and propose an action.

Two classifiers run side by side:
  * baseline_classify - hand-written rules over the evidence (free, instant). Kept on purpose:
    it is the bar the LLM must beat to justify its cost, and the fallback when the API is down.
  * llm_classify      - Claude with structured outputs. Adds judgement over free text
    (multilingual descriptions, dispute/accrual notes), explanations an accountant can act on,
    and calibrated confidence.
Whether a case needs a human is decided by `review_policy` (code), never by the model alone.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import config, llm

Category = Literal["fx_difference", "timing", "split", "miscoded_counterparty", "amount_typo", "duplicate",
                   "missing_buyer", "accrual_no_invoice", "wht", "dispute", "other"]


class CaseAssessment(BaseModel):
    category: Category
    confidence: float = Field(description="Calibrated probability (0.0-1.0) that the category is correct")
    explanation: str = Field(description="2-3 sentences for a group accountant, citing line keys and figures")
    responsible_entity: str = Field(description="Entity code that must act, or 'both' / 'group controller'")
    recommended_action: str = Field(description="Concrete next step, one sentence")
    adjustment_amount: Optional[float] = Field(description="Proposed correcting amount, or null if none")
    adjustment_currency: Optional[str] = Field(description="Currency of the adjustment, or null")
    adjustment_description: Optional[str] = Field(description="Correcting entry in plain words, or null")
    model_requests_review: bool = Field(description="True if a human must decide (ambiguous or judgement call)")
    review_reason: Optional[str] = Field(description="Why review is needed, or null")


SYSTEM = """You are an intercompany reconciliation analyst at NorthStar Infrastructure Group, a \
multinational engineering group whose six entities run different ERPs. Deterministic rules have \
already matched every straightforward pair of IC receivable (seller) and IC payable (buyer) lines \
for the October 2026 close. You receive one remaining exception case: its ledger lines plus \
evidence computed by code (differences, ratios, FX rate comparisons, digit patterns, duplicate \
twins, search results). Trust the computed numbers; do not redo arithmetic.

Classify the case into exactly one category:
- fx_difference: same invoice, buyer booked in another currency or at a rate other than the \
invoice-date rate; the difference is explained by the rate basis.
- timing: the other side posted after period end (cut-off); no correction needed this period.
- split: buyer recorded one invoice across several lines.
- miscoded_counterparty: a line is booked against the wrong intercompany partner.
- amount_typo: keying error (transposed or wrong digits) with no business reason.
- duplicate: one side booked the same invoice twice.
- missing_buyer: the seller invoiced but the buyer has no corresponding booking.
- accrual_no_invoice: the buyer accrued a cost the seller has not invoiced.
- wht: buyer booked net of withholding tax (e.g. Saudi WHT 5% services, 15% royalties/licences, \
20% management fees).
- dispute: buyer intentionally booked less because it contests part of the charge.
- other: none of the above fits or the evidence is insufficient.

Descriptions may be in German or abbreviated. A seller line alone means no buyer line was \
found; a buyer line alone means no seller line was found. Be calibrated: use confidence below \
0.7 when the evidence supports more than one category. Request human review for disputes, \
judgement calls, or anything you cannot verify from the evidence. Never invent ledger lines or \
amounts that are not in the case."""


def baseline_classify(case: dict) -> dict:
    ev, kind = case["evidence"], case["kind"]
    if kind == "seller_only":
        cat = "duplicate" if ev.get("possible_duplicate_of") else "missing_buyer"
    elif kind == "buyer_only":
        if ev.get("possible_duplicate_of"):
            cat = "duplicate"
        elif "accru" in case["lines"][0]["description"].lower():
            cat = "accrual_no_invoice"
        else:
            cat = "other"
    elif ev["counterparty_mismatch"]:
        cat = "miscoded_counterparty"
    elif ev["buyer_posted_after_period_end"]:
        cat = "timing"
    elif "fx" in ev:
        cat = "fx_difference"
    elif ev.get("ratio_consistent_with_withholding_rate"):
        cat = "wht"
    elif ev.get("digit_pattern"):
        cat = "amount_typo"
    elif (ev.get("buyer_to_seller_ratio") or 1) < 1:
        cat = "dispute"
    else:
        cat = "other"
    return {"category": cat}


def review_policy(case: dict, a: Optional[CaseAssessment]) -> tuple[bool, list[str]]:
    if a is None:
        return True, ["no model assessment (API unavailable or refused)"]
    reasons = []
    if a.confidence < config.REVIEW_CONFIDENCE_THRESHOLD:
        reasons.append(f"confidence {a.confidence:.2f} < {config.REVIEW_CONFIDENCE_THRESHOLD}")
    if a.category in config.ALWAYS_REVIEW_CATEGORIES:
        reasons.append(f"category '{a.category}' always needs a human")
    usd = max(abs(l["amount_usd"]) for l in case["lines"])
    if usd > config.MATERIALITY_USD:
        reasons.append(f"material amount (${usd:,.0f} > ${config.MATERIALITY_USD:,})")
    if a.model_requests_review:
        reasons.append(f"model: {a.review_reason or 'requested review'}")
    return bool(reasons), reasons


def _payload(case: dict) -> str:
    body = {k: case[k] for k in ("kind", "seller", "buyer", "lines", "evidence")}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1, default=str)


def classify_cases(cases: list[dict], use_llm: bool, api_limit: Optional[int] = None) -> list[dict]:
    """api_limit: only the first N cases may hit the API (cheap smoke tests); the rest use cache only."""
    def work(item):
        i, case = item
        allow = use_llm and (api_limit is None or i < api_limit)
        return case["case_id"], llm.structured_call(SYSTEM, _payload(case), CaseAssessment, allow_api=allow)

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        results = dict(pool.map(work, enumerate(cases)))

    for case in cases:
        a = results[case["case_id"]]
        case["baseline"] = baseline_classify(case)
        case["assessment"] = a.model_dump() if a else None
        case["needs_review"], case["review_reasons"] = review_policy(case, a)
    return cases
