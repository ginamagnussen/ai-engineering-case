# Evaluation report: IC Reconciliation Copilot

How we know the prototype works, where it fails, and what the numbers do and don't prove. Every figure below can be reproduced from this repository (see §8).

## 1. Method

**Ground truth.** The mock-data generator (`data_gen/`) writes an answer key next to the ledger extracts: one row per real transaction, with its break category and the ledger lines it produced. Only `recon/evaluate.py` and `recon/experiments.py` read it. The pipeline never does.

**Two datasets.**

| Set | Seed | Transactions / lines / real breaks | Used for |
|---|---|---|---|
| Development | 42 | 344 / 701 / 90 | Building and debugging |
| **Held-out** | 2027 | 353 / 724 / 102 | **Independent test** |

The held-out set was generated after the development fixes. One further fix (§5, item 6) was prompted by its rules-only run, and its pre-fix result is reported. The **model runs on the held-out set were made after all fixes, with no changes afterwards**, so they are a clean test.

**Metrics.**
- *Match precision:* share of rule-based auto-matches that pair lines from one real transaction. A false positive silently hides a break, so this is the key safety metric.
- *Auto-match recall:* share of transactions that should match automatically (clean, reference-only differences, splits, timing) and are fully matched.
- *Exception coverage:* share of real breaks that appear in at least one exception case.
- *Classification accuracy:* the model's break category against the true category, over exception cases with a real break. Each figure comes with a 95% Wilson interval, because the samples are small (89–102 cases).
- *Review rate:* share of cases the deterministic review policy sends to a human (confidence < 0.75, dispute or "other", amount over $100k, or the model asks for review).
- *Auto-accept accuracy:* accuracy on cases the policy would **not** send for review. This is the trust metric: these are the cases nobody looks at.

## 2. Headline results (default configuration: Claude Opus 5.5, medium effort)

| Metric | Development | **Held-out** |
|---|---|---|
| Match precision | 100% (245/245) | **100%** (245/245) |
| Auto-match recall | 93.3% | **96.0%** |
| ... after confirming 2 uncertain partner names in the UI | 98.4% (precision still 100%) | (not run) |
| Exception coverage | 100% (90/90) | **100%** (102/102) |
| Classification accuracy | 100% (89/89; CI 95.9–100) | **99.0%** (101/102; CI 94.7–99.8) |
| Review rate | 27.3% | **20.9%** |
| Auto-accept accuracy | 100% (77/77) | **100%** (85/85; CI 95.7–100) |
| Partner-name resolution (free-text legacy ledger) | 25/41 lines auto-resolved; the other 16 were held for a human, and every model suggestion was correct | 38/49 auto-resolved; 11 held, suggestions correct |
| Model cost per monthly run | $2.62 (110 cases) | $2.55 (115 cases) |
| Model latency per case | 8.0 s mean, 11.6 s p90 | 8.0 s mean, 10.8 s p90 |

**Value added by the model, like for like.** The same final code with no model calls at all, including no partner-name resolution:

| | Rules only | Rules + Claude |
|---|---|---|
| Auto-match recall (development / held-out) | 86.2% / 82.5% | 93.3% / 96.0% |
| Classification accuracy (development / held-out) | 92.2% / 96.1% | 100% / 99.0% |
| Explanation and proposed correcting entry | none | every case |
| Confidence you can route on | none | yes (§4) |

## 3. Model comparison

The same exception cases go to each model (partner-name resolution and matching held fixed). All models run at medium effort with adaptive thinking.

| Model | Accuracy, dev | Accuracy, held-out | Review rate, dev / held-out | Auto-accept accuracy | Cost per case | Latency (mean) |
|---|---|---|---|---|---|---|
| **Opus 5.5** | 100% | 99.0% | **27% / 21%** | 100% / 100% | $0.023 | 8.0 s |
| Sonnet 5.5 | 97.8% | 100% | 52% / 44% | 100% / 100% | $0.009 | 4.1 s |
| Haiku 5.5 | 98.9% | 100% | 48% / 32% | 100% / 100% | $0.0008 | 5.8 s |
| **Cascade: Haiku, then Opus if flagged** | 100% | 99.0% | **23% / 20%** | 100% / 100% | $0.008–0.013 | |

