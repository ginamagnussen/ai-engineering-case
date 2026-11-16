"""
M4 experiments: model comparison, confidence calibration, review-threshold sweep, error dump.

    python -m recon.experiments                 # uses cached answers; calls the API for missing ones
    python -m recon.experiments --no-llm        # cache only

Entity resolution and matching are held fixed (Opus answers from cache), so every model
classifies the identical set of exception cases. Results -> out/experiments.json.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics

from . import config, llm
from .cases import build_cases
from .classify import SYSTEM, CaseAssessment, _payload, classify_cases, review_policy
from .entities import EntityMaster, resolve_counterparties
from .evaluate import case_truth, wilson
from .ingest import load_context, load_lines
from .match import run_matching

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
THRESHOLDS = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
BUCKETS = [(0, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.01)]


def prepare():
    ctx = load_context()
    lines = resolve_counterparties(load_lines(ctx), EntityMaster(ctx.entities), use_llm=False)
    matches = run_matching(lines)
    return build_cases(lines, matches, ctx)


def score(cases: list[dict], truth: list[str], model: str) -> dict:
    idx = [i for i, t in enumerate(truth) if not t.startswith("auto:") and t != "unknown"]
    assessed = [i for i in idx if cases[i]["assessment"]]
    ok = [i for i in assessed if cases[i]["assessment"]["category"] == truth[i]]
    auto = [i for i in assessed if not cases[i]["needs_review"]]
    auto_ok = [i for i in auto if i in ok]

    recs = [llm.cached_record(SYSTEM, _payload(c), CaseAssessment) for c in cases]
    recs = [r for r in recs if r]
    tokens = {k: sum(r["usage"].get(k, 0) for r in recs) for k in ("input", "output", "cache_read", "cache_write")}
    lat = [r["latency_s"] for r in recs if "latency_s" in r]

    per_cat = {}
    for i in idx:
        d = per_cat.setdefault(truth[i], [0, 0])
        d[1] += 1
        d[0] += bool(cases[i]["assessment"]) and cases[i]["assessment"]["category"] == truth[i]

    # calibration: is stated confidence matched by observed accuracy?
    calib = []
    for lo, hi in BUCKETS:
        b = [i for i in assessed if lo <= cases[i]["assessment"]["confidence"] < hi]
        calib.append({"bucket": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(b),
                      "accuracy": round(sum(i in ok for i in b) / len(b), 3) if b else None,
                      "mean_confidence": round(statistics.mean(cases[i]["assessment"]["confidence"] for i in b), 3)
                      if b else None})

    # threshold sweep: review rate vs. errors that slip through un-reviewed
    sweep, orig = [], config.REVIEW_CONFIDENCE_THRESHOLD
    for t in THRESHOLDS:
        config.REVIEW_CONFIDENCE_THRESHOLD = t
        flags = {i: review_policy(cases[i], CaseAssessment(**cases[i]["assessment"]))[0] for i in assessed}
        n_auto = sum(not f for f in flags.values())
        slipped = sum(not flags[i] and i not in ok for i in assessed)
        sweep.append({"threshold": t, "review_rate": round(1 - n_auto / len(assessed), 3),
                      "auto_accepted": n_auto, "errors_auto_accepted": slipped,
                      "auto_accept_accuracy": round(1 - slipped / n_auto, 3) if n_auto else None})
    config.REVIEW_CONFIDENCE_THRESHOLD = orig

    errors = [{"case": cases[i]["case_id"], "kind": cases[i]["kind"], "true": truth[i],
               "predicted": cases[i]["assessment"]["category"], "confidence": cases[i]["assessment"]["confidence"],
               "flagged_for_review": cases[i]["needs_review"],
               "explanation": cases[i]["assessment"]["explanation"]} for i in assessed if i not in ok]

    return {
        "model": model, "scorable_cases": len(idx), "assessed": len(assessed),
        "accuracy": round(len(ok) / len(assessed), 3) if assessed else None,
        "accuracy_ci95": wilson(len(ok), len(assessed)),
        "review_rate": round(sum(cases[i]["needs_review"] for i in range(len(cases))) / len(cases), 3),
        "auto_accepted": len(auto), "auto_accept_accuracy": round(len(auto_ok) / len(auto), 3) if auto else None,
        "auto_accept_ci95": wilson(len(auto_ok), len(auto)),
        "cost_usd_all_cases": llm.cost_of(tokens, model), "cost_usd_per_case": round(llm.cost_of(tokens, model) / max(len(recs), 1), 4),
        "tokens": tokens, "mean_latency_s": round(statistics.mean(lat), 1) if lat else None,
        "p90_latency_s": round(sorted(lat)[int(0.9 * len(lat)) - 1], 1) if len(lat) >= 10 else None,
        "per_category": {k: f"{v[0]}/{v[1]}" for k, v in sorted(per_cat.items())},
        "calibration": calib, "threshold_sweep": sweep, "errors": errors,
    }


def cascade(cheap: list[dict], strong: list[dict], truth: list[str], cheap_m: str, strong_m: str) -> dict:
    """Simulate 'cheap model first, escalate to strong model when the review policy flags it'."""
    final, cost_tokens = [], {"cheap": 0.0, "strong": 0.0}
    for c, s in zip(cheap, strong):
        rc = llm.cached_record(SYSTEM, _payload(c), CaseAssessment)
        cost_tokens["cheap"] += llm.cost_of(rc["usage"], cheap_m) if rc else 0
        if c["assessment"] and not c["needs_review"]:
            final.append(c)
        else:
            rs = llm.cached_record(SYSTEM, _payload(s), CaseAssessment)
            cost_tokens["strong"] += llm.cost_of(rs["usage"], strong_m) if rs else 0
            final.append(s)
    idx = [i for i, t in enumerate(truth) if not t.startswith("auto:") and t != "unknown"]
    ok = sum(final[i]["assessment"] and final[i]["assessment"]["category"] == truth[i] for i in idx)
    auto = [i for i in idx if not final[i]["needs_review"]]
    auto_ok = sum(final[i]["assessment"]["category"] == truth[i] for i in auto)
    escalated = sum(f is s for f, s in zip(final, strong))
    total = cost_tokens["cheap"] + cost_tokens["strong"]
    return {"model": f"cascade {cheap_m} -> {strong_m}", "escalated": escalated,
            "accuracy": round(ok / len(idx), 3), "accuracy_ci95": wilson(ok, len(idx)),
            "review_rate": round(sum(f["needs_review"] for f in final) / len(final), 3),
            "auto_accepted": len(auto), "auto_accept_accuracy": round(auto_ok / len(auto), 3) if auto else None,
            "cost_usd_all_cases": round(total, 4), "cost_usd_per_case": round(total / len(final), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--models", nargs="*", default=MODELS)
    a = ap.parse_args()
    use_llm = not a.no_llm and llm.credentials_available()

    base = prepare()
    truth = case_truth(base)
    results, orig, classified = [], config.MODEL, {}
    for m in a.models:
        config.MODEL = m
        llm.USAGE.__init__()
        cases = classified[m] = classify_cases(copy.deepcopy(base), use_llm)
        r = score(cases, truth, m)
        r["this_run"] = llm.USAGE.as_dict()
        results.append(r)
        print(f"{m:20s} acc={r['accuracy']} {r['accuracy_ci95']}  review={r['review_rate']}  "
              f"auto_acc={r['auto_accept_accuracy']} (n={r['auto_accepted']})  "
              f"cost/case=${r['cost_usd_per_case']}  latency={r['mean_latency_s']}s  "
              f"new_calls={r['this_run']['api_calls']} errors={r['this_run']['errors']}")
    config.MODEL = orig

    cheap, strong = "claude-haiku-5-5", "claude-opus-5-5"
    if cheap in classified and strong in classified:
        c = cascade(classified[cheap], classified[strong], truth, cheap, strong)
        results.append(c)
        print(f"{'cascade haiku->opus':20s} acc={c['accuracy']} {c['accuracy_ci95']}  review={c['review_rate']}  "
              f"auto_acc={c['auto_accept_accuracy']} (n={c['auto_accepted']})  cost/case=${c['cost_usd_per_case']}  "
              f"escalated={c['escalated']}")

    config.OUT.mkdir(exist_ok=True)
    (config.OUT / "experiments.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"-> {config.OUT / 'experiments.json'}")


if __name__ == "__main__":
    main()
