# Executive summary: IC Reconciliation Copilot

**Decision requested:** fund a one-quarter data-discovery and shadow pilot (≈ $175k), with a go/no-go based on three pre-agreed kill metrics.

## The problem

Every month, before NIG can close its books, each entity's intercompany balances must agree with its partners'. Because NIG grew through acquisitions, its entities run different finance systems, in different formats and languages. Group accountants match thousands of lines in spreadsheets and chase the differences by email across time zones. This takes days of skilled time each close, delays the group close, and leaves some differences written off unexplained.

## What we built

A working prototype that does the routine matching automatically and prepares the difficult cases for an accountant to decide:

1. **Reads the exports** from six entities on five different finance systems, as they are.
2. **Automatically matches about 95%** of transactions using transparent rules. No AI is involved, so this part is free and fully auditable.
3. **Explains each remaining difference** using AI (Claude). For each case it says what happened (exchange-rate difference, withholding tax, wrong partner code, duplicate, dispute, and so on), why it thinks so, and the correcting entry it proposes.
4. **Keeps a person in charge.** Nothing is posted automatically. Uncertain, disputed or large items (over $100k) always go to an accountant, and every decision is logged for audit.

## Evidence it works

Tested on realistic simulated data, including a separate test set the system was never tuned on:

| Result | Held-out test |
|---|---|
| Automatic matches that were correct | **100%** |
| Real differences surfaced for review | **100%** |
| Differences correctly explained | **99%** |
| Cases accepted without review that were correct | **100%** |
| Share of cases still needing a person | **21%** |
| AI cost per monthly run | **≈ $2.50** |

The testing also found and fixed six defects in our own logic before they could reach users. The result to treat with caution: the data is simulated, and the AI's advantage over simple rules on real-world surprises is not yet proven. That is the first thing the pilot tests.

## Value and ROI

All figures are **placeholder assumptions** to be replaced with NIG data in the pilot.

| Assumption | Value |
|---|---|
| Effort spent on IC reconciliation today | 10 full-time-equivalents across regional finance teams |
| Loaded cost per FTE | $85k per year, so **$850k per year** in total |
| Build (2 quarters, small team) | **$350k** one-off |
| Run (hosting, support, AI usage) | **$120k per year**. AI usage is under $1k of this |

| Scenario | Time saved | Annual capacity freed | Net per year | Payback after go-live |
|---|---|---|---|---|
| Low (the kill threshold) | 25% | $212k | $92k | about 3.8 years: **we would stop** |
| **Base** | **40%** | **$340k** | **$220k** | **about 19 months** |
| High | 55% | $468k | $348k | about 12 months |

The freed capacity goes into analysis and a faster close, not headcount cuts. That matters for adoption. Not quantified in these figures:
- a faster intercompany sign-off (for example, from about 5 to about 3 days)
- fewer unexplained write-offs
- a complete audit trail

## Costs and risks

| Risk | How it is handled |
|---|---|
| The AI is wrong and nobody notices | Cases accepted without review are sampled every close. If more than 3% are wrong, or any wrongly cleared item exceeds $100k, automatic acceptance stops |
| Data is hard to obtain from older systems | A 6-week data check is the first gate. If it fails, we narrow the scope to the entities with clean data |
| Accountants don't trust or use it | It's designed with them: evidence first, every proposal shows its source lines, and their corrections improve it. Adoption is tracked from the first close |
| Dependency on an AI vendor | One small integration point, and the model can be swapped and re-tested in minutes. Available through NIG's cloud providers for data residency |
| The AI adds little over simple rules | Measured directly. If so, we keep the rules and review tool (most of the value) and drop the AI from classification |

## Rollout

1. **Weeks 1–6, data check:** collect one month's exports from every system and agree controls with Internal Audit.
2. **Rest of quarter 1, shadow pilot:** two entity pairs run in parallel with today's process on 3 past closes and 1 live close. **Go/no-go here.**
3. **Quarter 2, live pilot:** the same teams work in the tool, with single sign-on, access by entity and the audit trail in place.
4. **Quarter 3, group-wide rollout:** the remaining entities, including the acquired legacy systems.

**Kill test.** We stop or redesign if any of these happens:
- more than 3% of accepted cases are wrong in any close
- accountant time saved stays below 25% after 3 closes
- the AI is less than 5 points better than simple rules and accountants rate its explanations below 3 out of 5

---

*Prototype, source code and full technical documentation: see the project repository. A 7-minute demo video accompanies this summary.*
