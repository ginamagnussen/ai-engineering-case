# Technical design: IC Reconciliation Copilot

**For:** NIG engineering leadership design review · **Cluster:** FIN · Finance · **Status:** prototype complete, requesting pilot funding

This document covers the design choices behind the prototype (Task 2), what it takes to reach production (Task 3), and how the solution should be judged and if necessary ended (Task 5). The evaluation evidence is in [evaluation.md](evaluation.md) and is summarised here, not repeated.

---

## 0. Problem and context assumptions

**Workflow.** At every month-end, each legal entity's intercompany (IC) receivables must agree with its partners' IC payables before the group can consolidate. NIG grew by acquisition, so entities run different ERPs, export in different formats and languages, and identify partner entities differently. Group accountants match lines in spreadsheets and chase the breaks by email: FX, cut-off timing, wrong partner codes, keying errors, duplicates, withholding tax, disputes.

**User.** A group IC accountant, who works the exceptions, and entity controllers, who approve corrections and resolve disputes.

The brief is intentionally incomplete. These are the assumptions we made and how they shaped the design:

| # | Assumption | How it shaped the design |
|---|---|---|
| C1 | Entities can export IC receivable and payable lines monthly (CSV or report) with an amount, currency, date, partner identifier and invoice reference, but **formats and partner identifiers differ** | A schema-driven ingestion layer. Adding an ERP is a config entry, not code |
| C2 | Most IC volume is routine; the hard part is the **residual exceptions** | Rules first, LLM only for the residue (about 15% of lines) |
| C3 | Finance will not accept unreviewed AI postings (SOX-style controls, audit) | The model **proposes**, a person **disposes**. Nothing is posted. The review policy lives in code, and every decision is logged |
| C4 | Volumes are modest (thousands of lines per month, not millions) | A batch design. Token cost is negligible; **reviewer time is the cost that matters** |
| C5 | Some descriptions are not in English (German, Arabic) and some partner names are free text (the acquired legacy ledger) | The LLM handles free text, and confirmed names are fed back into master data |
| C6 | No historical labelled reconciliations were available | A generator with an answer key and deliberate hard cases, plus a held-out test set |

---

## 1. Architecture (Task 2)

```
6 ERP extracts ─► ingest (schema config) ─► canonical line table
                                              │
                    ┌─────────────────────────┤
                    ▼                         │
     counterparty resolution                  │
     master data ► confirmed aliases ► Claude (batched, ≥0.8 confidence, else human)
                    │                         │
                    ▼                         ▼
     deterministic matching: exact reference ▸ amount+date (with reference guard) ▸ split ▸ cross-period
                    │ ~95% matched, each with rule + confidence
                    ▼
     exception cases + computed evidence (differences, FX rate bases, WHT ratios, digit patterns,
                    │                       duplicate twins, search results)
                    ▼
     Claude structured output: category · confidence · explanation · action · correcting entry
     rules baseline (same evidence)        review policy in code (confidence, category, materiality)
                    ▼
     review UI: queue · case detail · approve / override / escalate · alias confirmation · audit log
```

**Design principles:**
1. **Rules first, the model for the residue.** Deterministic matching is free, instant and auditable. The model sees only what rules can't settle.
2. **Code computes, the model reasons.** Every number the model needs (differences, implied FX rates against four rate bases, WHT ratios, digit-transposition checks) is computed in Python and passed as evidence. The model is never asked to do arithmetic.
3. **Oversight is enforced in code, not in the prompt.** A case goes to a person if confidence is below 0.75, the category is dispute or "other", the amount exceeds $100k, or the model asks. The model can't talk its way past the policy.
4. **There is always a fallback.** A rules baseline classifies every case with the same evidence. If the API is unavailable, the pipeline completes and routes everything to review. This was observed in practice when credits ran out mid-run.
5. **Reproducible by construction.** Model answers are cached under a key of (model, prompt version, system prompt, input). They are committed, so the demo and every evaluation figure replay with no API key and no cost. Changing a prompt invalidates exactly the affected answers.

### Components

