"""
Deterministic matching. Cheap, explainable, auditable - and handles the bulk of volume.

For every ordered pair (seller S, buyer B) we match S's IC receivable lines booked against B
with B's IC payable lines booked against S:

  P1 exact_ref    same currency + amount + invoice reference found on buyer side
  P2 amount_date  same currency + amount within a date window (reference missing/garbled);
                  ambiguity (recurring fixed fees) lowers confidence
  P3 split        2-3 buyer lines posted the same day that sum to the seller amount
  A buyer line posted after period end is a cross-period match -> match_type "timing".
"""
from __future__ import annotations

import re
from itertools import combinations

import pandas as pd

from . import config


def ref_digits(s: str) -> str:
    return "".join(re.findall(r"\d", s or "")).lstrip("0")


def ref_alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def ref_match(seller_ref: str, buyer: dict) -> bool:
    """Does the buyer line carry the seller's invoice reference (in any common re-keyed form)?

    M4 fix: the digits-only rule alone missed every AE/SA reference ('AE-ICINV-00599' -> '599',
    below the 4-digit minimum), silently pushing those invoices into amount-only matching.
    """
    sa = ref_alnum(seller_ref)
    if len(sa) >= 6 and (sa in ref_alnum(buyer["ref"]) or any(sa in ref_alnum(t) for t in buyer["desc"].split())):
        return True
    sd = ref_digits(seller_ref)
    bd = ref_digits(buyer["ref"])
    if len(sd) >= 4:
        return sd in bd or any(sd in ref_digits(tok) for tok in buyer["desc"].split())
    return len(sd) >= 3 and sd == bd  # short numbers: exact digit equality only


def _days(s: dict, b: dict) -> int:
    return (b["posting_date"] - s["posting_date"]).days


def _in_window(s: dict, b: dict) -> bool:
    d = _days(s, b)
    if b["in_period"]:
        return -3 <= d <= config.DATE_WINDOW_IN_PERIOD
    return 0 <= d <= config.DATE_WINDOW_TIMING


def _same_amount(a: float, b: float, n: int = 1) -> bool:
    return abs(a - b) <= config.AMOUNT_TOL * n


def run_matching(lines: pd.DataFrame) -> list[dict]:
    known = lines[lines.counterparty.notna()]
    rec = known[(known.side == "REC") & known.in_period]
    pay = known[known.side == "PAY"]
    matches: list[dict] = []
    all_rec = {e: g.to_dict("records") for e, g in lines[lines.side == "REC"].groupby("entity")}

    def add(s, bs, rule, conf):
        for b in bs:
            b["_used"] = True
        s["_used"] = True
        mtype = "timing" if any(not b["in_period"] for b in bs) else rule
        matches.append({
            "match_id": f"M{len(matches) + 1:04d}", "seller": s["entity"], "buyer": bs[0]["entity"],
            "match_type": mtype, "rule": rule, "confidence": conf,
            "seller_lines": [s["line_key"]], "buyer_lines": [b["line_key"] for b in bs],
            "amount": s["amount"], "currency": s["currency"], "amount_usd": s["amount_usd"],
            "seller_date": s["posting_date"].isoformat(),
            "buyer_dates": ",".join(sorted({b["posting_date"].isoformat() for b in bs})),
        })

    for (seller, buyer), sel_df in rec.groupby(["entity", "counterparty"]):
        sel = sel_df.sort_values("posting_date").to_dict("records")
        buy = pay[(pay.entity == buyer) & (pay.counterparty == seller)].to_dict("records")
        for r in sel + buy:
            r["_used"] = False

        # P1: exact amount + reference
        for s in sel:
            c = [b for b in buy if not b["_used"] and b["currency"] == s["currency"]
                 and _same_amount(b["amount"], s["amount"]) and ref_match(s["ref"], b)]
            if c:
                add(s, [min(c, key=lambda b: abs(_days(s, b)))], "exact_ref", 0.99)

        # P2: exact amount within date window. Resolve the least ambiguous sellers first.
        # Guard (added after an observed false positive): never amount-match a buyer line whose
        # reference points at a *different* seller invoice - equal amounts across invoices are
        # common (recurring fees, net-of-WHT amounts that coincide with another gross amount).
        # Checks ALL the seller's invoices, incl. lines whose counterparty is unresolved
        # (held-out run showed a false positive when the referenced invoice was unresolved).
        def points_elsewhere(s, b):
            return any(o["line_key"] != s["line_key"] and ref_match(o["ref"], b) for o in all_rec[seller])

        def cands(s):
            return [b for b in buy if not b["_used"] and b["currency"] == s["currency"]
                    and _same_amount(b["amount"], s["amount"]) and _in_window(s, b)
                    and not points_elsewhere(s, b)]

        open_s = [s for s in sel if not s["_used"]]
        for s in sorted(open_s, key=lambda s: len(cands(s))):
            c = cands(s)
            if c:
                add(s, [min(c, key=lambda b: abs(_days(s, b)))], "amount_date", 0.9 if len(c) == 1 else 0.75)

        # P3: one-to-many split (same posting date, sums to seller amount)
        for s in sel:
            if s["_used"]:
                continue
            c = [b for b in buy if not b["_used"] and b["currency"] == s["currency"]
                 and b["amount"] < s["amount"] and _in_window(s, b)][: config.SPLIT_MAX_CANDIDATES]
            by_day: dict = {}
            for b in c:
                by_day.setdefault(b["posting_date"], []).append(b)
            found = None
            for group in by_day.values():
                for k in range(2, config.SPLIT_MAX_PARTS + 1):
                    for combo in combinations(group, k):
                        if _same_amount(sum(b["amount"] for b in combo), s["amount"], k):
                            found = list(combo)
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                add(s, found, "split", 0.85)

    return matches
