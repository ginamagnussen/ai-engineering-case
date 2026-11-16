"""
Turn unmatched lines into exception *cases* and attach deterministic evidence.

Design principle: code computes facts (differences, ratios, FX rate comparisons, digit
patterns, duplicate twins, search results); the LLM reasons over those facts. LLMs are bad at
arithmetic and good at judgement over messy, multilingual context - so we split the work
that way.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from .ingest import Context
from .match import ref_digits, ref_match

WHT_RATES = (0.05, 0.15, 0.20)


def _view(r: dict, ctx: Context) -> dict:
    erp = ctx.entities.set_index("entity_code").erp.get(r["entity"], "")
    return {
        "line_key": r["line_key"], "entity": r["entity"], "erp": erp,
        "account_type": "IC receivable" if r["side"] == "REC" else "IC payable",
        "posting_date": r["posting_date"].isoformat(),
        "period": "in period" if r["in_period"] else "after period end",
        "counterparty_as_booked": r["cp_raw"],
        "counterparty_resolved": r["counterparty"], "counterparty_resolution": r["cp_method"],
        "amount": r["amount"], "currency": r["currency"], "amount_usd": r["amount_usd"],
        "reference": r["ref"], "description": r["desc"],
    }


def _digit_pattern(a: float, b: float) -> str | None:
    x, y = f"{a:.2f}".replace(".", ""), f"{b:.2f}".replace(".", "")
    if len(x) != len(y) or x == y:
        return None
    diff = [i for i in range(len(x)) if x[i] != y[i]]
    if len(diff) == 2 and diff[1] == diff[0] + 1 and x[diff[0]] == y[diff[1]] and x[diff[1]] == y[diff[0]]:
        return "adjacent digits transposed"
    if len(diff) == 1:
        return "single digit differs"
    return None


def _pair_evidence(s: dict, b: dict, ctx: Context) -> dict:
    d = s["posting_date"]
    b_usd = b["amount"] * ctx.usd(b["currency"], d)
    ratio = b_usd / s["amount_usd"] if s["amount_usd"] else None
    ev = {
        "invoice_reference_found_on_buyer_line": ref_match(s["ref"], b),
        "days_between_postings": (b["posting_date"] - d).days,
        "buyer_posted_after_period_end": not b["in_period"],
        "buyer_booked_counterparty": b["counterparty"] or f"unresolved ('{b['cp_raw']}')",
        "expected_buyer_counterparty": s["entity"],
        "counterparty_mismatch": b["counterparty"] != s["entity"],
        "difference_usd_at_invoice_date_rate": round(s["amount_usd"] - b_usd, 2),
        "buyer_to_seller_ratio": round(ratio, 5) if ratio else None,
    }
    if ratio:
        gap = 1 - ratio
        hit = [r for r in WHT_RATES if abs(gap - r) < 0.002]
        ev["ratio_consistent_with_withholding_rate"] = f"{int(hit[0] * 100)}%" if hit else None
    if s["currency"] == b["currency"]:
        ev["digit_pattern"] = _digit_pattern(s["amount"], b["amount"])
    else:
        implied = b["amount"] / s["amount"]
        bases = {
            "daily rate at invoice date": ctx.usd(s["currency"], d) / ctx.usd(b["currency"], d),
            "daily rate at buyer posting date": (ctx.usd(s["currency"], b["posting_date"])
                                                 / ctx.usd(b["currency"], b["posting_date"])),
            "month-end rate": ctx.usd(s["currency"], ctx.period_end) / ctx.usd(b["currency"], ctx.period_end),
            "2026 budget rate": ctx.fx_budget[s["currency"]] / ctx.fx_budget[b["currency"]],
        }
        dev = {k: round((implied / v - 1) * 100, 3) for k, v in bases.items()}
        ev["fx"] = {"seller_currency": s["currency"], "buyer_currency": b["currency"],
                    "buyer_implied_rate": round(implied, 6),
                    "deviation_pct_vs_rate_basis": dev,
                    "closest_rate_basis": min(dev, key=lambda k: abs(dev[k]))}
    return ev


def _nearest(pool: list[dict], target_usd: float, ctx: Context, n: int = 3) -> list[dict]:
    ranked = sorted(pool, key=lambda r: abs(r["amount_usd"] - target_usd))[:n]
    return [{**_view(r, ctx), "ratio_to_target": round(r["amount_usd"] / target_usd, 4)} for r in ranked]


def _twin(r: dict, matched_rows: list[dict], match_of: dict) -> dict | None:
    for m in matched_rows:
        if (m["entity"] == r["entity"] and m["side"] == r["side"] and m["counterparty"] == r["counterparty"]
                and m["currency"] == r["currency"] and abs(m["amount"] - r["amount"]) < 0.01
                and ref_digits(m["ref"]) == ref_digits(r["ref"])):
            return {"line_key": m["line_key"], "posting_date": m["posting_date"].isoformat(),
                    "reference": m["ref"], "already_matched_in": match_of[m["line_key"]]}
    return None


def build_cases(lines: pd.DataFrame, matches: list[dict], ctx: Context) -> list[dict]:
    match_of = {k: m["match_id"] for m in matches for k in m["seller_lines"] + m["buyer_lines"]}
    rows = lines.to_dict("records")
    matched_rows = [r for r in rows if r["line_key"] in match_of]
    open_rows = [r for r in rows if r["line_key"] not in match_of]
    sellers = [r for r in open_rows if r["side"] == "REC" and r["in_period"]]
    buyers = [r for r in open_rows if r["side"] == "PAY"]  # incl. after-period lines as link targets

    # --- link unmatched seller lines to their most plausible unmatched buyer line ----------
    scored = []
    for s in sellers:
        for b in buyers:
            if b["entity"] == s["entity"] or (s["counterparty"] and b["entity"] != s["counterparty"]):
                continue
            rm = ref_match(s["ref"], b)
            cp_ok = b["counterparty"] == s["entity"]
            ratio = b["amount"] * ctx.usd(b["currency"], s["posting_date"]) / s["amount_usd"]
            near = 0.7 <= ratio <= 1.3
            days = abs((b["posting_date"] - s["posting_date"]).days)
            if not (rm or (cp_ok and near and days <= 20)):
                continue
            score = 3 * rm + cp_ok + (2 if abs(ratio - 1) < 1e-3 else near) + 0.5 * (days <= 10)
            scored.append((score, s["line_key"], b["line_key"], s, b))
    scored.sort(key=lambda t: -t[0])
    used, pairs = set(), []
    for _, sk, bk, s, b in scored:
        if sk in used or bk in used:
            continue
        used |= {sk, bk}
        pairs.append((s, b))

    cases = []

    def new_case(kind, seller, buyer, case_lines, evidence):
        cases.append({"case_id": f"C{len(cases) + 1:04d}", "kind": kind, "seller": seller, "buyer": buyer,
                      "lines": [_view(r, ctx) for r in case_lines], "evidence": evidence,
                      "line_keys": [r["line_key"] for r in case_lines]})

    for s, b in pairs:
        new_case("pair", s["entity"], b["entity"], [s, b], _pair_evidence(s, b, ctx))

    for s in sellers:
        if s["line_key"] in used:
            continue
        buyer = s["counterparty"]
        pool = [b for b in buyers if b["line_key"] not in used and (buyer is None or b["entity"] == buyer)
                and b["entity"] != s["entity"]]
        same_ref_elsewhere = [_view(r, ctx) | {"matched_in": match_of.get(r["line_key"])} for r in rows
                              if r["entity"] != s["entity"] and ref_match(s["ref"], r)]
        new_case("seller_only", s["entity"], buyer, [s], {
            "counterparty_unresolved": buyer is None,
            "buyer_unmatched_lines_searched": len(pool),
            "nearest_unmatched_buyer_lines_by_amount": _nearest(pool, s["amount_usd"], ctx),
            "lines_elsewhere_with_same_reference": same_ref_elsewhere,
            "possible_duplicate_of": _twin(s, matched_rows, match_of),
        })

    for b in buyers:
        if b["line_key"] in used or not b["in_period"]:
            continue  # unmatched November lines are next period's business
        seller = b["counterparty"]
        pool = [s for s in sellers if s["line_key"] not in used and (seller is None or s["entity"] == seller)]
        new_case("buyer_only", seller, b["entity"], [b], {
            "counterparty_unresolved": seller is None,
            "seller_unmatched_lines_searched": len(pool),
            "nearest_unmatched_seller_lines_by_amount": _nearest(pool, b["amount_usd"], ctx),
            "possible_duplicate_of": _twin(b, matched_rows, match_of),
        })
    return cases