| Component | Responsibility | Notes |
|---|---|---|
| `ingest.py` | 6 export formats into a canonical table | Handles separate DR/CR columns vs signed amounts, 4 date formats, decimal comma, semicolon delimiters, German headers |
| `entities.py` | Partner resolution | Oracle segments, SAP trading partners, legal names, aliases; then human-confirmed aliases; then one batched Claude call with sample lines as evidence |
| `match.py` | Matching rules | 4 passes. The reference guard blocks amount-only matches when the buyer's reference points to another invoice |
| `cases.py` | Exception grouping and evidence | Links seller and buyer orphans into pair cases; the evidence is the model's entire input |
| `classify.py` | Claude, rules baseline, review policy | Pydantic schema with structured outputs; 11-category taxonomy |
| `llm.py` | API wrapper | Structured outputs, disk cache, cost and latency telemetry, server-side refusal fallback |
| `review.py`, `app.py` | Human-in-the-loop | Append-only audit log keyed by the case's line set, so decisions survive re-runs |
| `evaluate.py`, `experiments.py` | Measurement | Scoring against the answer key, model comparison, calibration, threshold sweep, cascade |

### Models

- **Claude Opus 5.5** at medium effort with adaptive thinking, via the Anthropic API with structured outputs (a Pydantic schema, validated on return).
- **Why Opus:** in a like-for-like comparison, Opus, Sonnet and Haiku 5.5 classified at 98–100%. Opus sent half as many cases to review as Sonnet (21–27% vs 44–52%). One avoided review is worth about 500× the token difference.
- **Production recommendation: a cascade.** Haiku classifies first and Opus re-checks every case the policy flags. In simulation from cached answers it matched Opus's accuracy, had the lowest review rate (20–23%) and cost 47–64% less ([evaluation §3](evaluation.md)).
- **Prompt caching was tried and removed.** Telemetry showed 283k cache-write tokens and zero reads, because every case input is unique.

### Bought vs built

| Capability | Decision | Why | The dependency we accept |
|---|---|---|---|
| LLM | **Buy** (Claude API) | Frontier quality on multilingual, messy text with no MLOps team; structured outputs remove parsing glue | Vendor and pricing risk. Mitigated by a thin wrapper (one function), an evaluation harness that can score any model in minutes, and availability through AWS Bedrock, Google Vertex AI and Azure for procurement and data residency |
| Matching engine | **Build** (about 150 lines) | Domain-specific, must be explainable, and trivial to run | We own the rules. Covered by the evaluation suite |
| UI | **Buy** (Streamlit) for the prototype | Hours, not weeks | Not a production UI (see §2) |
| Evaluation harness | **Build** | No off-the-shelf tool knows our break taxonomy; this is what lets us change models or prompts safely | Ongoing maintenance |
| **Whole-process alternative** | **Evaluate before scaling** | Established financial-close vendors sell intercompany matching modules, and several now add AI features. **We have not evaluated them.** | If NIG already licenses such a platform, the better path is probably to use its matching and add our exception copilot (evidence, explanation, routing) on top of its exports. The copilot is the differentiating part; the matcher is not |

### What I would change with more time
1. **Real labelled data:** 3–6 historical closes with the accountants' actual resolutions, as the evaluation set (§3, A1).
2. **Measure run-to-run variance:** re-run a sample 5 times to quantify model agreement.
3. **Implement the cascade in code** (currently only simulated).
4. **Unit tests** for parsers and matching rules. Today the guard is the evaluation suite, which caught 6 defects but is coarse.
5. **Case linking as a global assignment** (optimal matching) instead of greedy pairing, the source of the residual mis-pairs.
6. **Proposed correcting entries in each ERP's journal format**, for one-click hand-off to the ERP's own approval workflow.
7. **Multi-label breaks** (for example, split and miscoded at once).

