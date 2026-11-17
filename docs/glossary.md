# Glossary

Plain-language explanations of the terms and abbreviations used in this project, grouped by topic.

## 1. Finance and accounting

| Term | Meaning |
|---|---|
| **Legal entity** | A separately registered company within the group, for example NorthStar's UK company or its Saudi company. Each keeps its own books |
| **Group** | The parent company and all the legal entities it owns |
| **Intercompany (IC)** | Transactions *between* two entities of the same group, for example the UK entity charging the Saudi entity for engineering work. For the group as a whole these cancel out, so both sides must agree |
| **Receivable** | Money a company is owed. The *seller* records an IC receivable |
| **Payable** | Money a company owes. The *buyer* records an IC payable |
| **Ledger** | A company's accounting record: the list of all its bookings (date, amount, account, description). "General ledger" (GL) is the main ledger |
| **Booking / posting** | Recording a transaction in the ledger |
| **Journal entry** | A booking that moves an amount between accounts, typically used to correct something |
| **Debit / credit (Dr / Cr)** | The two sides of every booking in double-entry accounting. Each entry has an equal debit and credit |
| **Reclass (reclassification)** | Moving a booking to the correct account or partner without changing its amount |
| **Month-end close** | The monthly process of finalising the books so financial results can be reported |
| **Consolidation** | Combining all entities' books into one set of group accounts. IC balances must agree before this can happen |
| **Reconciliation** | Checking that two records which should agree actually do, and explaining any differences |
| **Matching** | Pairing the seller's line with the buyer's line for the same transaction |
| **Invoice reference** | The invoice number, used to find both sides of the same transaction |
| **Counterparty / partner entity** | The *other* entity in an IC transaction |
| **Trading partner** | SAP's term for the counterparty entity |
| **Break / exception / difference** | A transaction where the two sides don't agree, or one side is missing |
| **FX (foreign exchange)** | Currency conversion. An **FX difference** arises when the two sides convert at different exchange rates |
| **Exchange rate: daily / month-end / budget** | *Daily:* that day's market rate. *Month-end:* the rate on the last day of the month. *Budget rate:* a fixed rate set in advance for planning, which should not be used for actual bookings |
| **Pegged currency** | A currency fixed to another; for example, the UAE dirham and Saudi riyal are pegged to the US dollar |
| **WHT (withholding tax)** | Tax the *buyer* deducts from a payment and pays to its own tax authority on the seller's behalf. In Saudi Arabia, for example, 5% on services and 20% on management fees. The buyer books the net amount, which creates a difference |
| **VAT** | Value-added tax, a sales tax on goods and services |
| **Cut-off / timing difference** | One side booked the transaction in a different month from the other. It clears by itself next month |
| **Accrual** | Booking a cost that has been incurred but not yet invoiced |
| **Split booking** | One invoice recorded as several lines, for example by cost centre |
| **Duplicate** | The same invoice booked twice |
| **Miscoding** | A booking against the wrong account or wrong partner entity |
| **Keying error / typo** | A mistake typing an amount, for example swapping two digits |
| **Dispute** | The buyer deliberately books less because it disagrees with part of the charge |
| **Credit note** | A document from the seller reducing an invoice |
| **Write-off** | Removing an unexplained balance from the books, usually at a cost |
| **Materiality** | The size above which an amount matters enough for extra scrutiny (set at $100k in this prototype) |
| **Recharge** | Passing a cost on to another entity, for example a project recharge |
| **Secondment** | An employee working temporarily for another entity; their time is recharged |
| **Management fee** | A charge from head office to entities for central services |
| **Shared services** | Central functions (for example IT) provided to several entities and charged to them |
| **Cost centre** | A unit within a company that costs are assigned to |
| **Controller** | The finance manager responsible for an entity's books |
| **Internal Audit** | The team that independently checks a company's controls and processes |
| **SOX / financial controls** | Rules (from the US Sarbanes–Oxley Act) requiring documented, tested controls over financial reporting |
| **Segregation of duties** | The person proposing a change must not be the one approving it |
| **Audit trail** | A record of who did what, when and why |

## 2. Terms specific to this solution

