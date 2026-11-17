"""
IC Reconciliation Copilot - reviewer UI.

    streamlit run app.py

Reads the pipeline outputs in out/ (run `python -m recon.run` first, or use the sidebar button).
"""
from __future__ import annotations

import ast
import json

import pandas as pd
import streamlit as st

from recon import config, llm
from recon.classify import Category
from recon.entities import OVERRIDES_FILE, confirm_alias
from recon.review import LOG, case_key, latest_decisions, load_log, record
from recon.run import run_pipeline

st.set_page_config(page_title="IC Reconciliation Copilot", page_icon="⚖️", layout="wide")
CATEGORIES = list(Category.__args__)
FILES = ["cases.json", "lines.csv", "matches.csv", "run_summary.json"]


# ------------------------------------------------------------------------------- data
def _mtime() -> float:
    return max((config.OUT / f).stat().st_mtime for f in FILES)


@st.cache_data
def load(_mtime: float):
    cases = json.loads((config.OUT / "cases.json").read_text(encoding="utf-8"))
    lines = pd.read_csv(config.OUT / "lines.csv", keep_default_na=False)
    matches = pd.read_csv(config.OUT / "matches.csv", keep_default_na=False)
    summary = json.loads((config.OUT / "run_summary.json").read_text(encoding="utf-8"))
    return cases, lines, matches, summary


def case_usd(c: dict) -> float:
    return max(abs(l["amount_usd"]) for l in c["lines"])


def status_of(c: dict, decisions: dict) -> str:
    d = decisions.get(case_key(c))
    return d["status"] if d else "open"


# ------------------------------------------------------------------------------- sidebar
st.sidebar.title("⚖️ IC Recon Copilot")
st.sidebar.caption("NorthStar Infrastructure Group · Oct-2026 close (mock data)")
reviewer = st.sidebar.text_input("Reviewer name", key="reviewer", placeholder="required to record decisions")
page = st.sidebar.radio("Page", ["Start here", "Overview", "Review queue", "Counterparty mapping",
                                 "Auto-matches", "Audit log", "Evaluation"])

with st.sidebar.expander("Run pipeline"):
    has_key = llm.credentials_available()
    allow_api = st.checkbox("Allow new API calls", value=False, disabled=not has_key,
                            help="Off = replay cached model answers only (free, deterministic).")
    if st.button("Run now", width="stretch"):
        with st.status("Running pipeline...", expanded=True) as s:
            summ = run_pipeline(use_llm=allow_api and has_key, log=st.write)
            s.update(label=f"Done in {summ['runtime_s']}s · LLM cost ${summ['llm']['cost_usd']}", state="complete")
        st.cache_data.clear()
        st.rerun()
    if st.button("Reset demo", width="stretch",
                 help="Clear recorded decisions and confirmed aliases, then re-run from cache (no API calls)."):
        for p in (LOG, OVERRIDES_FILE):
            p.unlink(missing_ok=True)
        run_pipeline(use_llm=False, log=lambda m: None)
        st.cache_data.clear()
        st.rerun()

if not all((config.OUT / f).exists() for f in FILES):
    # first start (e.g. a fresh hosted deployment): build outputs from cached answers, no API calls
    with st.spinner("Preparing the October close (first start)..."):
        run_pipeline(use_llm=False, log=lambda m: None)
    st.cache_data.clear()

cases, lines, matches, summary = load(_mtime())
ctx_entities = pd.read_csv(config.REFERENCE / "entities.csv", dtype=str, keep_default_na=False)
ENTITY_CODES = list(ctx_entities.entity_code)
decisions = latest_decisions()


# ------------------------------------------------------------------------------- pages
PLAIN_ENTITY = {"NIG-US": "US (head office)", "NIG-UK": "UK", "NIG-DE": "Germany", "NIG-AE": "UAE",
                "NIG-SA": "Saudi Arabia", "NIG-AU": "Australia (acquired)"}
TRICKY = {
    "NIG-US": "Separate debit and credit columns; US date order",
    "NIG-UK": "Signed amounts; partner as an SAP code",
    "NIG-DE": "German headers and text; semicolons; decimal comma",
    "NIG-AE": "Partner written as its full legal name",
    "NIG-SA": "Partner written as its full legal name",
    "NIG-AU": "Old system: partner names typed by hand",
}


def _raw_lines(entity: str, n: int) -> list[str]:
    with (config.RAW / f"ledger_{entity}.csv").open(encoding="utf-8") as f:
        return [next(f).rstrip("\n") for _ in range(n)]


