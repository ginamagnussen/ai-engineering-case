"""
Score a pipeline run against the generator's answer key (data/ground_truth).

This is the ONLY module allowed to read ground truth. Metrics:
  * matching precision (every auto-match pairs lines of one real transaction) and recall
    (share of auto-matchable transactions fully matched by rules)
  * exception coverage (every real break surfaces in some case)
  * case classification accuracy - LLM vs rules baseline, plus a confusion list
  * trust metric: accuracy of cases the policy would auto-accept (no human review)
  * counterparty resolution accuracy for lines the master data could not resolve
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from . import config

AUTO = {"clean", "fuzzy_ref", "split", "timing"}


def case_truth(cases: list[dict]) -> list[str]:
    """True break category per case ('auto:<cat>' if rules should have matched it, 'unknown' if none)."""
    lab = pd.read_csv(config.GROUND_TRUTH / "labels.csv", dtype=str, keep_default_na=False)
    line_tx = {k: r.tx_id for r in lab.itertuples()
               for k in (r.seller_line_ids + "|" + r.buyer_line_ids).split("|") if k}
    cat = dict(zip(lab.tx_id, lab.category))
    out = []
    for c in cases:
        txs = [line_tx[k] for k in c["line_keys"] if k in line_tx]
        t = cat[txs[0]] if txs else "unknown"
        out.append(f"auto:{t}" if t in AUTO else t)
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion - honest error bars for small samples."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(centre - half, 3), round(centre + half, 3))


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.1f}%)" if b else "n/a"


def evaluate(lines: pd.DataFrame, matches: list[dict], cases: list[dict]) -> tuple[dict, str]:
    lab = pd.read_csv(config.GROUND_TRUTH / "labels.csv", dtype=str, keep_default_na=False)
    tx_lines, line_tx = {}, {}
    for r in lab.itertuples():
        keys = [k for k in (r.seller_line_ids + "|" + r.buyer_line_ids).split("|") if k]
        tx_lines[r.tx_id] = set(keys)
        for k in keys:
            line_tx[k] = r.tx_id
    cat = dict(zip(lab.tx_id, lab.category))

    # --- matching -------------------------------------------------------------------------
    correct, covered, wrong = 0, set(), []
    for m in matches:
        keys = m["seller_lines"] + m["buyer_lines"]
        txs = {line_tx.get(k) for k in keys}
        if len(txs) == 1 and None not in txs:
            correct += 1
            t = txs.pop()
            if set(keys) == tx_lines[t]:
                covered.add(t)
        else:
            wrong.append(m["match_id"])
    auto_tx = [t for t, c in cat.items() if c in AUTO]
    recall = sum(t in covered for t in auto_tx)
    on_exceptions = Counter(cat[line_tx[m["seller_lines"][0]]] for m in matches
                            if line_tx.get(m["seller_lines"][0]) and cat[line_tx[m["seller_lines"][0]]] not in AUTO)

    # --- exceptions -----------------------------------------------------------------------
    exc_tx = {t for t, c in cat.items() if c not in AUTO}
    in_cases = set()
    rows = []
    for c in cases:
        txs = [line_tx.get(k) for k in c["line_keys"] if line_tx.get(k)]
        true = cat[txs[0]] if txs else "unknown"
        in_cases.update(txs)
        llm_cat = c["assessment"]["category"] if c["assessment"] else None
        rows.append({"case": c["case_id"], "true": true, "llm": llm_cat, "baseline": c["baseline"]["category"],
                     "review": c["needs_review"]})
    scorable = [r for r in rows if r["true"] not in AUTO and r["true"] != "unknown"]
    with_llm = [r for r in scorable if r["llm"]]
    llm_ok = sum(r["llm"] == r["true"] for r in with_llm)
    base_ok = sum(r["baseline"] == r["true"] for r in scorable)
    auto_accept = [r for r in with_llm if not r["review"]]
    auto_ok = sum(r["llm"] == r["true"] for r in auto_accept)
    confusion = Counter((r["true"], r["llm"]) for r in with_llm if r["llm"] != r["true"])
    base_conf = Counter((r["true"], r["baseline"]) for r in scorable if r["baseline"] != r["true"])
    per_cat = {}
    for r in scorable:
        d = per_cat.setdefault(r["true"], {"n": 0, "llm_ok": 0, "llm_n": 0, "base_ok": 0})
        d["n"] += 1
        d["base_ok"] += r["baseline"] == r["true"]
        if r["llm"]:
            d["llm_n"] += 1
            d["llm_ok"] += r["llm"] == r["true"]

    # --- counterparty resolution ----------------------------------------------------------
    labrow = lab.set_index("tx_id")
    unres = lines[lines.cp_method != "master_data"]
    cp_ok = 0
    for r in unres.itertuples():
        t = line_tx.get(r.line_key)
        if not t:
            continue
        lr = labrow.loc[t]
        truth = lr.buyer if r.side == "REC" else (lr.wrong_counterparty or lr.seller)
        cp_ok += r.counterparty == truth

    m = {
        "matches": len(matches), "match_precision": _pct(correct, len(matches)),
        "auto_matchable_recall": _pct(recall, len(auto_tx)),
        "matches_on_exception_transactions": dict(on_exceptions),
        "exception_coverage": _pct(len(exc_tx & in_cases), len(exc_tx)),
        "cases": len(cases), "cases_scorable": len(scorable),
        "llm_accuracy": _pct(llm_ok, len(with_llm)), "baseline_accuracy": _pct(base_ok, len(scorable)),
        "cases_needing_review": _pct(sum(r["review"] for r in rows), len(rows)),
        "auto_accepted_accuracy": _pct(auto_ok, len(auto_accept)),
        "counterparty_resolution_accuracy": _pct(cp_ok, len(unres)),
    }

    md = ["# Evaluation report", "", "| Metric | Value |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in m.items()]
    md += ["", "## Accuracy by true category", "", "| Category | Cases | LLM | Rules baseline |", "|---|---|---|---|"]
    for k, d in sorted(per_cat.items(), key=lambda kv: -kv[1]["n"]):
        md.append(f"| {k} | {d['n']} | {_pct(d['llm_ok'], d['llm_n'])} | {_pct(d['base_ok'], d['n'])} |")
    md += ["", "## LLM errors (true -> predicted)", ""]
    md += [f"- {t} -> {p}: {n}" for (t, p), n in confusion.most_common()] or ["- none"]
    md += ["", "## Baseline errors (true -> predicted)", ""]
    md += [f"- {t} -> {p}: {n}" for (t, p), n in base_conf.most_common()] or ["- none"]
    if wrong:
        md += ["", f"## Incorrect auto-matches: {', '.join(wrong)}"]
    missed = sorted(exc_tx - in_cases)
    if missed:
        md += ["", "## Exception transactions not surfaced in any case", ""]
        md += [f"- {t} ({cat[t]})" for t in missed]
    unknown = [r["case"] for r in rows if r["true"] in AUTO]
    if unknown:
        md += ["", f"## Cases that rules should have auto-matched: {', '.join(unknown)}"]
    return m, "\n".join(md) + "\n"