| Term | Meaning |
|---|---|
| **Copilot** | A tool that assists a person and prepares the work, while the person stays in charge |
| **Triage** | Sorting items by type and urgency to decide what needs attention first, as in a hospital emergency room. Here: sorting reconciliation breaks by cause and routing them to automatic approval or human review |
| **Case** | One exception bundled with its ledger lines and evidence, ready for review |
| **Evidence** | Facts computed by code for each case (differences, exchange-rate comparisons, tax ratios), given to the AI to reason over |
| **Auto-match** | A match made by the rules, with no AI involved |
| **Auto-accept (eligible)** | A case the review policy allows to be approved without detailed review (confident, small, not a dispute) |
| **Review policy** | Fixed rules in the code that decide which cases a person must review |
| **Human-in-the-loop** | A person checks and approves the system's output before anything happens |
| **Override / escalate** | *Override:* the reviewer corrects the AI's category. *Escalate:* the case is passed to a controller |
| **Alias** | An alternative name for an entity, for example "London office" for NorthStar UK |
| **Master data / MDM** | The official reference list of entities, codes and names. MDM (master data management) is the system that maintains it |
| **Extract / export** | A data file produced from a finance system |
| **Canonical table** | One common format that all the different exports are converted into |
| **Rules baseline** | A simple rules-only classifier used as a comparison: the AI must beat it to justify its cost |
| **Pipeline** | The automated sequence of steps: read data, match, build cases, classify, write results |
| **Close window** | The few days each month when the close happens and support must be most responsive |

## 3. AI terms

| Term | Meaning |
|---|---|
| **AI / LLM** | Artificial intelligence / large language model: an AI system that reads and writes text |
| **Claude** | Anthropic's family of AI models, used in this prototype |
| **Opus / Sonnet / Haiku** | Claude models from most to least capable, and most to least expensive |
| **Model** | The AI system itself |
| **Prompt** | The instructions and input sent to the model |
| **Structured outputs** | Forcing the model to answer in a fixed format (fields such as category and confidence), so software can read it reliably |
| **Token** | The unit AI usage is measured and billed in, roughly ¾ of a word. **MTok** = a million tokens |
| **Effort / adaptive thinking** | Settings controlling how much the model "thinks" before answering |
| **Confidence** | The model's own estimate (0–1) of how likely its answer is correct |
| **Calibration** | Whether stated confidence matches reality (answers given at 90% confidence should be right about 90% of the time) |
| **Cache (of model answers)** | Saved answers that can be replayed without calling the model again, for free and identically |
| **Prompt caching** | A provider feature that discounts repeated prompt text. Tried and removed here, because it didn't save money |
| **Cascade** | Using a cheap model first and sending only uncertain cases to an expensive model |
| **Fallback** | What happens when the primary path fails, for example rules-only mode when the AI service is unavailable |
| **Refusal** | The model declining to answer for safety reasons; handled by a fallback model |
| **Latency** | Response time. **p90** = 90% of responses are faster than this |
| **API / API key** | *API:* the interface programs use to call a service such as Claude. *API key:* the secret password for it, which must never be shared or published |
| **MLOps** | The engineering work of running your own AI models in production |

## 4. Evaluation and statistics

| Term | Meaning |
|---|---|
| **Mock / synthetic / simulated data** | Realistic but invented data, generated by a program |
| **Ground truth / answer key** | The known correct answers, used to score the system |
| **Seed** | A number that makes generated "random" data reproducible: the same seed gives the same data |
| **Development set** | The data used while building and debugging |
| **Held-out set** | Separate data never used during building, which gives an honest test |
| **Precision** | Of the matches made, the share that were correct |
| **Recall** | Of the matches that should have been made, the share that were found |
| **Accuracy** | The share of cases classified correctly |
| **Coverage** | The share of real breaks that appeared as a case |
| **Review rate** | The share of cases sent to a person |
| **Threshold** | A cut-off value; for example, cases below 0.75 confidence go to review |
| **CI (confidence interval)** | The range the true value probably lies in (95% CI). Wider with less data. **Wilson interval** is the method used |
| **False positive** | Something flagged as true that isn't, for example two lines matched that don't belong together |
| **Kill test / kill metric** | Pre-agreed measurements and limits that decide when to stop or redesign the project |

