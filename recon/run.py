"""
End-to-end pipeline run.

    python -m recon.run              # full run (uses the API when credentials exist, else cache)
    python -m recon.run --no-llm     # rules only, never calls the API
    python -m recon.run --limit 5    # at most 5 new API calls for case classification (smoke test)
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter

import pandas as pd

from . import config, llm
from .cases import build_cases
from .classify import classify_cases
from .entities import EntityMaster, resolve_counterparties
from .evaluate import evaluate
from .ingest import load_context, load_lines
from .match import run_matching


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="rules only; never call the API")
    ap.add_argument("--limit", type=int, default=None, help="max new API calls for case classification")
    ap.add_argument("--no-eval", action="store_true")
    a = ap.parse_args()

    use_llm = not a.no_llm and llm.credentials_available()
    if not a.no_llm and not use_llm:
        print("No ANTHROPIC_API_KEY found: replaying cached LLM answers only.")
    t0 = time.time()

    ctx = load_context()
    lines = load_lines(ctx)
    print(f"Ingested {len(lines)} lines from {lines.entity.nunique()} entity extracts")

    lines = resolve_counterparties(lines, EntityMaster(ctx.entities), use_llm)
    print("Counterparty resolution:", dict(Counter(lines.cp_method)))

    matches = run_matching(lines)
    print(f"Rule-based matches: {len(matches)}  {dict(Counter(m['match_type'] for m in matches))}")

    cases = build_cases(lines, matches, ctx)
    print(f"Exception cases: {len(cases)}  {dict(Counter(c['kind'] for c in cases))}")

    cases = classify_cases(cases, use_llm, a.limit)
    n_rev = sum(c["needs_review"] for c in cases)
    print(f"Classified. Needs human review: {n_rev}/{len(cases)}")
    print("LLM usage:", llm.USAGE.as_dict())

    config.OUT.mkdir(exist_ok=True)
    lines.to_csv(config.OUT / "lines.csv", index=False)
    pd.DataFrame(matches).to_csv(config.OUT / "matches.csv", index=False)
    (config.OUT / "cases.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False, default=str),
                                           encoding="utf-8")
    summary = {"period": ctx.manifest["period"], "lines": len(lines), "matches": len(matches),
               "cases": len(cases), "needs_review": n_rev, "llm": llm.USAGE.as_dict(),
               "runtime_s": round(time.time() - t0, 1)}

    if not a.no_eval:
        metrics, md = evaluate(lines, matches, cases)
        summary["eval"] = metrics
        (config.OUT / "eval.md").write_text(md, encoding="utf-8")
        print("\n" + md)
    (config.OUT / "run_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(f"Outputs written to {config.OUT}  ({summary['runtime_s']}s)")


if __name__ == "__main__":
    main()
