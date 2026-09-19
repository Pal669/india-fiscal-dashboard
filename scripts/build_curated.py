"""Rebuild data/curated.json from India_Fiscal_Framework_FY2526_v2.xlsx.

Why this is not a plain "cached values" export
  The workbook's *input* cells are sound, but many of its derived cells are broken (e.g. Total Receipts cached as
  -12,68,035, FY25 RE subsidies total 2,05,500, "Total Borrowings = Fiscal Deficit" showing 20,51,013). So this script
  reads only the hard-typed inputs, recomputes every derived figure itself, and runs a set of reconciliation checks
  that are published on the portal's "Data Notes" tab. Fix the workbook and re-run: the notes update themselves.

Run:  python scripts/build_curated.py [path-to-xlsx]
"""
import json, os, re, sys, warnings
from datetime import datetime
from pathlib import Path
import openpyxl

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLSX = Path(os.environ.get("FISCAL_XLSX", r"C:\Users\PC\OneDrive\EA Demo\Final Output\Economic Analysis\output\India_Fiscal_Framework_FY2526_v2.xlsx"))
YEARS = ["FY20-21", "FY21-22", "FY22-23", "FY23-24", "FY24-25 RE", "FY25-26 BE"]


# ---------- formatting ----------
def inr(n):
    """Indian digit grouping: 1568936 -> 15,68,936"""
    n = int(round(n)); s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + s


RATE = None      # INR per USD, set in main() from live data
RATE_DATE = ""


def usd(cr):
    """rupees crore -> US dollars (1 crore = 10,000,000 rupees)"""
    return cr * 1e7 / RATE


def money(cr):
    """rupees crore -> '$16.4 bn' / '$490 mn' (US$ billions and millions)"""
    u = usd(cr); a = abs(u); sign = "-" if u < 0 else ""
    if a >= 1e10:
        return f"{sign}${a / 1e9:,.1f} bn"
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f} bn"
    return f"{sign}${a / 1e6:,.0f} mn"


def pct(x, d=1):
    return f"{x * 100:.{d}f}%"


def lc(n):
    """rupees crore -> lakh crore, 2dp"""
    return round(n / 1e5, 2)


def parse_num(s):
    m = re.search(r"\d[\d,]*\.?\d*", str(s))
    return float(m.group(0).replace(",", "")) if m else None


# ---------- reading the workbook (inputs only) ----------
class Sheet:
    def __init__(self, ws):
        self.ws = ws

    def get(self, label, col, exact=False, cols=("A", "B")):
        for r in range(1, self.ws.max_row + 1):
            for lc_ in cols:
                t = self.ws[f"{lc_}{r}"].value
                if not isinstance(t, str):
                    continue
                t = t.strip()
                if (t == label) if exact else t.startswith(label):
                    v = self.ws[f"{col}{r}"].value
                    if isinstance(v, str) and v.startswith("="):
                        return None  # a formula: not an input
                    return v
        raise KeyError(f"label not found: {label!r} in {self.ws.title}")


def load(path):
    wb = openpyxl.load_workbook(path)  # formulas, not cached values
    ws = wb.worksheets
    return {k: Sheet(ws[i]) for i, k in enumerate(["dash", "rev", "cap", "exp", "sub", "int", "ind", "bor", "trend"])}, ws[0]


CHECKS = []


def check(sheet, what, expected, actual, tol=0.5, unit="₹ cr", fmt=money, note="", mode="eq"):
    """mode: eq = within tol; ge = actual must be >= expected; le = actual must be <= expected"""
    diff = None if (expected is None or actual is None) else actual - expected
    ok = diff is not None and (abs(diff) <= tol if mode == "eq" else diff >= 0 if mode == "ge" else diff <= 0)
    CHECKS.append(dict(sheet=sheet, what=what, expected=expected, actual=actual, diff=diff, ok=ok, unit=unit, fmt=fmt, note=note))


# ---------- table helpers ----------
def cell(x, kind="amt"):
    if x is None or x == "":
        return {"d": ""}
    if isinstance(x, str):
        return {"d": x}
    if kind == "pct":
        return {"d": pct(x), "v": x}
    if kind == "pct1":  # already in percent points
        return {"d": f"{x:.1f}%", "v": x}
    return {"d": money(x), "v": x}


def row(cells, cls=""):
    return {"cells": cells, "cls": cls}


def group(text):
    return {"group": True, "cells": [{"d": text}], "cls": "group"}


def table(header, rows):
    return {"t": "table", "header": header, "rows": rows}


def chart(type_, title, labels, series, **kw):
    return {"t": "chart", "type": type_, "title": title, "labels": labels,
            "series": [{"name": n, "data": d} for n, d in series], **kw}


AMT = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)\s*(lakh crore|lakh cr|crore|cr)(?![a-z])")


def _text(t):
    def sub(m):
        v = float(m.group(1).replace(",", ""))
        return money(v * 1e5 if m.group(2).startswith("lakh") else v)
    t = AMT.sub(sub, t)
    return (t.replace("₹ lakh crore", "US$ bn").replace("₹ lakh cr", "US$ bn").replace("₹ crore", "US$").replace("₹ cr", "US$"))


def _chart(c):
    """chart data arrives in Rs crore or Rs lakh crore (per its title); convert to US$ bn."""
    if c.get("unit") == "%":  # ratio charts are not money
        return
    lakh = "lakh" in c["title"]
    c["title"] = c["title"].replace("(₹ crore)", "(US$ bn)").replace("(₹ lakh crore)", "(US$ bn)")
    for sr in c["series"]:
        if "%" in sr["name"]:
            continue
        k = 1000 / RATE if lakh else 1 / (100 * RATE)
        sr["data"] = [None if v is None else round(v * k, 2) for v in sr["data"]]


def to_usd(node):
    if isinstance(node, str):
        return _text(node)
    if isinstance(node, list):
        return [to_usd(x) for x in node]
    if isinstance(node, dict):
        if node.get("t") == "chart":
            _chart(node)
        return {k: to_usd(v) for k, v in node.items()}
    return node


