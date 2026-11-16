#!/usr/bin/env python3
"""
Generate mock intercompany (IC) ledger extracts for NorthStar Infrastructure Group (NIG),
October 2026 month-end close.

Design goal: the data should EXPOSE the hard parts of IC reconciliation, not hide them:
  * 6 legal entities on 5 different ERPs, each with its own export schema
    (column names, date formats, sign conventions, decimal separators, language)
  * counterparty identifiers differ per system (Oracle segments, SAP trading partners,
    legal names, free text in the acquired Australian entity's legacy system)
  * inconsistent / missing invoice references
  * realistic break types: FX, timing (cut-off), miscoding, typos, duplicates, missing
    bookings, split bookings, Saudi withholding tax, disputes, accruals without invoice
  * recurring fixed-amount fees that make matching ambiguous

Outputs (default ./data):
  raw/ledger_<entity>.<csv>        what the pipeline sees (one export per entity)
  reference/entities.csv           entity master data incl. (incomplete) alias list
  reference/fx_rates.csv           daily + 2026 budget rates (USD per unit)
  raw/extract_manifest.json        period, extract date, file formats
  ground_truth/labels.csv          answer key per true transaction (DO NOT feed to pipeline)
  ground_truth/summary.json        counts per break category

Stdlib only. Deterministic for a given --seed.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

PERIOD = "2026-10"
PERIOD_START = date(2026, 10, 1)
PERIOD_END = date(2026, 10, 31)
EXTRACT_END = date(2026, 11, 10)  # extracts include early-November postings

# --------------------------------------------------------------------------------------
# Master data
# --------------------------------------------------------------------------------------
ENTITIES = {
    "NIG-US": dict(legal_name="NorthStar Infrastructure Group Inc.", country="US", currency="USD",
                   erp="Oracle EBS 12.2", oracle_segment="100", sap_code="US01", lang="en"),
    "NIG-UK": dict(legal_name="NorthStar Infrastructure UK Ltd", country="GB", currency="GBP",
                   erp="SAP S/4HANA (EN)", oracle_segment="200", sap_code="GB01", lang="en"),
    "NIG-DE": dict(legal_name="NorthStar Infrastruktur GmbH", country="DE", currency="EUR",
                   erp="SAP ECC 6.0 (DE)", oracle_segment="300", sap_code="DE01", lang="de"),
    "NIG-AE": dict(legal_name="NorthStar Infrastructure Middle East LLC", country="AE", currency="AED",
                   erp="Dynamics 365 F&O", oracle_segment="400", sap_code="AE01", lang="en"),
    "NIG-SA": dict(legal_name="NorthStar Infrastructure Saudi Arabia Co.", country="SA", currency="SAR",
                   erp="Dynamics 365 F&O", oracle_segment="410", sap_code="SA01", lang="en"),
    "NIG-AU": dict(legal_name="NorthStar Infrastructure Australia Pty Ltd", country="AU", currency="AUD",
                   erp="Legacy SCE Ledger (acquired Southern Cross Engineering, 2023)",
                   oracle_segment="500", sap_code="AU01", lang="en"),
}
SHORT = {k: k.split("-")[1] for k in ENTITIES}

# Free-text counterparty names typed by AU staff. Only the first two per entity are in the
# master alias list -> the rest must be resolved some other way (fuzzy match / LLM / human).
AU_FREE_TEXT = {
    "NIG-US": ["NIG US", "NorthStar US", "US HQ", "Head Office Denver"],
    "NIG-UK": ["NIG UK", "Northstar UK", "NS UK", "London office"],
    "NIG-DE": ["NIG DE", "Northstar GmbH", "Germany", "NS Infrastruktur"],
    "NIG-AE": ["NIG ME", "Northstar Dubai", "NS UAE", "Middle East LLC"],
    "NIG-SA": ["NIG KSA", "Northstar Saudi", "NS Riyadh", "KSA branch"],
}

PEGGED = {"USD": 1.0, "AED": 0.27229, "SAR": 0.26667}
FLOAT_BASE = {"GBP": 1.27, "EUR": 1.08, "AUD": 0.66}
BUDGET_DRIFT = {"GBP": 0.965, "EUR": 1.04, "AUD": 1.05}  # budget rate vs Oct actual level

PROJECTS = [
    ("P-24017", "Riyadh Metro Line 7"), ("P-23110", "Thames Tideway Support"),
    ("P-25002", "Sydney Harbour Tunnel Upgrade"), ("P-24088", "A8 Autobahn Bridge Rehab"),
    ("P-25031", "Dubai Creek Water Treatment"), ("P-24050", "I-95 Corridor Resurfacing"),
    ("P-25019", "NEOM Utilities Package 3"), ("P-24102", "HS2 Area North Design"),
]
WORK_EN = ["design review", "site supervision", "geotech analysis", "BIM coordination",
           "structural calcs", "QA/QC inspection", "cost estimating", "environmental survey"]
WORK_DE = ["Entwurfsprüfung", "Bauüberwachung", "Geotechnik", "BIM-Koordination",
           "Statik", "Qualitätsprüfung", "Kostenschätzung", "Umweltgutachten"]
PRODUCTS = ["Autodesk AEC", "Bentley OpenRoads", "Esri ArcGIS", "Microsoft 365 E5", "Primavera P6"]
FIRST = ["James", "Aisha", "Lukas", "Priya", "Omar", "Sophie", "Daniel", "Fatima", "Chen", "Emma",
         "Mohammed", "Hannah", "Rahul", "Olivia", "Khalid", "Isabel", "Tom", "Noura", "Felix", "Grace"]
LAST = ["Smith", "Al-Mansouri", "Becker", "Sharma", "Haddad", "Turner", "Müller", "Rahman", "Wang",
        "Clarke", "Al-Qahtani", "Schmidt", "Patel", "Brown", "Nasser", "Garcia", "Walsh", "Hassan",
        "Fischer", "O'Connor"]

CATEGORY_WEIGHTS = {
    "clean": 0.66, "fuzzy_ref": 0.12, "fx_difference": 0.06, "timing": 0.06, "split": 0.04,
    "miscoded_counterparty": 0.03, "amount_typo": 0.03, "duplicate": 0.025,
    "missing_buyer": 0.025, "dispute": 0.015, "accrual_no_invoice": 0.015,
}
EXPECTED_ACTION = {
    "clean": "auto_match",
    "fuzzy_ref": "auto_match (reference mismatch only)",
    "fx_difference": "buyer revalues at invoice-date rate / post FX adjustment",
    "timing": "no adjustment; cut-off timing item, clears next period",
    "split": "match one-to-many",
    "miscoded_counterparty": "buyer reclasses line to correct counterparty",
    "amount_typo": "buyer corrects amount",
    "duplicate": "buyer reverses duplicate line",
    "missing_buyer": "buyer books invoice (or confirms receipt)",
    "dispute": "escalate to entity controllers; agree amount",
    "accrual_no_invoice": "seller confirms and issues invoice, or buyer reverses accrual",
    "wht": "match gross; buyer reclasses withholding tax from IC payable to tax liability",
}
WHT_RATE = {"MGMT_FEE": 0.20, "LICENSE": 0.15}  # KSA WHT; everything else 5%


# --------------------------------------------------------------------------------------
@dataclass
class Line:
    uid: int
    entity: str
    posting_date: date
    side: str               # "REC" (IC receivable, seller) or "PAY" (IC payable, buyer)
    counterparty: str       # entity code as booked (may be wrong!)
    amount: float           # positive, in `currency`
    currency: str
    ref: str
    desc: str
    native_id: str = ""


@dataclass
class Txn:
    tx_id: str
    type: str
    seller: str
    buyer: str
    invoice_date: date
    currency: str
    amount: float
    seller_ref: str
    seller_desc: dict       # lang -> text
    buyer_desc: dict
    category: str = "clean"
    lines_seller: list = field(default_factory=list)
    lines_buyer: list = field(default_factory=list)
    wrong_cp: str = ""
    note: str = ""


class Gen:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.uid = 0
        self.lines: list[Line] = []
        self.txns: list[Txn] = []
        self.inv_counters = defaultdict(lambda: self.rng.randint(200, 900))
        self.fx = self._make_fx()

    # ---------------------------------------------------------------- FX
    def _make_fx(self):
        rates = {}
        d = PERIOD_START - timedelta(days=5)
        level = dict(FLOAT_BASE)
        while d <= EXTRACT_END:
            for c in level:
                level[c] *= 1 + self.rng.gauss(0, 0.0045)
            for c, v in PEGGED.items():
                rates[(c, d)] = v
            for c, v in level.items():
                rates[(c, d)] = round(v, 5)
            d += timedelta(days=1)
        return rates

    def usd(self, ccy: str, d: date) -> float:
        return self.fx[(ccy, d)]

    def budget(self, ccy: str) -> float:
        if ccy in PEGGED:
            return PEGGED[ccy]
        return round(FLOAT_BASE[ccy] * BUDGET_DRIFT[ccy], 5)

    # ---------------------------------------------------------------- helpers
    def new_line(self, **kw) -> Line:
        self.uid += 1
        ln = Line(uid=self.uid, **kw)
        self.lines.append(ln)
        return ln

    def invoice_ref(self, seller: str) -> str:
        self.inv_counters[seller] += self.rng.randint(1, 4)
        n = self.inv_counters[seller]
        return {
            "NIG-US": f"INV-US-{100000 + n}",
            "NIG-UK": f"{9000010000 + n}",
            "NIG-DE": f"RE-2026-{n:06d}",
            "NIG-AE": f"AE-ICINV-{n:05d}",
            "NIG-SA": f"SA-ICINV-{n:05d}",
            "NIG-AU": f"AU/IC/2026/{n:04d}",
        }[seller]

    def ref_variant(self, ref: str) -> str:
        """How a buyer might re-key the seller's invoice number."""
        r = self.rng.random()
        digits = "".join(ch for ch in ref if ch.isdigit())
        if r < 0.4:
            return digits.lstrip("0") or digits
        if r < 0.7:
            return ref.lower().replace("-", "").replace("/", "")
        return f"INV {ref}"

    def rand_date(self, start: date, end: date) -> date:
        return start + timedelta(days=self.rng.randint(0, (end - start).days))

    def emp(self):
        f, l = self.rng.choice(FIRST), self.rng.choice(LAST)
        return f"{f} {l}", f"{f[0]}.{l}"

    # ---------------------------------------------------------------- transactions
    def add_txn(self, ttype, seller, buyer, d, amount, sdesc, bdesc):
        tx = Txn(tx_id=f"T{len(self.txns) + 1:04d}", type=ttype, seller=seller, buyer=buyer,
                 invoice_date=d, currency=ENTITIES[seller]["currency"], amount=round(amount, 2),
                 seller_ref=self.invoice_ref(seller), seller_desc=sdesc, buyer_desc=bdesc)
        self.txns.append(tx)

    def build_transactions(self, n_secondments: int, n_projects: int):
        ents = list(ENTITIES)
        month_end = (date(2026, 10, 28), PERIOD_END)

        # Management fees: HQ -> every entity, fixed monthly amount (recurring = ambiguity)
        mgmt = {"NIG-UK": 85000, "NIG-DE": 92000, "NIG-AE": 64000, "NIG-SA": 71000, "NIG-AU": 48000}
        for b, amt in mgmt.items():
            self.add_txn("MGMT_FEE", "NIG-US", b, self.rand_date(*month_end), amt,
                         {"en": f"Mgmt fee Oct-26 - {SHORT[b]}", "de": f"Managementgebühr Okt-26 - {SHORT[b]}"},
                         {"en": "Management fee Oct-26 - US", "de": "Konzernumlage Management Okt-26"})

        # IT shared services: UK hosts the shared service centre
        it_fee = {"NIG-US": 21500, "NIG-DE": 14800, "NIG-AE": 9900, "NIG-SA": 9900, "NIG-AU": 7600}
        for b, amt in it_fee.items():
            self.add_txn("IT_SHARED", "NIG-UK", b, self.rand_date(*month_end), amt,
                         {"en": "IT shared services Oct 2026", "de": "IT-Shared-Services Okt 2026"},
                         {"en": "IT services recharge UK", "de": "IT-Umlage UK"})
            # licence recharges, sometimes several per buyer with similar amounts
            for _ in range(self.rng.randint(2, 5)):
                p = self.rng.choice(PRODUCTS)
                self.add_txn("LICENSE", "NIG-UK", b, self.rand_date(date(2026, 10, 5), date(2026, 10, 27)),
                             self.rng.choice([1, 1, 2, 5, 10]) * self.rng.choice([420.0, 1150.0, 2380.0, 3900.0]),
                             {"en": f"Software licence recharge - {p}", "de": f"Lizenzweiterbelastung - {p}"},
                             {"en": f"{p} licences from UK", "de": f"Lizenzen {p} UK"})

        # Interest on IC loans from HQ
        for b, principal, lid in [("NIG-AE", 12_000_000, "L-2021-07"), ("NIG-AU", 8_500_000, "L-2023-02"),
                                  ("NIG-DE", 5_000_000, "L-2024-11")]:
            self.add_txn("LOAN_INTEREST", "NIG-US", b, PERIOD_END, principal * 0.0575 / 12,
                         {"en": f"Interest IC loan {lid} Oct-26", "de": f"Zinsen IC-Darlehen {lid} Okt-26"},
                         {"en": f"Loan interest {lid}", "de": f"Darlehenszinsen {lid}"})

        # Secondments (staff recharges) between any two entities
        for _ in range(n_secondments):
            s, b = self.rng.sample(ents, 2)
            name, short = self.emp()
            code, _pname = self.rng.choice(PROJECTS)
            hours = self.rng.choice([8, 16, 24, 40, 60, 80, 120, 152, 168])
            rate = self.rng.choice([95, 110, 125, 140, 165, 185, 210]) * (5 if ENTITIES[s]["currency"] in ("AED", "SAR") else 1)
            self.add_txn("SECONDMENT", s, b, self.rand_date(PERIOD_START, PERIOD_END), hours * rate,
                         {"en": f"Secondment {name} {hours}h @ {rate} - {code}",
                          "de": f"Personalgestellung {name} {hours} Std - {code}"},
                         {"en": f"Seconded staff {short} {code}", "de": f"Personalgestellung {short} {code}"})

        # Project recharges: weighted toward entities that co-deliver projects
        pairs = [("NIG-AE", "NIG-SA")] * 4 + [("NIG-SA", "NIG-AE")] * 3 + [("NIG-UK", "NIG-DE")] * 3 + \
                [("NIG-DE", "NIG-UK")] * 2 + [("NIG-US", "NIG-AU")] * 2 + [("NIG-AU", "NIG-US")] * 2 + \
                [("NIG-UK", "NIG-SA")] * 2 + [("NIG-UK", "NIG-AE")] * 2 + [("NIG-DE", "NIG-SA")]
        for _ in range(n_projects):
            s, b = self.rng.choice(pairs)
            code, pname = self.rng.choice(PROJECTS)
            wi = self.rng.randrange(len(WORK_EN))
            amt = round(self.rng.lognormvariate(10.6, 0.9), -1)
            amt = min(max(amt, 4500), 480000) * (3.6 if ENTITIES[s]["currency"] in ("AED", "SAR") else 1)
            self.add_txn("PROJECT_RECHARGE", s, b, self.rand_date(PERIOD_START, PERIOD_END), amt,
                         {"en": f"Project recharge {code} {pname} - {WORK_EN[wi]}",
                          "de": f"Projektweiterbelastung {code} - {WORK_DE[wi]}"},
                         {"en": f"{code} subcontract {SHORT[s]} - {WORK_EN[wi]}",
                          "de": f"{code} Leistungen {SHORT[s]} - {WORK_DE[wi]}"})

    # ---------------------------------------------------------------- break injection
    def pick_category(self, tx: Txn) -> str:
        if tx.buyer == "NIG-SA" and self.rng.random() < 0.40:
            return "wht"
        cats, weights = zip(*CATEGORY_WEIGHTS.items())
        while True:
            c = self.rng.choices(cats, weights)[0]
            if c == "fx_difference":
                bc = ENTITIES[tx.buyer]["currency"]
                if tx.currency in PEGGED and bc in PEGGED:
                    continue  # no FX effect between pegged currencies
            return c

    def desc(self, tx: Txn, who: str, extra: str = "") -> str:
        ent = tx.seller if who == "seller" else tx.buyer
        lang = ENTITIES[ent]["lang"]
        txt = (tx.seller_desc if who == "seller" else tx.buyer_desc)[lang]
        if ent == "NIG-AU" and who == "buyer":
            txt = txt.lower().replace(" - ", " ")
        return f"{txt}{extra}"

    def buyer_date(self, tx: Txn) -> date:
        return min(tx.invoice_date + timedelta(days=self.rng.randint(0, 6)), PERIOD_END)

    def inject(self):
        for tx in self.txns:
            cat = self.pick_category(tx)
            if cat == "timing" and tx.invoice_date < date(2026, 10, 24):
                tx.invoice_date = self.rand_date(date(2026, 10, 24), PERIOD_END)
            tx.category = cat
            s, b = tx.seller, tx.buyer

            # ---- seller side
            if cat != "accrual_no_invoice":
                tx.lines_seller.append(self.new_line(
                    entity=s, posting_date=tx.invoice_date, side="REC", counterparty=b,
                    amount=tx.amount, currency=tx.currency, ref=tx.seller_ref, desc=self.desc(tx, "seller")))

            # ---- buyer side
            bl = dict(entity=b, side="PAY", counterparty=s, currency=tx.currency)
            ref_ok = tx.seller_ref if self.rng.random() < 0.8 else self.ref_variant(tx.seller_ref)

            if cat == "clean":
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=tx.amount,
                                                    ref=ref_ok, desc=self.desc(tx, "buyer")))
            elif cat == "fuzzy_ref":
                mode = self.rng.choice(["missing", "in_desc", "garbled"])
                ref, extra = "", ""
                if mode == "in_desc":
                    extra = f" inv {self.ref_variant(tx.seller_ref)}"
                elif mode == "garbled":
                    ref = self.ref_variant(tx.seller_ref)[:-1] + str(self.rng.randint(0, 9))
                tx.note = f"buyer reference {mode}"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=tx.amount,
                                                    ref=ref, desc=self.desc(tx, "buyer", extra)))
            elif cat == "fx_difference":
                bc = ENTITIES[b]["currency"]
                amt_b = round(tx.amount * self.budget(tx.currency) / self.budget(bc), 2)
                bl["currency"] = bc
                tx.note = f"buyer booked in {bc} at 2026 budget rate instead of invoice-date rate"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=amt_b,
                                                    ref=ref_ok, desc=self.desc(tx, "buyer")))
            elif cat == "timing":
                d = self.rand_date(date(2026, 11, 2), EXTRACT_END)
                tx.note = f"buyer posted {d.isoformat()} (November)"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=d, amount=tx.amount,
                                                    ref=ref_ok, desc=self.desc(tx, "buyer")))
            elif cat == "split":
                k = self.rng.choice([2, 2, 3])
                cuts = sorted(self.rng.uniform(0.15, 0.85) for _ in range(k - 1))
                parts, prev = [], 0.0
                for c in cuts:
                    parts.append(round(tx.amount * (c - prev), 2))
                    prev = c
                parts.append(round(tx.amount - sum(parts), 2))
                d = self.buyer_date(tx)
                for i, p in enumerate(parts):
                    tx.lines_buyer.append(self.new_line(
                        **bl, posting_date=d, amount=p, ref=ref_ok if i == 0 else self.rng.choice([ref_ok, ""]),
                        desc=self.desc(tx, "buyer", f" CC{self.rng.choice([4100, 4200, 4310, 5150])}")))
                tx.note = f"buyer split into {k} lines by cost centre"
            elif cat == "miscoded_counterparty":
                wrong = self.rng.choice([e for e in ENTITIES if e not in (s, b)])
                tx.wrong_cp = wrong
                bl["counterparty"] = wrong
                tx.note = f"buyer booked against {wrong} instead of {s}"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=tx.amount,
                                                    ref=ref_ok, desc=self.desc(tx, "buyer")))
            elif cat == "amount_typo":
                amt = self._typo(tx.amount)
                tx.note = f"buyer keyed {amt:.2f} instead of {tx.amount:.2f}"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=amt,
                                                    ref=ref_ok, desc=self.desc(tx, "buyer")))
            elif cat == "duplicate":
                d1 = self.buyer_date(tx)
                d2 = min(d1 + timedelta(days=self.rng.randint(1, 5)), PERIOD_END)
                for d in (d1, d2):
                    tx.lines_buyer.append(self.new_line(**bl, posting_date=d, amount=tx.amount,
                                                        ref=ref_ok, desc=self.desc(tx, "buyer")))
                tx.note = "buyer posted invoice twice"
            elif cat == "missing_buyer":
                tx.note = "buyer has no booking in October or early November"
            elif cat == "dispute":
                amt = round(tx.amount * self.rng.uniform(0.72, 0.92), 2)
                extra = " - partial accept, hours disputed pending PM approval" if ENTITIES[b]["lang"] == "en" \
                    else " - Teilfreigabe, Stunden strittig"
                tx.note = f"buyer accepted only {amt:.2f} of {tx.amount:.2f}"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=amt,
                                                    ref=ref_ok, desc=self.desc(tx, "buyer", extra)))
            elif cat == "accrual_no_invoice":
                tx.seller_ref = ""
                tx.note = "buyer accrued cost; seller has not invoiced"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=PERIOD_END, amount=tx.amount,
                                                    ref="ACCR-OCT26",
                                                    desc="Accrual - " + self.desc(tx, "buyer", " - invoice not received")))
            elif cat == "wht":
                rate = WHT_RATE.get(tx.type, 0.05)
                amt = round(tx.amount * (1 - rate), 2)
                tx.note = f"buyer booked net of {int(rate * 100)}% KSA withholding tax"
                tx.lines_buyer.append(self.new_line(**bl, posting_date=self.buyer_date(tx), amount=amt, ref=ref_ok,
                                                    desc=self.desc(tx, "buyer", f" net of WHT {int(rate * 100)}%")))

    def _typo(self, amount: float) -> float:
        s = f"{amount:.2f}"
        intpart = s.split(".")[0]
        idx = [i for i in range(len(intpart) - 1) if intpart[i] != intpart[i + 1]]
        if idx and self.rng.random() < 0.75:
            i = self.rng.choice(idx)
            t = intpart[:i] + intpart[i + 1] + intpart[i] + intpart[i + 2:]
            return float(t + "." + s.split(".")[1])
        return round(amount + self.rng.choice([-1000, 1000, -100, 90]), 2)

    # ---------------------------------------------------------------- ids + diff
    def assign_ids(self):
        by_ent = defaultdict(list)
        for ln in self.lines:
            by_ent[ln.entity].append(ln)
        start = {"NIG-US": 4471020, "NIG-UK": 1900045210, "NIG-DE": 5100873300,
                 "NIG-AE": 31200, "NIG-SA": 18800, "NIG-AU": 77410}
        for ent, lns in by_ent.items():
            lns.sort(key=lambda x: (x.posting_date, self.rng.random()))
            n = start[ent]
            for ln in lns:
                n += self.rng.randint(1, 3)
                if ent in ("NIG-AE", "NIG-SA"):
                    ln.native_id = f"ICV-{SHORT[ent]}-{n:06d}"
                else:
                    ln.native_id = str(n)
        return by_ent

    def oct_diff_usd(self, tx: Txn) -> float:
        """Seller minus buyer, October postings under the CORRECT counterparty, in USD at
        invoice-date rates (convention documented in the manifest)."""
        def tot(lines, cp):
            return sum(l.amount * self.usd(l.currency, tx.invoice_date)
                       for l in lines if l.posting_date <= PERIOD_END and l.counterparty == cp)
        return round(tot(tx.lines_seller, tx.buyer) - tot(tx.lines_buyer, tx.seller), 2)


