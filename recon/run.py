"""
End-to-end pipeline run.

    python -m recon.run              # full run (uses the API when credentials exist, else cache)
    python -m recon.run --no-llm     # never call the API (cached answers are still replayed)
    python -m recon.run --limit 5    # at most 5 new API calls for case classification (smoke test)
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from typing import Callable, Optional

import pandas as pd

from . import config, llm
from .cases import build_cases
from .classify import classify_cases
from .entities import EntityMaster, resolve_counterparties
from .evaluate import evaluate
from .ingest import load_context, load_lines
from .match import run_matching


def run_pipeline(use_llm: bool, api_limit: Optional[int] = None, do_eval: bool = True,
                 log: Callable[[str], None] = print) -> dict:
    llm.USAGE.__init__()  # fresh counters per run (the UI may run several times)
    t0 = time.time()

    ctx = load_context()
    lines = load_lines(ctx)
    log(f"Ingested {len(lines)} lines from {lines.entity.nunique()} entity extracts")

    lines = resolve_counterparties(lines, EntityMaster(ctx.entities), use_llm)
    log(f"Counterparty resolution: {dict(Counter(lines.cp_method))}")

    matches = run_matching(lines)
    log(f"Rule-based matches: {len(matches)}  {dict(Counter(m['match_type'] for m in matches))}")

    cases = build_cases(lines, matches, ctx)
    log(f"Exception cases: {len(cases)}  {dict(Counter(c['kind'] for c in cases))}")

    cases = classify_cases(cases, use_llm, api_limit)
    n_rev = sum(c["needs_review"] for c in cases)
    log(f"Classified. Needs human review: {n_rev}/{len(cases)}")
    log(f"LLM usage: {llm.USAGE.as_dict()}")

    config.OUT.mkdir(exist_ok=True)
    lines.to_csv(config.OUT / "lines.csv", index=False)
    pd.DataFrame(matches).to_csv(config.OUT / "matches.csv", index=False)
    (config.OUT / "cases.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False, default=str),
                                           encoding="utf-8")
    summary = {"period": ctx.manifest["period"], "lines": len(lines), "matches": len(matches),
               "cases": len(cases), "needs_review": n_rev,
               "unexplained_usd": round(sum(max(abs(l["amount_usd"]) for l in c["lines"]) for c in cases), 2),
               "llm": llm.USAGE.as_dict(), "model": config.MODEL, "used_api": use_llm,
               "runtime_s": round(time.time() - t0, 1)}
    if do_eval:
        metrics, md = evaluate(lines, matches, cases)
        summary["eval"] = metrics
        (config.OUT / "eval.md").write_text(md, encoding="utf-8")
    (config.OUT / "run_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="never call the API; replay cached answers only")
    ap.add_argument("--limit", type=int, default=None, help="max new API calls for case classification")
    ap.add_argument("--no-eval", action="store_true")
    ap.add_argument("--prune-cache", action="store_true", help="delete cached LLM answers this run did not use")
    a = ap.parse_args()

    use_llm = not a.no_llm and llm.credentials_available()
    if not a.no_llm and not use_llm:
        print("No ANTHROPIC_API_KEY found: replaying cached LLM answers only.")
    summary = run_pipeline(use_llm, a.limit, not a.no_eval)
    if a.prune_cache:
        print(f"Pruned {llm.prune_cache()} stale cache entries")
    if not a.no_eval:
        print("\n" + (config.OUT / "eval.md").read_text(encoding="utf-8"))
    print(f"Outputs written to {config.OUT}  ({summary['runtime_s']}s)")


if __name__ == "__main__":
    main()