def build(path):
    S, dash_ws = load(path)
    g = lambda sh, label, col, **k: S[sh].get(label, col, **k)

    # ===== inputs =====
    rev = {k: g("rev", lab, "C") for k, lab in dict(corp="Corporation Tax", inc="Taxes on Income", stt="Securities Transaction Tax",
           gst="Goods and Services Tax", exc="Union Excise", cus="Customs Duty", svc="Service Tax", ut="UT Taxes",
           dev="Less: Devolution", div="Dividends", intr="Interest Receipts", comm="Communication", oth="Other Non-Tax",
           disinv="Disinvestment", recov="Recovery of Loans").items()}
    GDP = g("ind", "Nominal GDP (FY26 BE)", "C")
    RR = g("ind", "Total Revenue Receipts (A)", "C")
    NDCAP = g("ind", "Total Capital Receipts excl", "C")
    REVEXP = g("ind", "Revenue Expenditure (C)", "C")
    CAPEX = g("ind", "Capital Expenditure (D)", "C")
    INT = g("ind", "Interest Payments (E)", "C")
    debt_pct = parse_num(S["ind"].get("Outstanding Central Govt Liabilities", "B"))
    DEBT_PCT = debt_pct / 100

    ex_rev_items = [("Interest payments on debt", "Interest Payments on Debt"), ("Subsidies (total)", "Subsidies (Total"),
        ("Transfers to states (grants and loans)", "Transfer to States"), ("Salaries (central govt employees)", "Salaries"),
        ("Pension (civil and defence)", "Pension"), ("MGNREGS", "MGNREGS"), ("PM-KISAN", "PM-KISAN"), ("PM Awas Yojana", "PM Awas"),
        ("Jal Jeevan Mission", "Jal Jeevan"), ("Other revenue expenditure", "Other Revenue Expenditure")]
    ex_cap_items = [("Roads, highways and bridges", "Roads"), ("Railways", "Railways"), ("Defence capital", "Defence Capital"),
        ("Capital loans to states (50-yr)", "States Capital"), ("Urban infrastructure and housing", "Urban Infrastructure"),
        ("Water infrastructure", "Jal Shakti"), ("Telecom and digital", "Telecom"), ("Energy and power", "Energy & Power"), ("Other capital expenditure", "Other Capital Expenditure")]
    exp_rev = [(n, g("exp", lab, "B"), g("exp", lab, "C")) for n, lab in ex_rev_items]
    exp_cap = [(n, g("exp", lab, "B"), g("exp", lab, "C")) for n, lab in ex_cap_items]

    sub_items = [("Food", "Food subsidy (NFSA and welfare schemes)", "Food Subsidy"), ("Fertiliser", "Urea", "Urea Subsidy"),
        ("Fertiliser", "Non-urea (DAP, MOP, NPS)", "Non-Urea"), ("Petroleum", "LPG (Ujjwala)", "LPG Subsidy"),
        ("Interest subsidies", "Kisan Credit Card subvention", "Kisan Credit Card"), ("Interest subsidies", "CLSS housing", "CLSS Housing"),
        ("Interest subsidies", "Other interest subsidies", "Other Interest Subsidies"), ("Other", "Market intervention and agriculture", "Market Intervention"),
        ("Other", "Export and MSME", "Export & MSME"), ("Other", "Miscellaneous", "Other Miscellaneous")]
    subs = [(grp, n, g("sub", lab, "C"), g("sub", lab, "D")) for grp, n, lab in sub_items]
    SUB_STATED = g("exp", "Subsidies (Total", "B")
    SUB_STATED_RE = g("exp", "Subsidies (Total", "C")

    ip = {k: [g("int", lab, c, exact=True) for c in "BCDE"] for k, lab in dict(fd="Fiscal Deficit (₹ Crore)", pd="Primary Deficit (₹ Crore)",
          rr="Total Revenue Receipts (₹ Crore)", te="Total Expenditure (₹ Crore)", gdp="Nominal GDP (₹ Crore)").items()}
    ip_years = ["FY22-23 Actual", "FY23-24 Actual", "FY24-25 RE", "FY25-26 BE"]

    bor = {k: (g("bor", lab, "B"), g("bor", lab, "C")) for k, lab in dict(dated="Dated Government Securities", tb="Treasury Bills",
           nssf="Securities Against Small Savings", gpf="General Provident Fund", multi="Multilateral", bilat="Bilateral").items()}
    DEBT_STOCK = g("bor", "Outstanding Central Govt Debt", "B")
    cap = dict(other=g("cap", "Other Capital Receipts", "C"))

    T = {lab: [g("trend", lab, c, exact=True, cols=("A",)) for c in "BCDEFG"] for lab in
         ["Nominal GDP", "Total Revenue Receipts", "Total Expenditure", "Revenue Expenditure", "Capital Expenditure", "Fiscal Deficit",
          "Revenue Deficit", "Primary Deficit", "Interest Payments", "Total Subsidies", "Gross Tax Revenue", "Fiscal Deficit % GDP",
          "Revenue Deficit % GDP", "Primary Deficit % GDP", "Interest Payments % GDP", "Capital Expenditure % GDP", "Tax Revenue % GDP",
          "Corporation Tax", "Taxes on Income", "GST (Centre's Share)", "Customs Duty", "Union Excise Duties"]}

    # headline figures as typed on the workbook's own dashboard (used only for cross-checks and card text)
    d = {k: parse_num(dash_ws[c].value) for k, c in dict(te="C7", tr="E7", fd="G7", rd="A12", pd="C12", gtr="E12", capex="G12",
         int="A17", sub="C17", gross_bor="E17", disinv="G17").items()}

    # ===== derived (recomputed, never read from the workbook's formulas) =====
    gross_tax = sum(rev[k] for k in ("corp", "inc", "stt", "gst", "exc", "cus", "svc", "ut"))
    direct = rev["corp"] + rev["inc"] + rev["stt"]
    indirect = rev["gst"] + rev["exc"] + rev["cus"] + rev["svc"] + rev["ut"]
    net_tax = gross_tax + rev["dev"]  # dev is negative
    nontax = rev["div"] + rev["intr"] + rev["comm"] + rev["oth"]
    nd_cap = rev["disinv"] + rev["recov"]
    TR = RR + NDCAP
    TE = REVEXP + CAPEX
    FD = TE - TR
    RD = REVEXP - RR
    PD = FD - INT
    exp_rev_sum = sum(b for _, b, _ in exp_rev)
    exp_cap_sum = sum(b for _, b, _ in exp_cap)
    sub_sum, sub_sum_re = sum(s[2] for s in subs), sum(s[3] for s in subs)
    food, fert = subs[0][2], subs[1][2] + subs[2][2]
    net_dated, tbill_net = bor["dated"][1], bor["tb"][1]
    sav = bor["nssf"][1] + bor["gpf"][1]
    ext_net = bor["multi"][1] + bor["bilat"][1]
    fin_net = net_dated + tbill_net + sav + ext_net
    fin_gross = bor["dated"][0] + bor["tb"][0] + bor["nssf"][0] + bor["gpf"][0] + bor["multi"][0] + bor["bilat"][0]

    # ===== reconciliation checks =====
    check("Revenue", "Gross tax revenue: sum of listed taxes vs dashboard headline", d["gtr"], gross_tax)
    check("Revenue", "Net tax + non-tax (sum of listed items) vs total revenue receipts", RR, net_tax + nontax, tol=1,
          note="Listed revenue items overshoot revenue receipts; one or more revenue lines (likely UT taxes or a non-tax line) is not on the same basis.")
    check("Fiscal", "Total receipts excl. borrowings = revenue receipts + non-debt capital receipts vs dashboard", d["tr"], TR)
    check("Fiscal", "Fiscal deficit = total expenditure − total receipts vs dashboard", d["fd"], FD)
    check("Fiscal", "Revenue deficit = revenue expenditure − revenue receipts vs dashboard", d["rd"], RD, tol=1,
          note="Expenditure components (sheet 03) sum ₹90 cr above the level implied by the dashboard revenue deficit.")
    check("Fiscal", "Primary deficit = fiscal deficit − interest vs dashboard", d["pd"], PD)
    check("Expenditure", "Revenue expenditure components sum vs revenue expenditure used in Fiscal Indicators", REVEXP, exp_rev_sum)
    check("Expenditure", "Capital expenditure components sum vs capital expenditure headline", CAPEX, exp_cap_sum)
    check("Subsidies", "FY25-26 BE itemised subsidies vs stated total subsidies", SUB_STATED, sub_sum,
          note="Itemised lines do not add to the stated ₹4,26,216 cr; the portal shows the gap as an 'unitemised residual'.")
    check("Subsidies", "FY24-25 RE itemised subsidies vs stated total subsidies", SUB_STATED_RE, sub_sum_re,
          note="The workbook's own FY24-25 RE total (formula) reads ₹2,05,500 cr because it omits Food; itemised lines sum higher, but still not to the stated figure.")
    check("Subsidies", "Food + fertiliser share of subsidies (workbook claims 87%)", 0.87, (food + fert) / SUB_STATED, tol=0.005, unit="%", fmt=lambda x: pct(x))
    check("Financing", "Net financing sources (net dated + T-bills + small savings/GPF + external net) vs fiscal deficit", FD, fin_net, tol=1,
          note="The workbook labels this total as 'Total Borrowings = Fiscal Deficit' but its sheet-07 formula adds gross and net columns together (₹20,51,013 cr).")
    check("Financing", "Workbook claim: 'fiscal deficit funded 90% by market borrowings' (gross dated G-secs ÷ FD)", 0.90, bor["dated"][0] / FD, tol=0.005, unit="%", fmt=lambda x: pct(x),
          note=f"Net dated G-secs ÷ FD = {pct(net_dated / FD)}; net dated + T-bills ÷ FD = {pct((net_dated + tbill_net) / FD)}. 90% is not reproducible on a net basis.")
    check("Financing", "Bilateral external borrowing: net ≤ gross", bor["bilat"][0], bor["bilat"][1], mode="le",
          note="Net receipts (₹32,391 cr) exceed gross (₹31,625 cr), which is not possible for a net figure.")
    ie = [ip["fd"][i] - ip["pd"][i] for i in range(4)]
    check("Interest", "FY25-26 BE interest (FD − PD) vs interest in Expenditure sheet", INT, ie[3])
    check("Interest", "FY24-25 RE interest (FD − PD) vs FY24-25 RE interest in Trends sheet", T["Interest Payments"][4], ie[2])
    gdp_now, gdp_prev = T["Nominal GDP"][5], T["Nominal GDP"][4]
    check("Trends", "Nominal GDP must rise year on year: FY23-24 → FY24-25 RE", T["Nominal GDP"][3], gdp_prev, mode="ge",
          note="Nominal GDP falls from ₹3,29,37,000 cr to ₹3,26,36,000 cr. Nominal GDP does not fall in a growth year, so the GDP series (and every ratio built on it) looks misaligned across years.")
    for i, y in enumerate(YEARS):
        tot = T["Total Expenditure"][i]
        s = T["Revenue Expenditure"][i] + T["Capital Expenditure"][i]
        check("Trends", f"{y}: revenue + capital expenditure vs total expenditure", tot, s, tol=1000,
              note="Tolerance ₹1,000 cr (workbook rounds to thousands)." if i == 0 else "")
    for i, y in enumerate(YEARS):
        s = T["Fiscal Deficit"][i] - T["Interest Payments"][i]
        check("Trends", f"{y}: fiscal deficit − interest vs primary deficit", T["Primary Deficit"][i], s, tol=1000)
    for i, y in enumerate(YEARS):
        stated = T["Fiscal Deficit % GDP"][i]
        calc = T["Fiscal Deficit"][i] / T["Nominal GDP"][i] * 100
        check("Trends", f"{y}: fiscal deficit % of GDP, typed vs recomputed from levels", stated, calc, tol=0.15, unit="%", fmt=lambda x: f"{x:.2f}%")
    check("Fiscal", "Total expenditure typed on FY24-25 RE (Expenditure sheet C28 = 47,161,889) vs Interest/Trends sheets", 4716000, 47161889, tol=1000,
          note="A stray digit: the FY24-25 RE total is typed as 47,161,889, ten times too large; every other sheet has ₹47,16,000 cr. The portal uses ₹47,16,000 cr.")

    # ===== blocks =====
    meta_src = "Union Budget 2025-26 (BE), PRS India, indiabudget.gov.in, as compiled in the source workbook"
    sheets = []

    # -- Overview
    exp_growth = TE / T["Total Expenditure"][4] - 1
    cards = [
        ["Nominal GDP", f"₹{GDP / 1e5:.1f} lakh crore", "FY26 BE"],
        ["Total expenditure", f"₹{inr(TE)} cr", f"{exp_growth * 100:.1f}% above FY25 RE"],
        ["Total receipts (excl. borrowings)", f"₹{inr(TR)} cr", f"{pct(TR / GDP)} of GDP"],
        ["Fiscal deficit", f"₹{inr(FD)} cr", f"{pct(FD / GDP)} of GDP"],
        ["Revenue deficit", f"₹{inr(d['rd'])} cr", f"{pct(d['rd'] / GDP)} of GDP"],
        ["Primary deficit", f"₹{inr(PD)} cr", f"{pct(PD / GDP)} of GDP"],
        ["Gross tax revenue", f"₹{inr(gross_tax)} cr", f"{gross_tax / T['Gross Tax Revenue'][4] * 100 - 100:+.1f}% vs FY25 RE"],
        ["Capital expenditure", f"₹{inr(CAPEX)} cr", f"{pct(CAPEX / GDP)} of GDP"],
        ["Interest payments", f"₹{inr(INT)} cr", f"{pct(INT / TE)} of expenditure · {pct(INT / RR)} of revenue receipts"],
        ["Total subsidies", f"₹{inr(SUB_STATED)} cr", f"Food + fertiliser = {pct((food + fert) / SUB_STATED, 0)}"],
        ["Gross dated borrowing", f"₹{inr(bor['dated'][0])} cr", f"Net ₹{inr(net_dated)} cr"],
        ["Disinvestment target", f"₹{inr(rev['disinv'])} cr", "Miscellaneous capital receipts"],
    ]
    ov = [{"t": "title", "text": "India: Union Budget Fiscal Framework, FY2025-26"},
          {"t": "subtitle", "text": f"Budget Estimates (BE), ₹ crore unless stated. {meta_src}."},
          {"t": "cards", "cards": cards},
          {"t": "section", "text": "The consolidation path"},
          chart("line", "Deficits as % of GDP (workbook series)", YEARS, [(n, T[k]) for n, k in
                [("Fiscal deficit", "Fiscal Deficit % GDP"), ("Revenue deficit", "Revenue Deficit % GDP"), ("Primary deficit", "Primary Deficit % GDP")]], unit="%"),
          {"t": "section", "text": "Where each rupee of spending goes (FY26 BE)"},
          chart("doughnut", "Total expenditure by head (₹ crore)", [n for n, _, _ in exp_rev] + ["Capital expenditure"], [("BE", [b for _, b, _ in exp_rev] + [CAPEX])]),
          {"t": "section", "text": "Key insights"},
          {"t": "note", "text": f"Interest is the single biggest item: ₹{inr(INT)} cr, {pct(INT / TE)} of all spending and {pct(INT / RR)} of revenue receipts."},
          {"t": "note", "text": f"Fiscal deficit of {pct(FD / GDP)} of GDP is down from {T['Fiscal Deficit % GDP'][0]:.1f}% in FY20-21 (workbook series). Primary deficit is {pct(PD / GDP)}, so most of the remaining gap is interest."},
          {"t": "note", "text": f"Capital expenditure is ₹{inr(CAPEX)} cr ({pct(CAPEX / GDP)} of GDP) against ₹{inr(T['Capital Expenditure'][4])} cr in FY24-25 RE: {CAPEX / T['Capital Expenditure'][4] * 100 - 100:+.1f}%."},
          {"t": "note", "text": f"Food and fertiliser are {pct((food + fert) / SUB_STATED, 0)} of the ₹{inr(SUB_STATED)} cr subsidy bill."},
          {"t": "banner", "kind": "warn", "text": f"Data check: {sum(1 for c in CHECKS if not c['ok'])} of {len(CHECKS)} reconciliation checks on the source workbook are flagged. Headline deficit figures reconcile; some sub-tables and the historical series do not. See the Data Notes tab before quoting any figure externally."}]
    sheets.append({"name": "Overview", "blocks": ov})

    # -- Revenue
    R = lambda label, amt, base, note="", cls="": row([{"d": label}, cell(amt), cell(amt / gross_tax, "pct") if base == "g" else cell(amt / RR, "pct"), {"d": note}], cls)
    rows = [group("A. Tax revenue"), group("Direct taxes"),
            R("Corporation tax", rev["corp"], "g", "Tax on company profits"), R("Taxes on income (personal and other)", rev["inc"], "g", "Personal income tax; new regime: zero tax up to ₹12 lakh"),
            R("Securities transaction tax and other", rev["stt"], "g", "STT on equity transactions, estate duty residuals"), R("Direct taxes sub-total", direct, "g", "", "sub"),
            group("Indirect taxes"), R("GST (CGST + cess)", rev["gst"], "g", "CGST ₹10,10,890 cr + compensation cess ₹1,67,110 cr"),
            R("Union excise duties", rev["exc"], "g", "Mainly petroleum products and tobacco"), R("Customs duty", rev["cus"], "g", "Import duties on goods"),
            R("Service tax (residual arrears)", rev["svc"], "g", "Legacy arrears after GST (July 2017)"), R("UT taxes", rev["ut"], "g", "Taxes levied in Union Territories"),
            R("Indirect taxes sub-total", indirect, "g", "", "sub"), R("Gross tax revenue (Centre + States)", gross_tax, "g", "Sum of the eight lines above", "total"),
            R("Less: devolution to States", rev["dev"], "r", "41% of divisible pool per Finance Commission formula"), R("Net tax revenue (Centre)", net_tax, "r", "", "total"),
            group("B. Non-tax revenue"), R("Dividends: RBI, banks and PSUs", rev["div"], "r", "RBI surplus transfer ~₹2.33 lakh cr plus CPSE dividends"),
            R("Interest receipts on loans", rev["intr"], "r", "Interest on Centre's loans to states and PSUs"), R("Communication services", rev["comm"], "r", "Spectrum auctions and telecom licence fees"),
            R("Other non-tax revenue", rev["oth"], "r", "Fees, user charges, external grants, misc receipts"), R("Non-tax revenue total", nontax, "r", f"Dividends are {pct(rev['div'] / nontax)} of non-tax revenue", "total"),
            group("C. Capital receipts (excl. borrowings)"), R("Disinvestment of PSUs", rev["disinv"], "r", "Target cut for the 5th year running; historically unmet"),
            R("Recovery of loans from states and PSUs", rev["recov"], "r", "Repayment of earlier Centre loans"), R("Non-debt capital receipts total", nd_cap, "r", "", "total"),
            group("Reconciliation"), R("Total revenue receipts (as used in the deficit arithmetic)", RR, "r", f"Net tax + non-tax as listed = ₹{inr(net_tax + nontax)} cr; gap ₹{inr(net_tax + nontax - RR)} cr (see Data Notes)", "sub"),
            R("Total receipts excl. borrowings", TR, "r", "Revenue receipts + non-debt capital receipts", "total")]
    sheets.append({"name": "Revenue", "blocks": [
        {"t": "title", "text": "Government revenue sources"}, {"t": "subtitle", "text": "FY2025-26 BE, ₹ crore. Source: Receipt Budget 2025-26, indiabudget.gov.in. Percent columns are recomputed."},
        {"t": "cards", "cards": [["Gross tax revenue", f"₹{inr(gross_tax)} cr", f"{pct(gross_tax / GDP)} of GDP"], ["Net tax revenue", f"₹{inr(net_tax)} cr", "After devolution to States"],
                                 ["Non-tax revenue", f"₹{inr(nontax)} cr", f"Dividends {pct(rev['div'] / nontax, 0)}"], ["Direct : indirect", f"{pct(direct / gross_tax, 0)} : {pct(indirect / gross_tax, 0)}", "Share of gross tax"]]},
        chart("doughnut", "Gross tax revenue by source (₹ crore)", ["Corporation tax", "Income tax", "GST", "Union excise", "Customs", "STT, service tax, UT taxes"],
              [("BE", [rev["corp"], rev["inc"], rev["gst"], rev["exc"], rev["cus"], rev["stt"] + rev["svc"] + rev["ut"]])]),
        table(["Category", "Amount (₹ cr)", "% of base", "Notes"], rows),
        {"t": "note", "text": "% of base: tax lines are shown as a share of gross tax revenue; devolution, non-tax and capital receipts as a share of total revenue receipts."}]})

    # -- Expenditure
    def er(n, b, r_, cls=""):
        return row([{"d": n}, cell(b), cell(r_) if r_ is not None else {"d": ""}, cell(b / TE, "pct"), cell(b / r_ - 1, "pct") if r_ else {"d": ""}], cls)
    rows = [group("A. Revenue expenditure (recurring)")] + [er(n, b, r_) for n, b, r_ in exp_rev] + [er("Revenue expenditure total", exp_rev_sum, None, "sub"),
            group("B. Capital expenditure (asset creation)")] + [er(n, b, r_) for n, b, r_ in exp_cap] + [er("Capital expenditure total", exp_cap_sum, T["Capital Expenditure"][4], "sub"),
            er("Total expenditure (revenue + capital)", TE, T["Total Expenditure"][4], "total")]
    sheets.append({"name": "Expenditure", "blocks": [
        {"t": "title", "text": "Government expenditure"}, {"t": "subtitle", "text": "FY2025-26 BE vs FY2024-25 RE, ₹ crore. Source: Expenditure Budget 2025-26. Shares and growth are recomputed."},
        {"t": "cards", "cards": [["Total expenditure", f"₹{inr(TE)} cr", f"{exp_growth * 100:.1f}% above FY25 RE"], ["Revenue expenditure", f"₹{inr(REVEXP)} cr", f"{pct(REVEXP / TE, 0)} of total"],
                                 ["Capital expenditure", f"₹{inr(CAPEX)} cr", f"{pct(CAPEX / TE, 0)} of total"], ["Interest", f"₹{inr(INT)} cr", f"{pct(INT / TE)} of total"]]},
        chart("bar", "Revenue expenditure by head, FY26 BE (₹ crore)", [n for n, _, _ in exp_rev], [("FY25-26 BE", [b for _, b, _ in exp_rev])], horizontal=True),
        chart("bar", "Capital expenditure by head: BE vs prior-year RE (₹ crore)", [n for n, _, _ in exp_cap if n != "Other capital expenditure"],
              [("FY25-26 BE", [b for n, b, _ in exp_cap if n != "Other capital expenditure"]), ("FY24-25 RE", [r_ for n, _, r_ in exp_cap if n != "Other capital expenditure"])]),
        table(["Category", "FY25-26 BE (₹ cr)", "FY24-25 RE (₹ cr)", "% of total exp", "Growth vs RE"], rows),
        {"t": "note", "text": "FY24-25 RE is blank where the workbook has no prior-year figure. Total FY24-25 RE expenditure uses ₹47,16,000 cr (Interest and Trends sheets); the Expenditure sheet's own typed value has a stray digit."}]})

    # -- Subsidies
    grp_tot = {}
    for gname, _, be, re_ in subs:
        a = grp_tot.setdefault(gname, [0, 0]); a[0] += be; a[1] += re_
    resid = SUB_STATED - sub_sum
    rows, cur = [], None
    for gname, n, be, re_ in subs:
        if gname != cur:
            cur = gname; rows.append(group(f"{gname.upper()}   (BE ₹{inr(grp_tot[gname][0])} cr · RE ₹{inr(grp_tot[gname][1])} cr)"))
        rows.append(row([{"d": n}, cell(be), cell(re_), cell(be / SUB_STATED, "pct"), cell(be / re_ - 1, "pct")]))
    rows += [row([{"d": "Unitemised residual (stated total − itemised)"}, cell(resid), cell(SUB_STATED_RE - sub_sum_re), cell(resid / SUB_STATED, "pct"), {"d": ""}], "note"),
             row([{"d": "Total subsidies (as stated)"}, cell(SUB_STATED), cell(SUB_STATED_RE), cell(1.0, "pct"), cell(SUB_STATED / SUB_STATED_RE - 1, "pct")], "total")]
    sheets.append({"name": "Subsidies", "blocks": [
        {"t": "title", "text": "Subsidy breakdown"}, {"t": "subtitle", "text": "FY2025-26 BE vs FY2024-25 RE, ₹ crore. Source: Expenditure Profile 2025-26, Statement 7."},
        {"t": "cards", "cards": [["Total subsidies", f"₹{inr(SUB_STATED)} cr", f"{pct(SUB_STATED / TE)} of expenditure"], ["Food", f"₹{inr(food)} cr", f"{pct(food / SUB_STATED, 0)} of bill"],
                                 ["Fertiliser", f"₹{inr(fert)} cr", f"{pct(fert / SUB_STATED, 0)} of bill"], ["Petroleum (LPG)", f"₹{inr(grp_tot['Petroleum'][0])} cr", f"{grp_tot['Petroleum'][0] / grp_tot['Petroleum'][1] * 100 - 100:+.1f}% vs RE"]]},
        chart("doughnut", "Subsidy bill by group, FY26 BE (₹ crore)", list(grp_tot) + ["Unitemised residual"], [("BE", [v[0] for v in grp_tot.values()] + [resid])]),
        table(["Subsidy", "FY25-26 BE (₹ cr)", "FY24-25 RE (₹ cr)", "% of stated total", "Growth vs RE"], rows),
        {"t": "note", "text": "Food and fertiliser share is computed on the stated total (₹4,26,216 cr). Itemised lines sum to ₹4,21,103 cr; the difference is shown as a residual rather than hidden."}]})

    # -- Interest
    def ir(label, vals, kind="amt", cls="", note=""):
        return row([{"d": label}] + [cell(v, kind) for v in vals] + [{"d": note}], cls)
    ie_pct_te = [ie[i] / ip["te"][i] for i in range(4)]
    ie_pct_rr = [ie[i] / ip["rr"][i] for i in range(4)]
    ie_pct_gdp = [ie[i] / ip["gdp"][i] for i in range(4)]
    rows = [ir("Fiscal deficit", ip["fd"]), ir("Primary deficit", ip["pd"]), ir("Interest payments (= FD − PD)", ie, cls="total", note="Largest single revenue-expenditure item"),
            ir("Total revenue receipts", ip["rr"]), ir("Total expenditure", ip["te"]), ir("Nominal GDP", ip["gdp"]),
            ir("Interest as % of total expenditure", ie_pct_te, "pct", "sub", "Target: below 20% over the consolidation path"),
            ir("Interest as % of revenue receipts", ie_pct_rr, "pct", "sub", "Over 37% means more than ₹1 of every ₹3 of revenue goes to interest"),
            ir("Interest as % of GDP", ie_pct_gdp, "pct", "sub"), ir("Fiscal deficit as % of GDP", [ip["fd"][i] / ip["gdp"][i] for i in range(4)], "pct", "sub"),
            ir("Primary deficit as % of GDP", [ip["pd"][i] / ip["gdp"][i] for i in range(4)], "pct", "sub")]
    sheets.append({"name": "Interest", "blocks": [
        {"t": "title", "text": "Interest payments"}, {"t": "subtitle", "text": "Four-year view, ₹ crore. Source: Budget at a Glance 2025-26. All ratios recomputed from the levels shown."},
        {"t": "cards", "cards": [["Interest, FY26 BE", f"₹{inr(ie[3])} cr", f"{ie[3] / ie[2] * 100 - 100:+.1f}% vs FY25 RE"], ["% of expenditure", pct(ie_pct_te[3]), f"{ie_pct_te[3] * 100 - ie_pct_te[0] * 100:+.1f} pp since FY22-23"],
                                 ["% of revenue receipts", pct(ie_pct_rr[3]), f"{ie_pct_rr[3] * 100 - ie_pct_rr[0] * 100:+.1f} pp since FY22-23"], ["% of GDP", pct(ie_pct_gdp[3]), ""]]},
        chart("combo", "Interest payments (₹ lakh crore, bars) and share of revenue receipts (%, line)", ip_years, [("Interest (₹ lakh cr)", [lc(x) for x in ie]), ("% of revenue receipts", [round(x * 100, 1) for x in ie_pct_rr])], dual=True),
        table(["Indicator"] + ip_years + ["Observation"], rows)]})

    # -- Deficits & debt
    def dr(label, formula, amt, cls="", note=""):
        return row([{"d": label}, {"d": formula}, cell(amt), cell(amt / GDP, "pct"), {"d": note}], cls)
    debt_amt = DEBT_PCT * GDP
    rows = [group("Key assumptions, FY2025-26 BE"), dr("Nominal GDP", "Government estimate, 10.1% nominal growth", GDP, "", "Denominator for every ratio"),
            dr("Total revenue receipts (A)", "Net tax + non-tax revenue", RR), dr("Non-debt capital receipts (B)", "Disinvestment + loan recoveries", NDCAP),
            dr("Total receipts excl. borrowings (A+B)", "Self-generated resources", TR, "sub"), dr("Revenue expenditure (C)", "Interest + subsidies + salaries + grants", REVEXP),
            dr("Capital expenditure (D)", "Infrastructure, defence, railways", CAPEX), dr("Total expenditure (C+D)", "Revenue + capital", TE, "sub"),
            dr("Interest payments (E)", "Fiscal deficit − primary deficit", INT),
            group("Deficits"), dr("Fiscal deficit", "Total expenditure − total receipts excl. borrowings", FD, "total", "Financed entirely by borrowing"),
            dr("Revenue deficit", "Revenue expenditure − revenue receipts", d["rd"], "total", "Current spending financed by borrowing (headline as stated on the workbook dashboard)"),
            dr("Primary deficit", "Fiscal deficit − interest payments", PD, "total", "Would be zero if interest were nil"),
            group("Debt"), dr("Outstanding central government liabilities", f"~{debt_pct:.1f}% of GDP (as estimated)", debt_amt, "", "Government targets 50% of GDP by March 2031"),
            dr("Gross dated-securities borrowing", "Dated G-secs issued in FY26", bor["dated"][0]), dr("Net dated-securities borrowing", "Gross − redemptions", net_dated)]
    sheets.append({"name": "Deficits & Debt", "blocks": [
        {"t": "title", "text": "Deficits and debt"}, {"t": "subtitle", "text": "FY2025-26 BE, ₹ crore. Formula-based reconstruction from Budget documents; % of GDP recomputed."},
        {"t": "cards", "cards": [["Fiscal deficit", pct(FD / GDP), f"₹{inr(FD)} cr"], ["Revenue deficit", pct(d['rd'] / GDP), f"₹{inr(d['rd'])} cr"], ["Primary deficit", pct(PD / GDP), f"₹{inr(PD)} cr"], ["Central govt liabilities", f"~{debt_pct:.1f}% of GDP", f"≈ ₹{debt_amt / 1e5:.0f} lakh crore"]]},
        chart("bar", "How the fiscal deficit is built (₹ lakh crore)", ["Total expenditure", "Total receipts", "Fiscal deficit", "Revenue deficit", "Primary deficit", "Interest"], [("FY26 BE", [lc(TE), lc(TR), lc(FD), lc(d["rd"]), lc(PD), lc(INT)])]),
        table(["Indicator", "Formula / definition", "Amount (₹ cr)", "% of GDP", "Note"], rows),
        {"t": "note", "text": "The workbook's own '% of GDP' cells for the debt block point at the wrong rows; they are recomputed here."}]})

    # -- Financing
    fr = lambda label, gross, net, note="", cls="": row([{"d": label}, cell(gross), cell(net), cell(net / FD, "pct") if net is not None else {"d": ""}, {"d": note}], cls)
    rows = [group("Domestic market borrowings"), fr("Dated government securities", bor["dated"][0], net_dated, "10, 30 and 40-year bonds; bulk of deficit financing"),
            fr("Treasury bills (91/182/364-day)", bor["tb"][0], tbill_net, "Short-term cash management"), fr("Market borrowings total", bor["dated"][0] + bor["tb"][0], net_dated + tbill_net, "", "sub"),
            group("Small savings and provident funds"), fr("Securities against small savings (NSSF)", bor["nssf"][0], bor["nssf"][1], "PPF, NSC, Sukanya Samriddhi, post-office schemes"),
            fr("GPF and others", bor["gpf"][0], bor["gpf"][1], "Central government employees' GPF"), fr("Small savings sub-total", bor["nssf"][0] + bor["gpf"][0], sav, "", "sub"),
            group("External borrowings"), fr("Multilateral (World Bank, ADB, NDB, AIIB)", bor["multi"][0], bor["multi"][1], "Concessional"),
            fr("Bilateral (Japan, Germany, France, Russia, EIB)", bor["bilat"][0], bor["bilat"][1], "Workbook shows net above gross; see Data Notes"), fr("External sub-total", bor["multi"][0] + bor["bilat"][0], ext_net, "", "sub"),
            group("Reconciliation"), fr("Sum of listed sources", fin_gross, fin_net, "", "total"), fr("Fiscal deficit to be financed", None, FD, "", "total"),
            fr("Unreconciled gap (listed sources − fiscal deficit)", None, fin_net - FD, "Cash-balance drawdown or other items not itemised in the workbook", "note")]
    sheets.append({"name": "Financing", "blocks": [
        {"t": "title", "text": "Capital receipts and borrowing structure"}, {"t": "subtitle", "text": "FY2025-26 BE, ₹ crore. Source: Receipt Budget 2025-26, RBI borrowing calendar."},
        {"t": "cards", "cards": [["Gross dated borrowing", f"₹{inr(bor['dated'][0])} cr", "RBI-run auctions"], ["Net dated borrowing", f"₹{inr(net_dated)} cr", f"{pct(net_dated / FD, 0)} of fiscal deficit"],
                                 ["Small savings + GPF", f"₹{inr(sav)} cr", f"{pct(sav / FD, 0)} of fiscal deficit"], ["External (net)", f"₹{inr(ext_net)} cr", f"{pct(ext_net / FD, 0)} of fiscal deficit"]]},
        chart("bar", "Financing mix, net (₹ crore)", ["Net dated G-secs", "Treasury bills", "Small savings and GPF", "External (net)", "Fiscal deficit"], [("FY26 BE", [net_dated, tbill_net, sav, ext_net, FD])]),
        {"t": "banner", "kind": "warn", "text": f"Listed net sources add to ₹{inr(fin_net)} cr against a fiscal deficit of ₹{inr(FD)} cr. The workbook's own total ('= Fiscal Deficit') is a formula error. The portal shows the gap instead of forcing a match."},
        table(["Instrument", "Gross (₹ cr)", "Net (₹ cr)", "Net % of fiscal deficit", "Notes"], rows),
        {"t": "subhead", "text": "Non-debt capital receipts and memo items"},
        table(["Item", "Amount", "Notes"], [row([{"d": "Disinvestment / miscellaneous capital receipts"}, cell(rev["disinv"]), {"d": "PSU stake sales; target reduced for the 5th consecutive year"}]),
              row([{"d": "Recovery of loans"}, cell(rev["recov"]), {"d": "Repayments from states and PSUs"}]),
              row([{"d": "Other capital receipts (PF, NSSF etc.), sheet 02"}, cell(cap["other"]), {"d": "Not the same as GPF ₹25,000 cr in the borrowing sheet"}]),
              row([{"d": "Outstanding central government debt (est. FY26)"}, cell(DEBT_STOCK), {"d": f"~{debt_pct:.1f}% of GDP; target 50% by FY31"}]),
              row([{"d": "Weighted-average G-sec yield (approx.)"}, {"d": "~7.1%"}, {"d": "As at February 2025 per workbook; see Live Data for current market indicators"}])])]})

    # -- Trends
    lakh = lambda k: [lc(x) for x in T[k]]
    def tr(label, key, kind="amt", note=""):
        vals = T[key]
        chg = (vals[5] / vals[0] - 1) if kind == "amt" else (vals[5] - vals[0])
        last = {"d": f"{chg * 100:+.0f}%", "v": chg} if kind == "amt" else {"d": f"{chg:+.1f} pp", "v": chg}
        return row([{"d": label}] + [cell(v, kind if kind == "amt" else "pct1") for v in vals] + [last])
    lv = ["Nominal GDP", "Total Revenue Receipts", "Total Expenditure", "Revenue Expenditure", "Capital Expenditure", "Fiscal Deficit", "Revenue Deficit", "Primary Deficit", "Interest Payments", "Total Subsidies", "Gross Tax Revenue"]
    pc = ["Fiscal Deficit % GDP", "Revenue Deficit % GDP", "Primary Deficit % GDP", "Interest Payments % GDP", "Capital Expenditure % GDP", "Tax Revenue % GDP"]
    tx = ["Corporation Tax", "Taxes on Income", "GST (Centre's Share)", "Customs Duty", "Union Excise Duties"]
    sheets.append({"name": "Trends", "blocks": [
        {"t": "title", "text": "Six-year fiscal trends"}, {"t": "subtitle", "text": "FY2020-21 to FY2025-26, ₹ crore. Source: Budget at a Glance (various years), PRS India, CGA. Values as typed in the workbook."},
        {"t": "banner", "kind": "warn", "text": "Several historical series in the source workbook do not reconcile with each other (revenue + capital expenditure ≠ total expenditure in five of six years; nominal GDP dips in FY24-25 RE). Treat this tab as directional until checked against Budget at a Glance. Details on the Data Notes tab."},
        chart("line", "Deficits as % of GDP", YEARS, [(n, T[k]) for n, k in [("Fiscal", "Fiscal Deficit % GDP"), ("Revenue", "Revenue Deficit % GDP"), ("Primary", "Primary Deficit % GDP")]], unit="%"),
        {"t": "grid", "charts": [
            chart("line", "Interest, capex and tax as % of GDP", YEARS, [(n, T[k]) for n, k in [("Interest", "Interest Payments % GDP"), ("Capex", "Capital Expenditure % GDP"), ("Tax revenue", "Tax Revenue % GDP")]], unit="%"),
            chart("bar", "Revenue receipts vs total expenditure (₹ lakh crore)", YEARS, [("Revenue receipts", lakh("Total Revenue Receipts")), ("Total expenditure", lakh("Total Expenditure"))]),
            chart("bar", "Capex vs interest (₹ lakh crore)", YEARS, [("Capital expenditure", lakh("Capital Expenditure")), ("Interest payments", lakh("Interest Payments"))]),
            chart("bar", "Tax revenue by source (₹ lakh crore)", YEARS, [(k, lakh(k)) for k in tx], stacked=True)]},
        {"t": "section", "text": "Levels (₹ crore)"}, table(["Indicator"] + YEARS + ["Change FY21→FY26"], [tr(k, k) for k in lv]),
        {"t": "section", "text": "As % of GDP"}, table(["Indicator"] + YEARS + ["Change (pp)"], [tr(k, k, "pct1") for k in pc]),
        {"t": "section", "text": "Tax revenue breakdown (₹ crore)"}, table(["Tax"] + YEARS + ["Change FY21→FY26"], [tr(k, k) for k in tx])]})

    # -- Data notes
    bad = [c for c in CHECKS if not c["ok"]]
    def fm(c, v):
        return "" if v is None else c["fmt"](v)
    def dfm(c):
        if c["diff"] is None:
            return ""
        return (f"{c['diff'] * 100:+.1f} pp" if c["unit"] == "%" and c["fmt"] is not money and abs(c["expected"]) <= 1.5 else
                f"{c['diff']:+.2f} pp" if c["unit"] == "%" else ("+" if c["diff"] > 0 else "") + money(c["diff"]))
    rows = []
    for c in sorted(CHECKS, key=lambda c: (c["ok"], c["sheet"])):
        rows.append(row([{"d": "OK" if c["ok"] else "FLAG"}, {"d": c["sheet"]}, {"d": c["what"]}, {"d": fm(c, c["expected"])}, {"d": fm(c, c["actual"])}, {"d": dfm(c)}, {"d": c["note"] if not c["ok"] else ""}], "" if c["ok"] else "sub"))
    sheets.append({"name": "Data Notes", "blocks": [
        {"t": "title", "text": "Data notes and reconciliation"},
        {"t": "subtitle", "text": f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from {Path(path).name}. Every figure on this portal is rebuilt from the workbook's typed inputs, not from its cached formula results."},
        {"t": "cards", "cards": [["Checks run", str(len(CHECKS)), "Automatic, on every rebuild"], ["Passed", str(len(CHECKS) - len(bad)), "Reconcile within tolerance"], ["Flagged", str(len(bad)), "Source workbook inconsistencies"]]},
        {"t": "banner", "kind": "warn", "text": "What this means: the headline deficit arithmetic (expenditure, receipts, fiscal, revenue and primary deficit, interest) reconciles. The flagged items are inside sub-tables, the financing table and the six-year history. Verify those against Budget at a Glance before quoting them to a client."},
        {"t": "subhead", "text": "Expected = what the other figures in the workbook imply. Workbook = what the workbook's own inputs give."},
        table(["Status", "Area", "Check", "Expected", "Workbook", "Difference", "Comment"], rows),
        {"t": "note", "text": "Not in the workbook: Union Budget 2026-27 (FY27 BE, FY26 RE) and FY26 provisional actuals. The portal's headline year is FY2025-26 BE until those are added."}]})

    fx_note = (f"All rupee figures are converted to US dollars (billions and millions) at ₹{RATE:.2f} per US$ (Yahoo Finance, {RATE_DATE}). "
               "One current rate is applied to every year, so US$ trends across years ignore how the rupee moved over time (it is weaker now than in FY21), "
               "which understates US$ growth against a historical-rate conversion. Percent-of-GDP and other ratios are unaffected.")
    for sh in sheets:
        sh["blocks"] = to_usd(sh["blocks"])
    sheets[0]["blocks"].insert(2, {"t": "banner", "kind": "", "text": fx_note})
    return {"fx": {"inr_per_usd": round(RATE, 4), "as_of": RATE_DATE}, "source_file": Path(path).name, "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "checks_total": len(CHECKS), "checks_flagged": len(bad), "sheets": sheets}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    global RATE, RATE_DATE
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    if os.environ.get("FISCAL_USDINR"):
        RATE, RATE_DATE = float(os.environ["FISCAL_USDINR"]), "fixed by FISCAL_USDINR"
    else:
        m = json.loads((ROOT / "data" / "live.json").read_text(encoding="utf-8"))["market"]["series"]["INR=X"]
        RATE, RATE_DATE = m["last"], m["as_of"]
    out = build(src)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "curated.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"curated.json built from {src.name}: {len(out['sheets'])} tabs, {out['checks_total']} checks, {out['checks_flagged']} flagged")
    for c in CHECKS:
        if not c["ok"]:
            print(f"  FLAG [{c['sheet']}] {c['what']}")


if __name__ == "__main__":
    main()
