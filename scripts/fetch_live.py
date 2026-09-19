"""Pull live public data into data/live.json. Run daily by GitHub Actions and by scripts/update_all.py.

Sources (no API keys):
  1. World Bank API: India central-government fiscal ratios, nominal GDP, CPI, real interest rate.
     The fiscal series lag by years (see "latest year" on the Live tab); they are context and a cross-check, not current-year figures.
  2. Yahoo Finance chart API: USD/INR and Brent crude, the two market prices that move India's fiscal arithmetic
     (rupee -> external debt service and import bill, oil -> subsidies and excise).
  3. RBI press-release RSS: watches for borrowing-calendar, G-sec auction, state-finance and budget-related releases.
Each source is fetched independently: one failing never blocks the others, and a failed source keeps its previous data.
The file is only rewritten when the content changed.
"""
import json, re, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "live.json"
UA = {"User-Agent": "Mozilla/5.0 (india-fiscal-portal)"}

WB = {  # id: (label, unit)
    "GC.NLD.TOTL.GD.ZS": ("Central govt net lending / borrowing", "% of GDP"),
    "GC.REV.XGRT.GD.ZS": ("Central govt revenue (excl. grants)", "% of GDP"),
    "GC.XPN.TOTL.GD.ZS": ("Central govt expense", "% of GDP"),
    "GC.TAX.TOTL.GD.ZS": ("Central govt tax revenue", "% of GDP"),
    "GC.XPN.INTP.RV.ZS": ("Interest payments", "% of revenue"),
    "GC.DOD.TOTL.GD.ZS": ("Central govt debt", "% of GDP"),
    "NY.GDP.MKTP.CD": ("Nominal GDP", "US$"),
    "FP.CPI.TOTL.ZG": ("CPI inflation", "%"),
    "FR.INR.RINR": ("Real interest rate", "%"),
}
MARKET = {"INR=X": ("USD/INR", "INR per USD"), "BZ=F": ("Brent crude (front month)", "USD per barrel")}
RBI_RE = re.compile(r"borrowing|government of india|g-sec|dated securit|treasury bill|state development loan|state government|ways and means|"
                    r"union budget|fiscal|weekly statistical|monthly bulletin|state finances|market stabilisation|cash management", re.I)


def get(url, as_json=True):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body) if as_json else body.decode("utf-8-sig", "replace")


def world_bank():
    series, updated = {}, None
    for code, (label, unit) in WB.items():
        try:
            d = get(f"https://api.worldbank.org/v2/country/IND/indicator/{code}?format=json&per_page=100&date=2005:2030")
            updated = updated or d[0].get("lastupdated")
            pts = {x["date"]: x["value"] for x in d[1] if x["value"] is not None}
            if pts:
                series[code] = {"label": label, "unit": unit, "data": dict(sorted(pts.items()))}
        except Exception as e:
            print(f"WB {code} failed: {e}", file=sys.stderr)
    return {"source": "World Bank WDI (IMF Government Finance Statistics for the fiscal series)", "lastupdated": updated, "series": series}


def market():
    out = {}
    for tk, (label, unit) in MARKET.items():
        try:
            d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=1y&interval=1d")
            r = d["chart"]["result"][0]
            pts = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c]
            weekly = pts[::-5][::-1]  # every 5th trading day, anchored on the latest
            last_d, last = pts[-1]
            out[tk] = {"label": label, "unit": unit, "last": round(last, 2), "as_of": last_d,
                       "chg_1y_pct": round((last / pts[0][1] - 1) * 100, 1),
                       "chg_1m_pct": round((last / pts[-22][1] - 1) * 100, 1) if len(pts) > 22 else None,
                       "points": [[d_, round(c, 2)] for d_, c in weekly]}
        except Exception as e:
            print(f"market {tk} failed: {e}", file=sys.stderr)
    return {"source": "Yahoo Finance", "series": out}


def rbi_watch():
    try:
        root = ET.fromstring(get("https://www.rbi.org.in/pressreleases_rss.xml", as_json=False).lstrip("﻿"))
    except Exception as e:
        print(f"RBI RSS failed: {e}", file=sys.stderr)
        return None
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if RBI_RE.search(title):
            items.append({"title": title, "link": (it.findtext("link") or "").strip(), "date": (it.findtext("pubDate") or "").strip()})
    return {"feed": "https://www.rbi.org.in/pressreleases_rss.xml", "items": items[:10]}


def main():
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    fresh = {"world_bank": world_bank(), "market": market(), "rbi_watch": rbi_watch()}
    new, status = {}, {}
    for k, v in fresh.items():
        ok = bool(v) and bool(v.get("series") if k != "rbi_watch" else True)
        status[k] = "ok" if ok else "failed - kept previous data"
        new[k] = v if ok else old.get(k)
    if not (new["world_bank"] and new["world_bank"].get("series")) and not (new["market"] and new["market"].get("series")):
        sys.exit("No source returned data - leaving live.json untouched")
    new["status"] = status
    strip = lambda d: {k: v for k, v in d.items() if k != "fetched_at"}
    if strip(old) == strip(new):
        print("No change in upstream data")
        return
    new["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print("live.json updated", new["fetched_at"], status)


if __name__ == "__main__":
    main()