## 5. Technology and security

| Term | Meaning |
|---|---|
| **ERP** | Enterprise resource planning: the core business and finance system |
| **SAP S/4HANA, SAP ECC** | SAP's current and previous-generation ERP |
| **Oracle EBS** | Oracle E-Business Suite, an ERP |
| **Dynamics 365 (F&O)** | Microsoft's ERP (Finance & Operations) |
| **BW** | SAP Business Warehouse, SAP's reporting database |
| **Legacy system** | An older system still in use, here the ledger of an acquired company |
| **CSV** | A simple spreadsheet-like text file format |
| **Prototype** | A working early version built to test an idea |
| **UI** | User interface: the screens people use |
| **Streamlit** | A Python framework for quickly building simple web apps |
| **Python, pandas, Pydantic** | The programming language; a data-table library; a library for validating data formats |
| **Repo (repository), Git, GitHub** | The project's code store; the version-control tool; the website hosting it |
| **Branch / commit / merge** | A separate line of work / a saved change / combining a branch back into the main line |
| **venv** | An isolated Python environment for one project |
| **SSO** | Single sign-on: one company login for all tools |
| **Entra ID** | Microsoft's identity and login service (formerly Azure AD) |
| **Row-level access** | Users see only the data they're allowed to, for example only their own entities |
| **Vault (secrets)** | A secure store for passwords and keys |
| **Data residency** | Rules requiring data to be stored or processed in a specific country or region |
| **GDPR** | The EU's data protection regulation |
| **PDPL** | Saudi Arabia's Personal Data Protection Law |
| **Personal data / masking** | Information identifying people (for example employee names); masking hides it before processing |
| **Write-once (WORM) storage** | Storage where records can't be changed or deleted afterwards. Used for audit logs |
| **On-call** | Engineers available to fix problems outside normal hours |
| **Idempotent** | Safe to run twice; re-running doesn't create duplicates |
| **Hosting / Streamlit Community Cloud** | Running the app on a server so others can open it by link; Streamlit's free hosting service |

## 6. Business case

| Term | Meaning |
|---|---|
| **ROI** | Return on investment: what is gained relative to what is spent |
| **Payback period** | How long until savings cover the initial cost |
| **FTE** | Full-time equivalent: one person's full working time (two half-time people = 1 FTE) |
| **Loaded cost** | The full cost of an employee: salary plus benefits, office and overhead |
| **Capacity freed** | Working time released for other tasks, not necessarily a headcount reduction |
| **Pilot / shadow pilot** | A limited trial. In a *shadow* pilot, the new tool runs alongside the existing process without replacing it |
| **Go / no-go** | A formal decision point on whether to continue |
| **Rollout** | Extending the solution to all teams and countries |
| **SME** | Subject-matter expert |
| **PM** | Project manager |
| **Build vs buy** | Whether to develop something in-house or purchase it from a vendor |
| **Vendor dependency** | Reliance on an external supplier, a risk if their prices or terms change |

## 7. Codes in the mock data

| Code | Meaning |
|---|---|
| **NIG** | NorthStar Infrastructure Group, the fictional company in the case |
| **NIG-US, -UK, -DE, -AE, -SA, -AU** | The six entities: United States, United Kingdom, Germany, United Arab Emirates, Saudi Arabia (KSA, Kingdom of Saudi Arabia), Australia |
| **USD, GBP, EUR, AED, SAR, AUD** | US dollar, British pound, euro, UAE dirham, Saudi riyal, Australian dollar |
| **FIN, HR, LEG, IT, PRO, COM, KNW, ANL, GWT** | The case's operational clusters: Finance, People & HR, Legal, IT & Security, Procurement, Comms & Growth, Knowledge & Ops, Analytics & Strategy, Sales & Marketing |
| **P-24017 etc.** | Fictional project codes, for example P-24017 "Riyadh Metro Line 7" |
| **C0048 etc.** | Case numbers in the review app |
| **M0–M7** | The project's milestones |
| **"Teilfreigabe, Stunden strittig"** | German: "partial approval, hours disputed" |
