(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const PALETTE = ["#2e75b6", "#e08a1e", "#1a7f4b", "#c0392b", "#7d5ba6", "#17a2b8", "#b8860b", "#6c7a89", "#d6558b", "#3a9d8f"];
  let curated, live, charts = [], current, tabs;

  const fetchJSON = async p => { const r = await fetch(p + "?t=" + Date.now()); if (!r.ok) throw new Error(p + " " + r.status); return r.json(); };

  async function init() {
    try { [curated, live] = await Promise.all([fetchJSON("data/curated.json"), fetchJSON("data/live.json").catch(() => null)]); }
    catch (e) { $("#view").innerHTML = `<div class="banner warn">Could not load data: ${esc(e.message)}</div>`; return; }
    $("#srcfile").textContent = curated.source_file;
    $("#asof").textContent = `Budget data rebuilt ${curated.built_at}` + (live ? ` · live data ${live.fetched_at}` : "");
    tabs = curated.sheets.map((s, i) => ({ id: "s" + i, name: s.name, sheet: s }));
    if (live) { const at = tabs.findIndex(t => t.name === "Data Notes"); tabs.splice(at < 0 ? tabs.length : at, 0, { id: "live", name: "Live Data", live: true }); }
    $("#tabs").innerHTML = tabs.map(t => `<button class="tab${t.live ? " live" : ""}" role="tab" data-id="${t.id}" aria-selected="false">${esc(t.name)}</button>`).join("");
    $("#tabs").addEventListener("click", e => { const b = e.target.closest(".tab"); if (b) show(b.dataset.id); });
    addEventListener("hashchange", () => show(location.hash.slice(1)));
    show(location.hash.slice(1) || "s0");
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => show(current));
  }

  function show(id) {
    const t = tabs.find(x => x.id === id) || tabs[0];
    current = t.id; history.replaceState(null, "", "#" + t.id);
    document.querySelectorAll(".tab").forEach(b => b.setAttribute("aria-selected", b.dataset.id === t.id));
    charts.forEach(c => c.destroy()); charts = [];
    const view = $("#view");
    view.innerHTML = t.live ? renderLive() : renderBlocks(t.sheet.blocks);
    view.querySelectorAll("canvas[data-chart]").forEach(cv => makeChart(cv, JSON.parse(cv.dataset.chart)));
    scrollTo(0, 0);
  }

  // ---------- curated blocks ----------
  function renderBlocks(blocks) {
    let out = "";
    for (const b of blocks) {
      switch (b.t) {
        case "title": out += `<h2 class="sheet">${esc(b.text)}</h2>`; break;
        case "subtitle": out += `<p class="subtitle">${esc(b.text)}</p>`; break;
        case "section": out += `<h3 class="section">${esc(b.text)}</h3>`; break;
        case "subhead": out += `<div class="subhead">${esc(b.text)}</div>`; break;
        case "note": out += `<p class="note">${esc(b.text)}</p>`; break;
        case "banner": out += `<div class="banner ${b.kind || ""}">${esc(b.text)}</div>`; break;
        case "cards": out += cardsHTML(b.cards); break;
        case "table": out += tableHTML(b); break;
        case "chart": out += chartBox(b); break;
        case "grid": out += `<div class="grid2">${b.charts.map(c => chartBox(c)).join("")}</div>`; break;
      }
    }
    return out;
  }

  const cardsHTML = cards => `<div class="cards">${cards.map(c => `<div class="card ${/^[-−]/.test(c[1] || "") ? "neg" : ""}"><div class="l">${esc(c[0])}</div><div class="v">${esc(c[1])}</div><div class="n">${esc(c.slice(2).join(" · "))}</div></div>`).join("")}</div>`;

  function tableHTML(t) {
    const head = t.header.map(h => `<th>${esc(h)}</th>`).join("");
    const rows = t.rows.map(r => {
      if (r.group) return `<tr class="${r.cls}"><td colspan="${t.header.length}">${esc(r.cells[0].d)}</td></tr>`;
      const tds = r.cells.map((c, i) => {
        const isNum = c.v !== undefined;
        const flag = c.d === "FLAG" ? " flag" : c.d === "OK" ? " okc" : "";
        return `<td class="${isNum ? "num" : ""}${isNum && c.v < 0 ? " neg" : ""}${flag}">${esc(c.d)}</td>`;
      }).join("");
      return `<tr class="${r.cls}">${tds}</tr>`;
    }).join("");
    return `<div class="tablewrap"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function chartBox(c) {
    const tall = c.horizontal || (c.type === "bar" && c.labels.length > 8 && c.series.length === 1);
    return `<div class="chartbox ${tall ? "tall" : ""}"><h4>${esc(c.title)}</h4><div class="cv"><canvas data-chart='${esc(JSON.stringify(c)).replace(/'/g, "&#39;")}'></canvas></div></div>`;
  }

  function makeChart(cv, c) {
    const ink = css("--muted"), grid = css("--line");
    const horizontal = !!c.horizontal;
    const fmt = v => (v == null ? "" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
    const suffix = c.unit === "%" ? "%" : "";
    let cfg;
    if (c.type === "doughnut") {
      const total = c.series[0].data.reduce((a, b) => a + (b || 0), 0);
      cfg = { type: "doughnut", data: { labels: c.labels, datasets: [{ data: c.series[0].data, backgroundColor: PALETTE, borderColor: css("--card"), borderWidth: 2 }] },
        options: { maintainAspectRatio: false, plugins: { legend: { position: innerWidth < 600 ? "bottom" : "right", labels: { color: ink, boxWidth: 12, font: { size: 11 } } },
          tooltip: { callbacks: { label: x => ` ${x.label}: ${fmt(x.parsed)} (${(x.parsed / total * 100).toFixed(1)}%)` } } } } };
    } else {
      const datasets = c.series.map((s, i) => {
        const isLine = c.type === "line" || (c.type === "combo" && i > 0);
        const col = PALETTE[i % PALETTE.length];
        const base = { label: s.name, data: s.data, type: isLine ? "line" : "bar", order: isLine ? 0 : 1 };
        if (isLine) Object.assign(base, { borderColor: col, backgroundColor: col, tension: .25, pointRadius: s.data.length > 20 ? 0 : 3, spanGaps: true, borderWidth: 2, yAxisID: c.dual && c.type === "combo" ? "y2" : "y" });
        else base.backgroundColor = (c.series.length === 1 && !c.stacked) ? s.data.map(v => (v < 0 ? css("--neg") : PALETTE[0])) : col;
        return base;
      });
      const tick = { color: ink, callback: v => fmt(v) + suffix };
      const zero = c.type !== "line";
      const scales = horizontal
        ? { y: { ticks: { color: ink, font: { size: 11 }, autoSkip: false }, grid: { color: grid }, stacked: !!c.stacked }, x: { ticks: tick, grid: { color: grid }, stacked: !!c.stacked } }
        : { x: { ticks: { color: ink, font: { size: 11 }, maxRotation: 60, autoSkip: true }, grid: { color: grid }, stacked: !!c.stacked }, y: { ticks: tick, beginAtZero: zero, grid: { color: grid }, stacked: !!c.stacked } };
      if (c.dual) scales.y2 = { position: "right", ticks: { color: ink, callback: v => fmt(v) + "%" }, grid: { drawOnChartArea: false } };
      cfg = { type: "bar", data: { labels: c.labels, datasets },
        options: { maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x", interaction: { mode: "index", intersect: false },
          plugins: { legend: { display: datasets.length > 1, labels: { color: ink, boxWidth: 12 } }, tooltip: { callbacks: { label: x => ` ${x.dataset.label}: ${fmt(x.parsed[horizontal ? "x" : "y"])}${suffix}` } } }, scales } };
    }
    charts.push(new Chart(cv, cfg));
  }

  // ---------- live layer ----------
  const yrs = o => Object.keys(o).sort();
  const usdBn = v => v == null ? null : Math.round(v / 1e9 * 10) / 10;   // US$ -> US$ bn, 1 dp
  const sgn = v => (v > 0 ? "+" : "") + v;

  function renderLive() {
    const wb = live.world_bank || { series: {} }, S = wb.series || {}, M = (live.market || {}).series || {}, rbi = live.rbi_watch || { items: [] }, st = live.status || {};
    const failed = Object.entries(st).filter(([, v]) => v !== "ok").map(([k]) => k);
    let out = `<h2 class="sheet">Live public data</h2><p class="subtitle">Pulled by a scheduled job from public feeds; last run ${esc(live.fetched_at)}.</p>`;
    if (failed.length) out += `<div class="banner warn">Last run could not refresh: ${failed.map(esc).join(", ")}. Showing the previous data for those.</div>`;
    out += rbi.items.length
      ? `<div class="banner warn"><b>RBI releases spotted (borrowing, G-sec, state finances, budget):</b><ul class="rbi">${rbi.items.map(i => `<li><a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.title)}</a> <span>${esc((i.date || "").slice(0, 16))}</span></li>`).join("")}</ul></div>`
      : `<div class="banner">RBI press-release watch: nothing on borrowing, G-secs, state finances or the budget in the latest feed window.</div>`;

    const cards = [];
    for (const [tk, m] of Object.entries(M)) cards.push([`${m.label}`, m.unit.startsWith("INR") ? `₹${m.last}` : `$${m.last}`, `${sgn(m.chg_1y_pct)}% over 1 year`, m.chg_1m_pct != null ? `${sgn(m.chg_1m_pct)}% over 1 month` : "", `as of ${m.as_of}`].filter(Boolean));
    const latest = code => { const d = S[code]?.data || {}, ys = yrs(d), y = ys[ys.length - 1]; return { y, v: d[y], p: d[ys[ys.length - 2]], py: ys[ys.length - 2] }; };
    if (S["FP.CPI.TOTL.ZG"]) { const l = latest("FP.CPI.TOTL.ZG"); cards.push([`CPI inflation (${l.y})`, l.v.toFixed(2) + "%", `${l.py}: ${l.p?.toFixed(2)}%`]); }
    if (S["NY.GDP.MKTP.CD"]) { const l = latest("NY.GDP.MKTP.CD"); cards.push([`Nominal GDP (${l.y})`, `$${usdBn(l.v).toLocaleString("en-US")} bn`, `${((l.v / l.p - 1) * 100).toFixed(1)}% vs ${l.py} (in US$)`]); }
    if (S["GC.NLD.TOTL.GD.ZS"]) { const l = latest("GC.NLD.TOTL.GD.ZS"); cards.push([`Central govt net borrowing (${l.y})`, l.v.toFixed(2) + "% of GDP", "World Bank / IMF GFS, latest available"]); }
    if (S["GC.XPN.INTP.RV.ZS"]) { const l = latest("GC.XPN.INTP.RV.ZS"); cards.push([`Interest as % of revenue (${l.y})`, l.v.toFixed(1) + "%", "World Bank / IMF GFS, latest available"]); }
    out += cardsHTML(cards);

    const mk = (title, codes, type, opt = {}) => {
      const years = [...new Set(codes.flatMap(c => yrs(S[c]?.data || {})))].sort();
      const conv = opt.conv || (x => x == null ? null : Math.round(x * 100) / 100);
      return chartBox({ title, type, unit: opt.unit, labels: years, series: codes.filter(c => S[c]).map(c => ({ name: S[c].label, data: years.map(y => conv(S[c].data[y])) })) });
    };
    const mline = tk => M[tk] ? chartBox({ title: `${M[tk].label}, 1 year (${M[tk].unit})`, type: "line", labels: M[tk].points.map(p => p[0]), series: [{ name: M[tk].label, data: M[tk].points.map(p => p[1]) }] }) : "";
    out += `<div class="grid2">${mline("INR=X")}${mline("BZ=F")}</div>`;
    out += `<h3 class="section">Government fiscal ratios (World Bank / IMF)</h3>`;
    const fy = latest("GC.NLD.TOTL.GD.ZS").y;
    out += `<div class="banner">The latest fiscal-ratio year available from this source is <b>${esc(fy || "n/a")}</b>: it lags the budget by several years. Use it for long-run context and as an independent cross-check on the Trends tab, not for current-year numbers. The 2022 point sits on a different reporting basis from earlier years, so the step-change from 2018 is partly a definitional break.</div>`;
    out += `<div class="grid2">${mk("Central govt revenue, expense, tax (% of GDP)", ["GC.REV.XGRT.GD.ZS", "GC.XPN.TOTL.GD.ZS", "GC.TAX.TOTL.GD.ZS"], "line", { unit: "%" })}${mk("Net lending / borrowing (% of GDP)", ["GC.NLD.TOTL.GD.ZS"], "bar", { unit: "%" })}${mk("Interest payments (% of revenue)", ["GC.XPN.INTP.RV.ZS"], "line", { unit: "%" })}${mk("Nominal GDP (US$ bn)", ["NY.GDP.MKTP.CD"], "bar", { conv: usdBn })}${mk("CPI inflation (%)", ["FP.CPI.TOTL.ZG"], "bar", { unit: "%" })}${mk("Central govt debt (% of GDP)", ["GC.DOD.TOTL.GD.ZS"], "line", { unit: "%" })}</div>`;

    const years = yrs(S["GC.NLD.TOTL.GD.ZS"]?.data || {}).slice(-8);
    const rows = Object.entries(S).map(([code, s]) => {
      const cur = code === "NY.GDP.MKTP.CD";
      return `<tr><td>${esc(s.label)} (${cur ? "US$ bn" : esc(s.unit)})</td>${years.map(y => { const v = s.data[y]; const x = v == null ? "" : cur ? usdBn(v).toLocaleString("en-US") : v.toFixed(2); return `<td class="num${v < 0 ? " neg" : ""}">${x}</td>`; }).join("")}</tr>`;
    }).join("");
    out += `<h3 class="section">Series table (last ${years.length} years)</h3><div class="tablewrap"><table><thead><tr><th>Series</th>${years.map(y => `<th>${y}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
    out += `<h3 class="section">Primary sources to check first</h3><ul class="src"><li><a href="https://www.indiabudget.gov.in/" target="_blank" rel="noopener">Union Budget documents</a>: Budget at a Glance, Receipt and Expenditure Budgets</li><li><a href="https://cga.nic.in/MonthlyAccounts.aspx" target="_blank" rel="noopener">CGA monthly accounts</a>: month-by-month actuals against BE (not machine-readable, so not auto-pulled)</li><li><a href="https://prsindia.org/budgets" target="_blank" rel="noopener">PRS India budget analysis</a></li><li><a href="https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx" target="_blank" rel="noopener">RBI press releases</a>: borrowing calendar, auction results</li></ul>`;
    out += `<p class="note">Sources: ${esc(wb.source || "")}; ${esc((live.market || {}).source || "")}; RBI press-release feed. World Bank dataset updated ${esc(wb.lastupdated || "n/a")}. Fiscal-year and calendar-year labelling differ between sources, so live and budget figures will not match to the decimal.</p>`;
    return out;
  }

  init();
})();
