"""
Normalise heterogeneous ERP exports into one canonical line table.

Adding a new ERP/entity = adding one entry to SCHEMAS (no code change). This is the part
that a production build would replace with real connectors; the canonical schema is the
contract between ingestion and everything downstream.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import config

# Per-entity export schema. `rec_prefix` identifies the IC receivable account; everything
# else in the extract is the IC payable account.
SCHEMAS: dict[str, dict] = {
    "NIG-US": dict(sep=",", id="JE_LINE_ID", date="GL_DATE", date_fmt="%m/%d/%Y", account="ACCOUNT",
                   rec_prefix="01-1410", cp="INTERCO_SEGMENT", cp_kind="oracle_segment",
                   desc="DESCRIPTION", dr="ENTERED_DR", cr="ENTERED_CR", ccy="CURRENCY_CODE", ref="REFERENCE"),
    "NIG-UK": dict(sep=",", id="DocumentNo", date="PostingDate", date_fmt="%d.%m.%Y", account="GLAccount",
                   rec_prefix="154", cp="TradingPartner", cp_kind="sap_trading_partner",
                   desc="Text", amount="AmountDocCurr", ccy="DocCurrency", ref="Reference"),
    "NIG-DE": dict(sep=";", id="Belegnummer", date="Buchungsdatum", date_fmt="%d.%m.%Y", account="Sachkonto",
                   rec_prefix="154", cp="Partnergesellschaft", cp_kind="sap_trading_partner",
                   desc="Buchungstext", amount="Betrag", decimal_comma=True, ccy="Währung", ref="Referenz"),
    "NIG-AE": dict(sep=",", id="Voucher", date="TransDate", date_fmt="%Y-%m-%d", account="MainAccount",
                   rec_prefix="131", cp="CounterpartyName", cp_kind="legal_name",
                   desc="Description", amount="AmountCur", ccy="CurrencyCode", ref="DocumentNumber"),
    "NIG-SA": dict(sep=",", id="Voucher", date="TransDate", date_fmt="%Y-%m-%d", account="MainAccount",
                   rec_prefix="131", cp="CounterpartyName", cp_kind="legal_name",
                   desc="Description", amount="AmountCur", ccy="CurrencyCode", ref="DocumentNumber"),
    "NIG-AU": dict(sep=",", id="txn_id", date="date", date_fmt="%d/%m/%Y", account="acct",
                   rec_prefix="IC-REC", cp="ic_entity", cp_kind="free_text",
                   desc="memo", amount="amount", ccy="ccy", ref="ref"),
}


def _num(s: pd.Series, decimal_comma: bool = False) -> pd.Series:
    s = s.fillna("").astype(str).str.strip()
    if decimal_comma:
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def load_entity(entity: str) -> pd.DataFrame:
    sc = SCHEMAS[entity]
    df = pd.read_csv(config.RAW / f"ledger_{entity}.csv", sep=sc["sep"], dtype=str,
                     keep_default_na=False, encoding="utf-8")
    if "dr" in sc:
        signed = _num(df[sc["dr"]]) - _num(df[sc["cr"]])
    else:
        signed = _num(df[sc["amount"]], sc.get("decimal_comma", False))
    return pd.DataFrame({
        "entity": entity,
        "native_id": df[sc["id"]],
        "posting_date": pd.to_datetime(df[sc["date"]], format=sc["date_fmt"]).dt.date,
        "side": df[sc["account"]].str.startswith(sc["rec_prefix"]).map({True: "REC", False: "PAY"}),
        "cp_raw": df[sc["cp"]].str.strip(),
        "cp_kind": sc["cp_kind"],
        "amount": signed.abs().round(2),
        "currency": df[sc["ccy"]].str.upper().str.strip(),
        "ref": df[sc["ref"]].str.strip(),
        "desc": df[sc["desc"]].str.strip(),
    })


@dataclass
class Context:
    manifest: dict
    period_end: date
    entities: pd.DataFrame
    fx_daily: dict
    fx_budget: dict

    def usd(self, ccy: str, d: date) -> float:
        return self.fx_daily[(ccy, d)]


def load_context() -> Context:
    manifest = json.loads((config.RAW / "extract_manifest.json").read_text(encoding="utf-8"))
    ents = pd.read_csv(config.REFERENCE / "entities.csv", dtype=str, keep_default_na=False)
    fx = pd.read_csv(config.REFERENCE / "fx_rates.csv", dtype={"usd_per_unit": float}, keep_default_na=False)
    daily = {(r.currency, date.fromisoformat(r.date)): r.usd_per_unit
             for r in fx[fx.rate_type == "daily"].itertuples()}
    budget = {r.currency: r.usd_per_unit for r in fx[fx.rate_type == "budget_2026"].itertuples()}
    return Context(manifest, date.fromisoformat(manifest["period_end"]), ents, daily, budget)


def load_lines(ctx: Context) -> pd.DataFrame:
    lines = pd.concat([load_entity(e) for e in SCHEMAS], ignore_index=True)
    lines["line_key"] = lines.entity + ":" + lines.native_id
    lines["in_period"] = lines.posting_date <= ctx.period_end
    lines["amount_usd"] = [round(a * ctx.usd(c, d), 2) for a, c, d in
                           zip(lines.amount, lines.currency, lines.posting_date)]
    return lines
