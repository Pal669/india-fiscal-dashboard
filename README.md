# India Fiscal Dashboard

Static site (GitHub Pages) with two data layers.

| Layer | What | How it updates |
|---|---|---|
| Budget tabs (Overview, Revenue, Expenditure, Subsidies, Interest, Deficits & Debt, Financing, Trends) | `India_Fiscal_Framework_FY2526_v2.xlsx`, rebuilt from its typed inputs | Manual trigger: `python scripts/update_all.py --push` |
| Live Data tab | World Bank fiscal/macro series, USD/INR and Brent (Yahoo Finance), RBI press-release watcher | Automatic daily by GitHub Actions, or on demand (Actions tab > Refresh live data > Run workflow), or `python scripts/update_all.py --live --push` |
| Data Notes tab | 36 reconciliation checks on the workbook | Regenerated on every budget rebuild |

## Why the budget layer is rebuilt, not copied
Many derived cells in the workbook are broken (Total Receipts cached as -12,68,035; "Total Borrowings = Fiscal Deficit"
showing 20,51,013; etc.). `build_curated.py` reads only typed inputs, recomputes everything, and lists every mismatch on the
Data Notes tab. Fix the workbook, re-run, and the flags clear on their own.

## Commands
    python scripts/update_all.py                 # rebuild budget data + pull live data, locally
    python scripts/update_all.py --push          # ...and publish (commit + push)
    python scripts/update_all.py --live --push   # live data only
    python scripts/update_all.py --xlsx "path\to\new_workbook.xlsx"
    python -m http.server 8000                   # preview at http://localhost:8000

Setting `FISCAL_XLSX` changes the default workbook path. To move to a new budget year, point at the new workbook; the
label lookups in `build_curated.py` are by row text, so a re-laid-out workbook may need those labels updated.

## Currency
Default is rupees (crore / lakh, Indian digit grouping). For US$ billions/millions run
`python scripts/build_curated.py --currency=usd` (uses the latest USD/INR in `data/live.json`; pin with `FISCAL_USDINR=90`).

## Refresh frequency
Live job every 3 hours (GitHub Actions, commits only on change); the open page re-checks every 5 minutes; budget tabs on demand.
The Update Schedule tab lists every government release, its cadence and whether it is automatic.