def _sample_row(entity: str) -> dict:
    import csv as _csv
    from recon.ingest import SCHEMAS

    sc = SCHEMAS[entity]
    header, first = _raw_lines(entity, 2)
    row = dict(zip(next(_csv.reader([header], delimiter=sc["sep"])), next(_csv.reader([first], delimiter=sc["sep"]))))
    if "dr" in sc:
        amount = f"debit {row[sc['dr']]}" if row[sc["dr"]] else f"credit {row[sc['cr']]}"
    else:
        amount = row[sc["amount"]]
    return {"date": row[sc["date"]], "amount": f"{amount} {row[sc['ccy']]}", "partner": row[sc["cp"]]}


def page_start():
    st.header("Month-end intercompany check")
    st.markdown("##### Six group companies must agree on what they owe each other before the books can close. "
                "Today that means spreadsheets and email. Here, **rules match, AI explains, people decide.**")

    cols = st.columns([4, 1, 4, 1, 4, 1, 4])
    steps = [(f"{len(ENTITY_CODES)} exports", "arrive in different formats, from 5 finance systems"),
             (f"{summary['matches']} matched", "automatically by rules. No AI, no cost, fully traceable"),
             (f"{summary['cases']} explained", "differences, each with a cause and suggested fix from AI"),
             (f"{summary['needs_review']} decided", "by a person. Nothing is booked automatically")]
    for i, (big, small) in enumerate(steps):
        with cols[2 * i], st.container(border=True):
            st.markdown(f"### {big}")
            st.caption(small)
        if i < 3:
            cols[2 * i + 1].markdown("<h2 style='text-align:center'>→</h2>", unsafe_allow_html=True)

    st.subheader("What comes in")
    st.caption("One example entry from each company's export, exactly as written. Same kind of data, six different formats.")
    erp = ctx_entities.set_index("entity_code").erp
    rows = []
    for e in ENTITY_CODES:
        s = _sample_row(e)
        rows.append({"Company": PLAIN_ENTITY[e], "Finance system": erp[e].split(" (")[0],
                     "Date as written": s["date"], "Amount as written": s["amount"],
                     "Partner company written as": s["partner"], "What makes it tricky": TRICKY[e]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    pick = st.selectbox("Show a raw export file", ENTITY_CODES, index=ENTITY_CODES.index("NIG-DE"),
                        format_func=lambda e: f"{PLAIN_ENTITY[e]} · ledger_{e}.csv")
    st.code("\n".join(_raw_lines(pick, 6)), language=None)

    st.subheader("Run the October close")
    if st.button("▶ Run now", type="primary"):
        with st.spinner("Reading 6 exports, matching, explaining exceptions..."):
            s = run_pipeline(use_llm=False, log=lambda m: None)
        st.cache_data.clear()
        st.session_state["start_ran"] = s["runtime_s"]
        st.rerun()
    if "start_ran" in st.session_state:
        st.success(f"Done in {st.session_state['start_ran']} seconds. Open **Overview** in the sidebar.")


def page_overview():
    st.header("October 2026 intercompany close")
    n_matched_lines = sum(len(ast.literal_eval(x)) for col in ("seller_lines", "buyer_lines")
                          for x in matches[col])
    reviewed = sum(status_of(c, decisions) != "open" for c in cases)
    c = st.columns(6)
    c[0].metric("Ledger lines", summary["lines"], help="IC receivable/payable lines across 6 ERP extracts")
    c[1].metric("Auto-matched", summary["matches"], f"{n_matched_lines} lines", delta_color="off")
    c[2].metric("Exception cases", summary["cases"])
    c[3].metric("Need human review", summary["needs_review"])
    c[4].metric("Decisions recorded", f"{reviewed}/{len(cases)}")
    full = summary["llm"].get("full_run_cost_usd", summary["llm"]["cost_usd"])
    c[5].metric("Model cost per run", f"${full:.2f}",
                f"this replay: ${summary['llm']['cost_usd']:.2f} ({summary['llm']['cache_hits']} cached)",
                delta_color="off", help="What the model calls cost when generated live; cached replays are free.")

    rows = []
    for k in cases:
        a = k["assessment"] or {}
        rows.append({"category": a.get("category", "no assessment"), "needs_review": k["needs_review"],
                     "usd": case_usd(k), "responsible": a.get("responsible_entity", "?"),
                     "status": status_of(k, decisions)})
    df = pd.DataFrame(rows)
    left, right = st.columns(2)
    with left:
        st.subheader("Exceptions by type")
        g = df.groupby("category").agg(cases=("usd", "size"), need_review=("needs_review", "sum"),
                                       gross_usd=("usd", "sum")).sort_values("gross_usd", ascending=False)
        st.dataframe(g.style.format({"gross_usd": "${:,.0f}"}), width="stretch")
    with right:
        st.subheader("Open work by responsible entity")
        o = df[df.status == "open"].groupby("responsible").agg(open_cases=("usd", "size"),
                                                                gross_usd=("usd", "sum"))
        st.dataframe(o.sort_values("gross_usd", ascending=False).style.format({"gross_usd": "${:,.0f}"}),
                     width="stretch")
    st.caption(f"Model: {summary['model']} · run took {summary['runtime_s']}s · "
               f"gross exception value ${summary.get('unexplained_usd', 0):,.0f} (largest line per case)")


def render_case(k: dict):
    a = k["assessment"]
    st.subheader(f"{k['case_id']} · {k['kind'].replace('_', ' ')} · {k['seller'] or '?'} → {k['buyer'] or '?'}")
    left, right = st.columns([3, 2])
    with left:
        if a:
            st.markdown(f"**AI assessment:** `{a['category']}` · confidence **{a['confidence']:.0%}** · "
                        f"responsible: **{a['responsible_entity']}**")
            st.write(a["explanation"])
            st.markdown(f"**Recommended action:** {a['recommended_action']}")
            if a.get("adjustment_description"):
                amt = f"{a['adjustment_amount']:,.2f} {a['adjustment_currency'] or ''}" \
                    if a.get("adjustment_amount") is not None else ""
                st.markdown(f"**Proposed adjustment:** {a['adjustment_description']} {amt}")
        else:
            st.warning("No model assessment available for this case.")
    with right:
        if k["needs_review"]:
            st.warning("**Needs human review**\n\n" + "\n".join(f"- {r}" for r in k["review_reasons"]))
        else:
            st.success("Eligible for auto-accept under the review policy")
        base = k["baseline"]["category"]
        agree = a and a["category"] == base
        st.caption(f"Rules baseline says `{base}` - {'agrees' if agree else 'DISAGREES'} with the model")

    lv = pd.DataFrame(k["lines"])[["line_key", "account_type", "posting_date", "period", "counterparty_as_booked",
                                   "counterparty_resolved", "amount", "currency", "amount_usd", "reference",
                                   "description"]]
    st.dataframe(lv, hide_index=True, width="stretch")
    with st.expander("Evidence computed by the rules engine (sent to the model)"):
        st.json(k["evidence"])

    hist = [e for e in load_log() if e["case_key"] == case_key(k)]
    if hist:
        st.caption("Decision history")
        st.dataframe(pd.DataFrame(hist)[["timestamp", "reviewer", "status", "final_category", "comment"]],
                     hide_index=True, width="stretch")

    with st.form(f"decide_{case_key(k)}", clear_on_submit=True):
        st.markdown("**Record decision**")
        c1, c2 = st.columns([1, 1])
        action = c1.radio("Action", ["approve", "override", "escalate"], horizontal=True,
                          format_func={"approve": "Approve proposal", "override": "Override category",
                                       "escalate": "Escalate to controller"}.get)
        default = CATEGORIES.index(a["category"]) if a else len(CATEGORIES) - 1
        new_cat = c2.selectbox("Correct category (for override)", CATEGORIES, index=default)
        comment = st.text_input("Comment")
        if st.form_submit_button("Submit decision", type="primary"):
            if not reviewer.strip():
                st.error("Enter your name in the sidebar first.")
            elif action == "override" and a and new_cat == a["category"]:
                st.error("Pick a different category to override, or choose Approve.")
            else:
                record(k, action, reviewer.strip(), comment, new_cat)
                st.toast(f"{k['case_id']}: {action} recorded")
                st.rerun()


def page_queue():
    st.header("Exception review queue")
    rows = []
    for k in cases:
        a = k["assessment"] or {}
        rows.append({"case": k["case_id"], "status": status_of(k, decisions),
                     "review": "⚠️ yes" if k["needs_review"] else "no",
                     "ai_category": a.get("category", "—"), "confidence": a.get("confidence"),
                     "seller": k["seller"] or "?", "buyer": k["buyer"] or "?", "usd": case_usd(k),
                     "summary": (a.get("explanation") or "").split(". ")[0][:140]})
    df = pd.DataFrame(rows)

    f = st.columns(4)
    st_f = f[0].multiselect("Status", ["open", "approved", "overridden", "escalated"], default=["open"])
    rv_f = f[1].selectbox("Review flag", ["All", "Needs review", "Auto-accept eligible"])
    cat_f = f[2].multiselect("AI category", sorted(df.ai_category.unique()))
    ent_f = f[3].multiselect("Entity", sorted(set(df.seller) | set(df.buyer)))
    jump = st.text_input("Jump to case", placeholder="e.g. C0048", key="jump").strip().upper()
    if jump:
        df = df[df.case == jump]
    elif st_f:
        df = df[df.status.isin(st_f)]
    if rv_f != "All":
        df = df[df.review.str.startswith("⚠️") == (rv_f == "Needs review")]
    if cat_f:
        df = df[df.ai_category.isin(cat_f)]
    if ent_f:
        df = df[df.seller.isin(ent_f) | df.buyer.isin(ent_f)]
    df = df.sort_values(["review", "usd"], ascending=[False, False]).reset_index(drop=True)

    st.caption(f"{len(df)} cases · select a row to open it")
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key="queue",
                      column_config={"usd": st.column_config.NumberColumn("USD", format="$%.0f"),
                                     "confidence": st.column_config.ProgressColumn("conf.", min_value=0,
                                                                                   max_value=1, format="%.2f")})
    sel = ev.selection.rows
    if sel:
        cid = df.iloc[sel[0]]["case"]
        st.divider()
        render_case(next(k for k in cases if k["case_id"] == cid))


def page_counterparties():
    st.header("Counterparty mapping")
    st.write("Free-text counterparty names the master data could not resolve. Names the model was not "
             "confident about wait here for a human. Confirmed names are added to the alias table, so the "
             "next run resolves them deterministically - the model is needed less every month.")
    un = lines[lines.cp_method == "unresolved"]
    if un.empty:
        st.success("No unresolved counterparties.")
    for (ent, raw), g in un.groupby(["entity", "cp_raw"]):
        sugg = g.cp_suggestion.iloc[0] or None
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.markdown(f"**'{raw}'** in {ent} · {len(g)} lines")
            c1.caption(g.cp_reasoning.iloc[0] or "no model suggestion")
            options = [e for e in sorted(set(lines.entity)) if e != ent]
            pick = c2.selectbox("Maps to", options, index=options.index(sugg) if sugg in options else 0,
                                key=f"cp_{ent}_{raw}")
            if c3.button("Confirm", key=f"ok_{ent}_{raw}", width="stretch"):
                if not reviewer.strip():
                    st.error("Enter your name in the sidebar first.")
                else:
                    confirm_alias(raw, pick, reviewer.strip())
                    st.toast(f"'{raw}' → {pick} saved. Re-run the pipeline to apply.")
            with st.expander("Lines"):
                st.dataframe(g[["line_key", "side", "posting_date", "amount", "currency", "ref", "desc"]],
                             hide_index=True, width="stretch")
    if OVERRIDES_FILE.exists():
        st.subheader("Human-confirmed aliases")
        st.dataframe(pd.read_csv(OVERRIDES_FILE), hide_index=True, width="stretch")
        st.caption("Re-run the pipeline (sidebar) to apply new confirmations.")
    st.subheader("Resolved by the model (above confidence threshold) - spot-check")
    lm = lines[lines.cp_method == "llm"].groupby(["entity", "cp_raw", "counterparty"]).agg(
        lines=("line_key", "size"), confidence=("cp_confidence", "max"), reasoning=("cp_reasoning", "first"))
    st.dataframe(lm.reset_index(), hide_index=True, width="stretch")


def page_matches():
    st.header("Rule-based auto-matches")
    st.write("Deterministic matches need no model call. Lower-confidence rules (amount + date without a "
             "reference, ambiguous recurring amounts) are listed for spot-checks.")
    c1, c2 = st.columns(2)
    types = c1.multiselect("Match type", sorted(matches.match_type.unique()))
    max_conf = c2.slider("Show matches with confidence up to", 0.7, 1.0, 1.0, 0.01)
    m = matches[matches.confidence <= max_conf]
    if types:
        m = m[m.match_type.isin(types)]
    st.caption(f"{len(m)} of {len(matches)} matches")
    st.dataframe(m[["match_id", "seller", "buyer", "match_type", "confidence", "amount", "currency",
                    "amount_usd", "seller_date", "buyer_dates", "seller_lines", "buyer_lines"]],
                 hide_index=True, width="stretch")


def page_audit():
    st.header("Audit log")
    log = load_log()
    if not log:
        st.info("No decisions recorded yet.")
        return
    df = pd.DataFrame(log)
    st.dataframe(df, hide_index=True, width="stretch")
    over = df[df.action == "override"]
    st.caption(f"{len(df)} decisions · {len(over)} overrides (these become labelled examples for the next "
               f"evaluation and prompt revision)")
    st.download_button("Download CSV", df.to_csv(index=False), "audit_log.csv", "text/csv")


def page_eval():
    st.header("Evaluation against the synthetic answer key")
    st.caption("Demo only: the mock-data generator's ground truth makes the pipeline measurable. "
               "In production this is replaced by reviewer decisions (approve/override rates).")
    p = config.OUT / "eval.md"
    st.markdown(p.read_text(encoding="utf-8") if p.exists() else "Run the pipeline with evaluation first.")


{"Start here": page_start, "Overview": page_overview, "Review queue": page_queue, "Counterparty mapping": page_counterparties,
 "Auto-matches": page_matches, "Audit log": page_audit, "Evaluation": page_eval}[page]()