### How we know it works (summary of [evaluation.md](evaluation.md))
- **Ground truth:** synthetic data with an answer key: 12 break types, 6 ERPs, multilingual text, recurring fixed fees, free-text partners. Plus a **held-out set** (a different seed) scored after all fixes.
- **Held-out results:** 100% match precision; 96% auto-match recall; 100% of real breaks surfaced; **99.0%** classification accuracy (95% CI 94.7–99.8); **100%** accuracy on the 85 cases auto-accepted without review; 21% of cases routed to a person; $2.55 per monthly run.
- **Failures observed:** 6 pipeline defects found and fixed, including reference matching that silently never worked for 2 of 6 entities. Most model "errors" were upstream mis-pairings that **the model itself flagged** as "different invoices". The remaining model errors all carried confidence of 0.55 or less and were routed to review.
- **Honest limitation:** on synthetic data, a hand-written rules classifier is close behind the model, because the break types are known in advance. The model's value on unanticipated real-world breaks is unproven. That is assumption A1.

---

## 2. Production readiness (Task 3)

### The honest estimate

**Two quarters to a production pilot on two entity clusters, then a third quarter to roll out group-wide.** It depends on three things:
1. **Data access** to IC extracts from every ERP, including the acquired legacy ledgers, within the first 6 weeks.
2. **A named process owner in Group Finance** who owns the review policy, taxonomy and thresholds, and signs off the controls with Internal Audit.
3. **Identity and permissions integration** (SSO plus entity-level access) before any real data enters the tool.

Without (1), this becomes the "six months of data engineering" project the brief warns about. That is why it's the first gate in the plan.

### Workstreams