How to read this:
- **All three models classify about as accurately** as each other: the intervals overlap. They differ in **how often they are confident enough to skip review**. Opus sends half as many cases to people as Sonnet.
- **Reviewer time outweighs tokens.** Opus costs about $0.02 more per case than Haiku. One avoided review (about 10 minutes of an accountant's time) costs roughly $10. So the model that minimises the review rate wins on total cost, as long as auto-accept accuracy holds.
- **The cascade is the production recommendation.** Haiku handles the cases it's sure of, and Opus re-checks everything the policy flags. It matches Opus's accuracy, has the lowest review rate, and costs 47–64% less than Opus alone. Every configuration costs under $3 per monthly close at this volume, so this matters only at scale.

## 4. Confidence calibration and the review threshold

- **The development run before the fixes** (the stress test, with flawed case construction) showed calibrated confidence. Opus was 97–100% accurate on cases at confidence 0.80 or above and 50% accurate below 0.70. Raising the threshold from 0.75 to 0.85 would have prevented the 2 errors that were auto-accepted at the time.
- **On the final pipeline**, no model auto-accepted a wrong answer at any threshold from 0.50 to 0.95, on either dataset. Every error carried confidence of 0.55 or less and was routed to a human.
- **Decision:** keep 0.75 for now. The sweep shows it is safe here, but data this clean can't tell 0.75 and 0.85 apart. **Re-calibrate on the first pilot month** using reviewer overrides. The threshold is one setting (`REVIEW_CONFIDENCE_THRESHOLD`).

## 5. Failures observed, and what we changed

The evaluation's main value was finding defects in our own pipeline. Most model "errors" turned out to be upstream bugs that the model had correctly flagged as suspicious.

| # | Found by | Failure | Impact | Fix |
|---|---|---|---|---|
| 1 | Dev evaluation (M2) | An amount-only match paired two different invoices: a net-of-withholding-tax amount (16,800 × 0.95) equalled another invoice's gross amount of 15,960 | 1 silent false-positive match | Never amount-match a buyer line whose reference points to another invoice |
| 2 | Cost telemetry (M2) | Prompt caching wrote 283k tokens and read 0, because every case input is unique | ~20% overspend | Caching removed for per-case calls |
| 3 | UI build (M3) | pandas `NaN` (which counts as true) instead of `None` meant lines with an unresolved partner were never linked to their counterpart | 11 extra exception cases | Normalise missing values |
| 4 | M4 error analysis | Reference normalisation reduced `AE-ICINV-00599` to `599`, below the 4-digit minimum, so **reference matching never worked for 2 of 6 entities** | Those invoices fell back to amount-only matching and pairing | Also compare the full alphanumeric reference. Reference matches went from 125 to 189 |
| 5 | M4 error analysis | Case construction paired lines from different invoices (similar amount, same partner) | 4 of 5 Opus "errors". In every one, the model wrote that the lines "appear to be different invoices" and flagged it. The 2 auto-accepted errors were knock-on effects | Same reference guard applied to case linking |
| 6 | Held-out rules-only run | Two secondments with identical amounts (168h × 210 AUD). The guard missed it because the referenced invoice's partner was unresolved | 1 false positive (held-out precision 99.5% before the fix) | The guard now checks all of the seller's invoices |

**Remaining model errors on the final pipeline:**
- *Held-out, Opus:* a 90 SAR keying error (687,960 vs 688,050) that isn't a digit swap. Classified "other" at 0.50 confidence and **routed to review**. The rules baseline also missed it.
- *Development, Sonnet and Haiku:* a seller invoice paired with an unrelated accrual for a different person. Called an accrual rather than a missing booking, at 0.50–0.55 confidence; **routed to review**.
- *Development, Sonnet:* an FX difference whose ratio (0.951) happened to look like 5% Saudi withholding tax on a Saudi buyer. Genuinely ambiguous; 0.55 confidence; **routed to review**.

**The trade-off we chose:** fix 6 costs about 1 point of auto-match recall on the development set (3 correct amount-only matches now become cases). A missed match lands in front of a reviewer; a false match is never seen. In reconciliation, precision wins.

## 6. What the rules baseline tells us

On the final pipeline, the hand-written baseline classifier reaches 97.8% (development) and 99.0% (held-out) **when given the model-resolved partners**. That is a real finding and we report it as one:

- The break types in the mock data are the ones we designed, and the evidence features were built to expose them. On this data a rule tree can be written for every category, so the model's classification advantage is small.
- **The model's measured value is elsewhere:** resolving free-text partner names (+7 to +14 points of auto-match recall), explanations and correcting entries an accountant can act on, calibrated confidence for routing, and reading free text (multilingual descriptions, dispute and accrual notes).
- **Its value on breaks nobody anticipated is unproven by this evaluation.** That is the assumption the pilot must test first. It is the first of the three in the design document's kill test: if the model doesn't beat the rules on real exceptions, keep the rules and the review UI and drop the model from classification.

## 7. Limitations and threats to validity

- **Synthetic data, single author.** The same person designed the break taxonomy, the data and the pipeline. Real ledgers will have break types, formats and noise not represented here. The held-out set guards against overfitting to one sample, not to the generator.
- **Small samples.** About 90–100 scorable cases per set. A 99% point estimate has a lower 95% bound of about 95%.
- **One run per configuration.** Model outputs are not deterministic, and run-to-run variance was not measured; cached answers make the demo reproducible, not the model. A pilot should re-run a sample to measure agreement.
- **Single-label ground truth.** Real breaks can be compound, for example a split booking that is also miscoded.
- **Latency** was measured with 8 parallel workers. **Costs** use list prices at the time of writing.

## 8. Reproduce

```bash
python -m recon.run --no-llm                    # development set, replay cached answers
python -m recon.experiments --no-llm            # model comparison + cascade (dev)

python data_gen/generate_mock_data.py --seed 2027 --out data_holdout
RECON_DATA=data_holdout RECON_OUT=out_holdout python -m recon.run --no-llm
RECON_DATA=data_holdout RECON_OUT=out_holdout python -m recon.experiments --no-llm
```

All model answers used in this report are committed in `cache/llm/` (677 entries), so the commands above make no API calls. Total API spend for all development and evaluation runs: about $10.