# --------------------------------------------------------------------------------------
# Writers: each ERP exports differently
# --------------------------------------------------------------------------------------
def de_num(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def write_ledgers(by_ent, raw: Path, rng: random.Random):
    raw.mkdir(parents=True, exist_ok=True)
    files = {}

    def w(name, header, rows, delim=","):
        p = raw / name
        with p.open("w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f, delimiter=delim)
            wr.writerow(header)
            wr.writerows(rows)
        files[name] = {"rows": len(rows), "delimiter": delim}

    # Oracle EBS: separate DR/CR columns, US dates, interco segment codes
    rows = []
    for l in by_ent["NIG-US"]:
        acct = "01-1410-000" if l.side == "REC" else "01-2410-000"
        dr, cr = (f"{l.amount:.2f}", "") if l.side == "REC" else ("", f"{l.amount:.2f}")
        rows.append([l.native_id, l.posting_date.strftime("%m/%d/%Y"), acct,
                     ENTITIES[l.counterparty]["oracle_segment"], l.desc, dr, cr, l.currency, l.ref])
    w("ledger_NIG-US.csv", ["JE_LINE_ID", "GL_DATE", "ACCOUNT", "INTERCO_SEGMENT", "DESCRIPTION",
                            "ENTERED_DR", "ENTERED_CR", "CURRENCY_CODE", "REFERENCE"], rows)

    # SAP S/4 (English): signed amounts, dd.mm.yyyy, trading partner codes
    rows = []
    for l in by_ent["NIG-UK"]:
        amt = l.amount if l.side == "REC" else -l.amount
        rows.append([l.native_id, l.posting_date.strftime("%d.%m.%Y"),
                     "154000" if l.side == "REC" else "254000", ENTITIES[l.counterparty]["sap_code"],
                     l.desc, f"{amt:.2f}", l.currency, l.ref])
    w("ledger_NIG-UK.csv", ["DocumentNo", "PostingDate", "GLAccount", "TradingPartner", "Text",
                            "AmountDocCurr", "DocCurrency", "Reference"], rows)

    # SAP ECC (German locale): semicolon delimiter, decimal comma, German headers/text
    rows = []
    for l in by_ent["NIG-DE"]:
        amt = l.amount if l.side == "REC" else -l.amount
        rows.append([l.native_id, l.posting_date.strftime("%d.%m.%Y"),
                     "1540000" if l.side == "REC" else "2540000", ENTITIES[l.counterparty]["sap_code"],
                     l.desc, de_num(amt), l.currency, l.ref])
    w("ledger_NIG-DE.csv", ["Belegnummer", "Buchungsdatum", "Sachkonto", "Partnergesellschaft",
                            "Buchungstext", "Betrag", "Währung", "Referenz"], rows, delim=";")

    # Dynamics 365: ISO dates, counterparty by legal name
    for ent in ("NIG-AE", "NIG-SA"):
        rows = []
        for l in by_ent[ent]:
            amt = l.amount if l.side == "REC" else -l.amount
            rows.append([l.native_id, l.posting_date.isoformat(), "131000" if l.side == "REC" else "231000",
                         ENTITIES[l.counterparty]["legal_name"], l.desc, f"{amt:.2f}", l.currency, l.ref])
        w(f"ledger_{ent}.csv", ["Voucher", "TransDate", "MainAccount", "CounterpartyName", "Description",
                                "AmountCur", "CurrencyCode", "DocumentNumber"], rows)

    # Legacy AU system: free-text counterparty, D/C flag, dd/mm/yyyy
    rows = []
    for l in by_ent["NIG-AU"]:
        rows.append([l.native_id, l.posting_date.strftime("%d/%m/%Y"),
                     "IC-REC" if l.side == "REC" else "IC-PAY", rng.choice(AU_FREE_TEXT[l.counterparty]),
                     l.desc, f"{l.amount:.2f}", "D" if l.side == "REC" else "C", l.currency, l.ref])
    w("ledger_NIG-AU.csv", ["txn_id", "date", "acct", "ic_entity", "memo", "amount", "dr_cr", "ccy", "ref"], rows)
    return files


def write_reference(g: Gen, ref: Path):
    ref.mkdir(parents=True, exist_ok=True)
    with (ref / "entities.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["entity_code", "legal_name", "country", "functional_currency", "erp",
                     "oracle_segment", "sap_trading_partner", "known_aliases"])
        for code, e in ENTITIES.items():
            aliases = AU_FREE_TEXT.get(code, [])[:2]  # deliberately incomplete
            wr.writerow([code, e["legal_name"], e["country"], e["currency"], e["erp"],
                         e["oracle_segment"], e["sap_code"], "|".join(aliases)])
    with (ref / "fx_rates.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["rate_type", "date", "currency", "usd_per_unit"])
        for (c, d), v in sorted(g.fx.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            wr.writerow(["daily", d.isoformat(), c, v])
        for c in list(PEGGED) + list(FLOAT_BASE):
            wr.writerow(["budget_2026", "", c, g.budget(c)])


def write_ground_truth(g: Gen, gt: Path):
    gt.mkdir(parents=True, exist_ok=True)
    ids = lambda lines: "|".join(f"{l.entity}:{l.native_id}" for l in lines)
    with (gt / "labels.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["tx_id", "type", "category", "seller", "buyer", "invoice_date", "currency", "amount",
                     "seller_ref", "seller_line_ids", "buyer_line_ids", "wrong_counterparty",
                     "oct_diff_usd", "expected_action", "note"])
        for tx in g.txns:
            wr.writerow([tx.tx_id, tx.type, tx.category, tx.seller, tx.buyer, tx.invoice_date.isoformat(),
                         tx.currency, f"{tx.amount:.2f}", tx.seller_ref, ids(tx.lines_seller),
                         ids(tx.lines_buyer), tx.wrong_cp, f"{g.oct_diff_usd(tx):.2f}",
                         EXPECTED_ACTION[tx.category], tx.note])
    cats = Counter(tx.category for tx in g.txns)
    summary = {
        "transactions": len(g.txns),
        "ledger_lines": len(g.lines),
        "by_category": dict(cats.most_common()),
        "by_type": dict(Counter(tx.type for tx in g.txns).most_common()),
        "total_abs_oct_diff_usd": round(sum(abs(g.oct_diff_usd(t)) for t in g.txns), 2),
    }
    (gt / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--secondments", type=int, default=170)
    ap.add_argument("--projects", type=int, default=150)
    a = ap.parse_args()

    out = Path(a.out)
    g = Gen(a.seed)
    g.build_transactions(a.secondments, a.projects)
    g.inject()
    by_ent = g.assign_ids()
    files = write_ledgers(by_ent, out / "raw", g.rng)
    write_reference(g, out / "reference")
    manifest = {
        "company": "NorthStar Infrastructure Group (fictional)",
        "period": PERIOD,
        "period_end": PERIOD_END.isoformat(),
        "extract_run_date": EXTRACT_END.isoformat(),
        "note": "Extracts contain IC receivable/payable postings from 2026-10-01 to extract_run_date. "
                "Postings after period_end belong to November.",
        "reconciliation_convention": "Compare in transaction currency when both sides match; otherwise "
                                     "convert both sides to USD at the seller's invoice-date daily rate.",
        "files": {k: {**v, "entity": k.replace("ledger_", "").rsplit(".", 1)[0],
                      "erp": ENTITIES[k.replace("ledger_", "").rsplit(".", 1)[0]]["erp"]}
                  for k, v in files.items()},
        "seed": a.seed,
    }
    (out / "raw" / "extract_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary = write_ground_truth(g, out / "ground_truth")
    print(json.dumps(summary, indent=2))
    print(f"\nWrote data to {out}")


if __name__ == "__main__":
    main()