| Area | Prototype today | Production requirement | Effort |
|---|---|---|---|
| **Data** | CSV files on disk | Scheduled read-only extracts per ERP: SAP (standard report or BW extract), Oracle, Dynamics, plus legacy file drops. A canonical store (Postgres or the data platform). FX rates from Treasury's rate source. Partner aliases written back to master data (MDM). Data-quality checks: reference fill rate, partner codes, sign conventions | **Largest item.** About 1 data engineer for 2 quarters; legacy ledgers carry the most risk |
| **Security and compliance** | Local, single user | SSO (Entra ID), **row-level access by entity** (controllers see only their entities), secrets in a vault, read-only ERP credentials. LLM traffic through an enterprise agreement with no training on customer data. **Data residency:** route EU and KSA data through regional endpoints where required (GDPR, KSA PDPL); minimise personal data, since employee names appear in secondment descriptions and can be masked before the model call. Audit log in write-once storage, with retention aligned to the statutory audit | About 0.5 security engineer plus Internal Audit review |
| **Controls** | Policy in code | Policy and thresholds under change control. Segregation of duties (the proposer can't approve). **No automatic postings** in phase 1; approved corrections flow into each ERP's existing journal approval. Model or prompt changes gated by the regression evaluation | Process owner and Internal Audit sign-off |
| **Reliability** | Batch on demand | Daily runs during the close window, idempotent and retryable. On API failure, fall back to rules plus full review (already built). Alerts on failure, cost spikes and drift (override-rate spikes) | About 0.5 engineer |
| **Languages and jurisdictions** | EN and DE text, KSA WHT | Arabic descriptions; jurisdiction rules (WHT and VAT rates, cut-off calendars) as **config tables, not prompt text**, maintained by Group Tax | Tax SME time |
| **UX** | Streamlit | An internal web app, or embedding in NIG's existing close or workflow tool, so accountants don't need another tool | About 1 engineer for 1 quarter |
| **Cost at scale** | $2.60 per run (110 cases) | Assume 20 entity clusters × about 150 exception cases × 12 months ≈ 36k cases per year. **≈ $850/year on Opus, ≈ $400/year with the cascade.** Tokens are not the cost driver; hosting, about $20–30k/year, and people are | Negligible |
| **Ownership and support** | One developer | **Product owner:** Group Finance IC process lead. **Run:** platform team (hosting, on-call), with heightened support during the close window. **Connectors:** finance systems team. **Model and prompts:** the AI engineering team, with an evaluation gate. Business-hours support, plus a close-window escalation path | About 0.5 FTE ongoing |

### Phased plan

| Phase | Duration | Scope | Exit criteria |
|---|---|---|---|
| **0 · Data discovery** | Weeks 1–6 | One month of IC extracts from every ERP; measure reference fill rate and partner mapping coverage; agree the controls with Internal Audit | Extracts available for at least 80% of IC volume. **Otherwise, narrow the scope to the SAP and Oracle entities** |
| **1 · Shadow pilot** | Rest of Q1 | Two clusters (for example UK↔DE and AE↔SA). Run on 3 historical closes plus 1 live close **in parallel** with the current process. Measure against the accountants' actual resolutions | Kill metrics (§3) within thresholds |
| **2 · Live pilot** | Q2 | The same clusters work in the tool; SSO, entity permissions and the write-once audit log are live; cascade implemented | Time saved ≥ 25%, auto-accept error ≤ 3% |
| **3 · Rollout** | Q3 | Remaining clusters, including legacy ledgers; Arabic; ERP journal hand-off | Group-wide adoption ≥ 70% of cases actioned in the tool |

**Team:** a tech lead, 2 engineers, 1 data engineer, plus 0.5 product owner and 0.5 finance SME, for about 2 quarters. That's roughly $350k fully loaded (placeholder rates). Ongoing run cost is about $120k/year (hosting, support, connector maintenance).

---

## 3. Assumptions and kill test (Task 5)

### The three assumptions the solution depends on most

**A1 · The model adds value beyond rules on real exceptions.**
- *Why it matters:* on synthetic data, a rules classifier is close behind the model. If real breaks are equally predictable, the model's cost, dependency and governance aren't justified for classification.
- *How to validate:* the shadow pilot on 3 historical closes. Compare model vs rules vs what the accountant actually did, on the long tail of breaks the taxonomy doesn't anticipate. Also have accountants rate explanation usefulness (1–5).
- *If false:* keep ingestion, matching, the evidence layer and the review UI, which deliver most of the time saving without a model. Use the model only for partner-name resolution and drafting explanations. **Redesign, don't kill.**

**A2 · IC data can be obtained from every ERP in weeks, with usable references.**
- *Why it matters:* reference matching does most of the work; when references are missing, the system falls back to weaker amount-and-date matching and more human review. The brief warns that six months of data engineering means a failed prototype.
- *How to validate:* Phase 0. One month's extract per ERP within 6 weeks; measure reference fill rate (target ≥ 70% of lines) and partner mapping coverage.
- *If false:* scope down to the entities with clean extracts (likely SAP and Oracle) and treat legacy ledgers as a migration dependency, or buy connectors through a reconciliation vendor. The benefit shrinks with the coverage.

**A3 · Accountants trust the proposals enough to stop re-doing the work.**
- *Why it matters:* a perfectly accurate tool saves nothing if every proposal is re-checked by hand. Adoption, not accuracy, delivers the ROI. The users didn't ask for the tool.
- *How to validate:* a time study before and after for two pilot teams (hours per close on IC); approve and override rates; share of cases actioned in the tool vs in spreadsheets; a short survey. Design for trust: the evidence comes first, every proposal cites its lines, and the accountants' overrides visibly improve the system.
- *If false:* change the UX and positioning, for example evidence-only mode with no AI category, before spending more. If time saved stays below the threshold, stop.

### Kill metrics

Measured from the first live close, after one calibration close.

| # | Metric | Measured how | Window | Kill or redesign if |
|---|---|---|---|---|
| **K1 · Safety** | Error rate of auto-accepted cases | Controller re-checks a random sample of 50 auto-accepted cases per close | Every close during the pilot | **> 3%** in any close, or **any wrongly cleared item > $100k** → stop auto-accept immediately (everything goes to review); fix or redesign before resuming |
| **K2 · Value** | Reduction in accountant hours on IC reconciliation per close vs the pre-pilot baseline | Time study and timesheets for the two pilot teams | 3 consecutive live closes | **< 25%** → stop; the business case needs about 35% to pay back the build within 2 years |
| **K3 · Model value-add** | Model accuracy minus rules-baseline accuracy on reviewer-confirmed cases, together with explanation usefulness | Reviewer decisions; 1–5 rating in the UI | 3 consecutive closes | **< 5 percentage points better and usefulness < 3/5** → remove the model from classification (keep rules plus UI, per A1) |

**Leading indicator, not a kill metric:** under 50% of pilot cases actioned in the tool by the second live close means an adoption problem. Investigate before K2 fires.
