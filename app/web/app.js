/* Dashboard front end.
 *
 * Money arrives from the API as strings, deliberately: JSON numbers are IEEE doubles
 * and would quietly undo the exactness the backend keeps all the way down. Number() is
 * called here only to format and to plot -- never to compute. Every figure on screen was
 * calculated server-side in Decimal.
 */

const S = {
  market: localStorage.getItem("market") || "INDIA",
  markets: [],
  view: null,
  token: localStorage.getItem("token") || "",
  config: { auth_enabled: false },
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const num = (s) => (s === null || s === undefined ? null : Number(s));

// Animation you cannot turn off is an accessibility bug: everything that moves checks
// this first and jumps straight to its final state instead.
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* --- formatting ----------------------------------------------------------- */

const locale = () => (S.view?.currency === "INR" ? "en-IN" : "en-US");

function money(value, digits = 0) {
  const n = num(value);
  if (n === null) return "—";
  return new Intl.NumberFormat(locale(), {
    style: "currency",
    currency: S.view?.currency || "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

// P/L always carries an explicit sign, so the colour is reinforcement and never the
// only thing telling you which way a number went.
function signed(value, digits = 0) {
  const n = num(value);
  if (n === null) return "—";
  return (n >= 0 ? "+" : "−") + money(Math.abs(n), digits).replace("-", "");
}

const pct = (value) => (num(value) === null ? "—" : `${num(value).toFixed(2)}%`);
const signedPct = (value) => {
  const n = num(value);
  return n === null ? "—" : `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(2)}%`;
};
const cls = (value) => {
  const n = num(value);
  return n === null ? "dim" : n >= 0 ? "gain" : "loss";
};

/* Count a number up from zero on arrival. Formatting runs through the same functions as
 * static text, so the animated value can never disagree with the settled one. */
function animateNumber(node, target, format) {
  if (target === null || REDUCED) {
    node.textContent = format(target);
    return;
  }
  const start = performance.now();
  const duration = 700;
  const step = (now) => {
    const p = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - p, 3);
    node.textContent = format(target * eased);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* --- api ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  const res = await fetch(path, { ...options, headers });

  if (res.status === 401 && S.config.auth_enabled) {
    S.token = "";
    localStorage.removeItem("token");
    renderAuth();
    throw new Error("Signed out");
  }
  if (!res.ok) {
    let detail;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = res.statusText;
    }
    const err = new Error(typeof detail === "string" ? detail : "Request failed");
    err.detail = detail;
    throw err;
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}

/* --- the exchange clock -----------------------------------------------------
 * One piece of live chrome, and it earns its place: whether the active market is open
 * is something a portfolio page should simply know. Trading hours only -- exchange
 * holidays are not modelled, so the dot is honest to the minute, not the calendar. */

const EXCHANGES = {
  INDIA: { tz: "Asia/Kolkata", label: "NSE", open: 9 * 60 + 15, close: 15 * 60 + 30 },
  US: { tz: "America/New_York", label: "NYSE", open: 9 * 60 + 30, close: 16 * 60 },
};

function tickClock() {
  const host = $("clock");
  if (!host) return;
  const ex = EXCHANGES[S.market];
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: ex.tz, weekday: "short", hour: "2-digit", minute: "2-digit",
      second: "2-digit", hour12: false, timeZoneName: "short",
    })
      .formatToParts(new Date())
      .map((p) => [p.type, p.value])
  );
  const minutes = Number(parts.hour) % 24 * 60 + Number(parts.minute);
  const weekday = !["Sat", "Sun"].includes(parts.weekday);
  const open = weekday && minutes >= ex.open && minutes < ex.close;

  host.innerHTML = "";
  const dot = el("span", `dot ${open ? "live" : ""}`);
  host.appendChild(dot);
  host.appendChild(
    el("span", null,
      `${ex.label} ${parts.hour}:${parts.minute}:${parts.second} ${parts.timeZoneName} · ${open ? "open" : "closed"}`)
  );
}

/* --- tabs ---------------------------------------------------------------------
 * Sections live behind tabs instead of one long scroll. Keys 1-4 switch panels. */

const TABS = ["overview", "stocks", "sectors", "explore", "report", "briefing", "manage"];

function setTab(name) {
  if (!TABS.includes(name)) name = "overview";
  for (const t of TABS) {
    $(`tab-${t}`).setAttribute("aria-selected", String(t === name));
    $(`panel-${t}`).hidden = t !== name;
  }
  localStorage.setItem("tab", name);
  // Charts rendered while their panel was hidden measured a zero-width host and fell
  // back to a guess; re-measure now that the panel has real geometry.
  if (name === "overview") renderCharts(false);
  if (name === "report") loadReport();  // lazy — don't fetch until the tab is opened
  if (name === "briefing") loadBriefing();
}

function wireTabs() {
  for (const t of TABS) $(`tab-${t}`).onclick = () => setTab(t);
  addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest("input, textarea, select") || $("up-dialog").open) return;
    const i = Number(e.key) - 1;
    if (i >= 0 && i < TABS.length) setTab(TABS[i]);
  });
  // A ?tab= param wins over the last-used tab, so a tab is deep-linkable.
  const wanted = new URLSearchParams(location.search).get("tab");
  setTab(wanted || localStorage.getItem("tab"));
}

/* --- charts ---------------------------------------------------------------
 * Hand-rolled SVG rather than a charting library: it keeps the page dependency-free
 * and gives exact control over the mark specs -- 4px rounded data-ends anchored to the
 * baseline, a 2px surface gap between bars, recessive axes.
 *
 * The viewBox is sized to the container's real width, so text renders at its CSS pixel
 * size on every screen instead of scaling down with the chart. A resize re-renders
 * (debounced, without replaying the entrance animation).
 */

const SEQ = ["--seq-650", "--seq-550", "--seq-450", "--seq-350", "--seq-250"];
const ROW = 30;
const GAP = 2;
const RADIUS = 4;

// Rounded only on the data end; the baseline end stays square so the bar reads as
// anchored to zero rather than floating. `w` is a positive magnitude and `dir` alone
// decides which way it grows -- passing a negative width as well would cancel out and
// send loss bars off to the gain side.
function barPath(x, y, w, h, dir) {
  const r = Math.min(RADIUS, w);
  if (w < 0.5) return "";
  const end = dir > 0 ? x + w : x - w;
  const lip = dir > 0 ? end - r : end + r;
  return `M${x},${y} H${lip} Q${end},${y} ${end},${y + r} V${y + h - r} Q${end},${y + h} ${lip},${y + h} H${x} Z`;
}

function svgRoot(host, height, animate) {
  host.innerHTML = "";
  const width = Math.max(host.clientWidth || 600, 300);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("height", height);
  svg.setAttribute("role", "img");
  svg.classList.add("emph");
  if (!animate) svg.classList.add("no-anim");
  host.appendChild(svg);
  return [svg, width];
}

function node(parent, tag, attrs, text) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  parent.appendChild(n);
  return n;
}

// A bar that rises out of its own baseline: scaleX(0 -> 1) about the exact baseline x,
// staggered by row. transform-box: view-box makes the origin a chart coordinate.
function growFrom(mark, originX, index) {
  mark.classList.add("grow");
  mark.style.transformBox = "view-box";
  mark.style.transformOrigin = `${originX}px 0px`;
  mark.style.setProperty("--i", index);
}

// Long sector names ("Engineering & capital goods") get an ellipsis when the label
// column can't hold them; the full name lives in the tooltip and the native <title>.
function fit(text, widthPx) {
  const max = Math.floor(widthPx / 6.6); // ~6.6px per character at 12px
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1)).trimEnd()}…` : text;
}

const tip = $("tip");
function hoverable(mark, label) {
  node(mark, "title", {}, label); // native fallback for touch and screen readers
  mark.addEventListener("mousemove", (e) => {
    tip.textContent = label;
    tip.classList.add("on");
    // Flip to the left of the cursor when the tooltip would run off the right edge.
    const w = tip.offsetWidth;
    const x = e.clientX + 14 + w > innerWidth - 8 ? e.clientX - 14 - w : e.clientX + 14;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, e.clientY - 10)}px`;
  });
  mark.addEventListener("mouseleave", () => tip.classList.remove("on"));
}

/* Sector allocation: magnitude, one hue, more-is-darker. Horizontal because sector
   names are long ("Pharma & Healthcare") and would never fit under vertical columns. */
function allocationChart(sectors, animate = true) {
  const host = $("chart-allocation");
  if (!sectors.length) {
    host.innerHTML = '<p class="empty">No holdings yet — head to Manage and upload a file.</p>';
    return;
  }
  const height = sectors.length * ROW + 10;
  const [svg, width] = svgRoot(host, height, animate);
  svg.setAttribute("aria-label", "Sector allocation as a share of invested amount");

  const LABEL = Math.min(170, Math.max(92, Math.round(width * 0.28)));
  const VALUE = 56;
  const span = width - LABEL - VALUE;
  const max = Math.max(...sectors.map((s) => num(s.allocation_pct)));

  node(svg, "line", {
    x1: LABEL - 4, y1: 0, x2: LABEL - 4, y2: sectors.length * ROW, class: "baseline",
  });

  sectors.forEach((s, i) => {
    const y = i * ROW;
    const w = max > 0 ? (num(s.allocation_pct) / max) * span : 0;
    const colour = `var(${SEQ[Math.min(i, SEQ.length - 1)]})`;

    node(svg, "text", {
      x: LABEL - 10, y: y + ROW / 2 + 4, "text-anchor": "end", class: "tick",
    }, fit(s.sector, LABEL - 14));

    const bar = node(svg, "path", {
      d: barPath(LABEL, y + GAP, w, ROW - GAP * 2 - 4, 1),
      fill: colour,
      class: "bar",
    });
    growFrom(bar, LABEL, i);
    hoverable(bar, `${s.sector} — ${pct(s.allocation_pct)} of invested (${money(s.invested)}, ${s.stock_count} stock${s.stock_count === 1 ? "" : "s"})`);

    // Direct label on every bar: only a handful of sectors, so no clutter, and it
    // removes any need to read a value off an axis.
    const label = node(svg, "text", {
      x: LABEL + w + 8, y: y + ROW / 2 + 4, class: "val fade",
    }, pct(s.allocation_pct));
    label.style.setProperty("--i", i);
  });
}

/* Profit/loss: polarity, so a diverging pair around a zero baseline. Blue for gain and
   red for loss rather than the conventional green/red -- under deuteranopia green and
   red measure dE 6.8, which is to say they are the same colour, and this is the one
   chart where that would matter. */
function pnlChart(sectors, animate = true) {
  const host = $("chart-pnl");
  const priced = sectors.filter((s) => s.pnl !== null);
  if (!priced.length) {
    host.innerHTML = '<p class="empty">No prices available yet — hit Refresh.</p>';
    return;
  }
  const height = priced.length * ROW + 16;
  const [svg, width] = svgRoot(host, height, animate);
  svg.setAttribute("aria-label", "Profit and loss by sector");

  const LABEL = Math.min(170, Math.max(92, Math.round(width * 0.28)));
  // Label room on BOTH sides: a maximal loss bar carries its label to the left, and
  // without the reserve it would crash into the sector names.
  const PAD = 58;
  const span = width - LABEL - PAD * 2;
  const zero = LABEL + PAD + span / 2;
  const half = span / 2;
  const max = Math.max(...priced.map((s) => Math.abs(num(s.pnl))), 1);

  node(svg, "line", {
    x1: zero, y1: 0, x2: zero, y2: priced.length * ROW, class: "zeroline",
  });

  priced.forEach((s, i) => {
    const y = i * ROW;
    const value = num(s.pnl);
    const w = (Math.abs(value) / max) * half;
    const up = value >= 0;

    node(svg, "text", {
      x: LABEL - 10, y: y + ROW / 2 + 4, "text-anchor": "end", class: "tick",
    }, fit(s.sector, LABEL - 14));

    const bar = node(svg, "path", {
      d: barPath(zero, y + GAP, w, ROW - GAP * 2 - 4, up ? 1 : -1),
      fill: up ? "var(--gain)" : "var(--loss)",
      class: "bar",
    });
    growFrom(bar, zero, i); // both directions collapse toward the zero line
    hoverable(bar, `${s.sector} — ${signed(s.pnl, 0)} (${signedPct(s.pnl_pct)})`);

    const label = node(svg, "text", {
      x: up ? zero + w + 8 : zero - w - 8,
      y: y + ROW / 2 + 4,
      "text-anchor": up ? "start" : "end",
      class: "val fade",
    }, signedPct(s.pnl_pct));
    label.style.setProperty("--i", i);
  });
}

function renderCharts(animate) {
  if (!S.view) return;
  allocationChart(S.view.sectors, animate);
  pnlChart(S.view.sectors, animate);
  donutChart(animate);
}

// Re-measure on resize -- without replaying the entrance, which would turn every window
// drag into a fireworks show.
let resizeTimer;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => renderCharts(false), 160);
});

/* --- tables --------------------------------------------------------------- */

function table(host, columns, rows, cellsFor) {
  host.innerHTML = "";
  const thead = el("thead");
  const tr = el("tr");
  columns.forEach((c) => tr.appendChild(el("th", null, c)));
  thead.appendChild(tr);
  host.appendChild(thead);

  const tbody = el("tbody");
  if (!rows.length) {
    const empty = el("tr");
    const td = el("td", "empty", "Nothing here yet — upload a holdings file in Manage.");
    td.colSpan = columns.length;
    empty.appendChild(td);
    tbody.appendChild(empty);
  }
  rows.forEach((row, i) => {
    const tr = cellsFor(row);
    tr.style.setProperty("--i", Math.min(i, 18)); // cap the stagger on long tables
    tbody.appendChild(tr);
  });
  host.appendChild(tbody);
}

function meter(value, index) {
  const wrap = el("div", "meter");
  wrap.appendChild(el("span", null, pct(value)));
  const track = el("div", "track");
  const fill = el("div", "fill");
  fill.style.width = `${Math.min(100, num(value))}%`;
  fill.style.setProperty("--i", Math.min(index, 18));
  track.appendChild(fill);
  wrap.appendChild(track);
  return wrap;
}

const CAP_LABEL = { large: "Large cap", mid: "Mid cap", small: "Small cap" };

function stocksTable(view) {
  table(
    $("stocks"),
    ["Sno", "Stock", "Sector", "Size", "Units", "Invested", "Market value", "P/L", "P/L %", "Allocation %"],
    view.stocks,
    (r) => {
      const tr = el("tr");
      tr.appendChild(el("td", "dim", String(r.sno)));

      const name = el("td");
      name.appendChild(el("span", "ticker", r.ticker));
      name.appendChild(el("div", "dim", r.name));
      tr.appendChild(name);

      tr.appendChild(el("td", null, r.sector));

      // Company size — the small-cap check made visible per holding, not only when an
      // agent warns. A dash when the provider gave us no market cap.
      const size = el("td");
      if (r.cap_class) {
        size.appendChild(el("span", `cap ${r.cap_class}`, CAP_LABEL[r.cap_class] || r.cap_class));
      } else {
        size.appendChild(el("span", "dim", "—"));
      }
      tr.appendChild(size);

      tr.appendChild(el("td", null, r.units));
      tr.appendChild(el("td", null, money(r.invested, 2)));

      const mv = el("td");
      if (r.market_value === null) {
        mv.appendChild(el("span", "chip", "no price"));
      } else {
        mv.appendChild(el("span", null, money(r.market_value, 2)));
        if (r.stale_price) mv.appendChild(el("span", "chip warn", "stale"));
      }
      tr.appendChild(mv);

      tr.appendChild(el("td", cls(r.pnl), r.pnl === null ? "—" : signed(r.pnl, 2)));
      tr.appendChild(el("td", cls(r.pnl_pct), signedPct(r.pnl_pct)));

      const alloc = el("td");
      alloc.appendChild(meter(r.allocation_pct, r.sno));
      tr.appendChild(alloc);
      return tr;
    }
  );
}

function sectorsTable(view) {
  table(
    $("sectors"),
    ["Sno", "Sector", "Stocks", "Invested", "Market value", "P/L", "P/L %", "Allocation %"],
    view.sectors,
    (r) => {
      const tr = el("tr");
      tr.appendChild(el("td", "dim", String(r.sno)));
      tr.appendChild(el("td", "ticker", r.sector));
      tr.appendChild(el("td", null, String(r.stock_count)));
      tr.appendChild(el("td", null, money(r.invested, 2)));

      const mv = el("td");
      mv.appendChild(el("span", null, r.market_value === null ? "—" : money(r.market_value, 2)));
      if (r.unpriced_count) {
        mv.appendChild(el("span", "chip warn", `${r.unpriced_count} unpriced`));
      }
      tr.appendChild(mv);

      tr.appendChild(el("td", cls(r.pnl), r.pnl === null ? "—" : signed(r.pnl, 2)));
      tr.appendChild(el("td", cls(r.pnl_pct), signedPct(r.pnl_pct)));

      const alloc = el("td");
      alloc.appendChild(meter(r.allocation_pct, r.sno));
      tr.appendChild(alloc);
      return tr;
    }
  );
}

/* --- Overview hero: a bento of the headline numbers + an allocation donut ----
 * The four totals stop being a flat strip and become a composition: a large
 * animated value with P/L pills, a hand-rolled allocation donut (drawn in with
 * anime.js), and four derived highlight tiles the eye reads at a glance. */

const SVGNS = "http://www.w3.org/2000/svg";
// Allocation is a share, not a gain or a loss, so the donut uses a cool
// violet->cyan chrome ramp that can't be mistaken for the blue/red data palette.
const DONUT = ["#7b6cff", "#6c5ce7", "#5b8def", "var(--seq-450)", "#2bb8f0", "#63d3e8", "#9aa0d8"];

function heroPill(k, v, klass) {
  const p = el("div", "hero-pill");
  p.appendChild(el("span", "hp-k", k));
  p.appendChild(el("span", `hp-v ${klass || ""}`, v));
  return p;
}

function heroValueTile(view) {
  const t = view.totals;
  const card = el("div", "card hero-value");
  card.appendChild(el("div", "label", "Total value"));
  const big = el("div", "hero-num");
  card.appendChild(big);
  animateNumber(big, num(t.market_value), (n) => money(n));

  const pills = el("div", "hero-pills");
  pills.appendChild(heroPill("Invested", money(num(t.invested)), ""));
  pills.appendChild(heroPill("Profit / loss", signed(num(t.pnl)), cls(t.pnl)));
  pills.appendChild(heroPill("Return", signedPct(num(t.pnl_pct)), cls(t.pnl_pct)));
  card.appendChild(pills);

  card.appendChild(
    el("div", "hero-sub",
      `${t.stock_count} stocks · ${t.sector_count} sectors · ${view.unpriced.length ? view.unpriced.length + " unpriced" : "all priced"}`)
  );
  return card;
}

function donutTile() {
  const card = el("div", "card hero-donut");
  card.appendChild(el("h2", null, "Allocation"));
  const wrap = el("div", "donut-wrap");
  const chart = el("div"); chart.id = "chart-donut";
  const legend = el("div", "donut-legend"); legend.id = "donut-legend";
  wrap.appendChild(chart);
  wrap.appendChild(legend);
  card.appendChild(wrap);
  return card;
}

function statTile(label, big, sub, subKlass) {
  const card = el("div", "card hero-stat");
  card.appendChild(el("div", "label", label));
  card.appendChild(el("div", "stat-big", big));
  card.appendChild(el("div", `stat-sub ${subKlass || ""}`, sub || ""));
  return card;
}

function heroStats(view) {
  const stocks = view.stocks || [], sectors = view.sectors || [];
  const pS = stocks.filter((s) => s.pnl_pct !== null);
  const pSec = sectors.filter((s) => s.pnl_pct !== null);
  const maxBy = (arr, key) => (arr.length ? arr.reduce((a, b) => (num(b[key]) > num(a[key]) ? b : a)) : null);
  const minBy = (arr, key) => (arr.length ? arr.reduce((a, b) => (num(b[key]) < num(a[key]) ? b : a)) : null);

  const mover = maxBy(pS, "pnl_pct");
  const best = maxBy(pSec, "pnl_pct");
  const worst = minBy(pSec, "pnl_pct");
  const weight = maxBy(stocks, "allocation_pct");

  return [
    statTile("Top mover", mover ? mover.ticker : "—", mover ? signedPct(mover.pnl_pct) : "no prices", mover ? cls(mover.pnl_pct) : "dim"),
    statTile("Best sector", best ? best.sector : "—", best ? signedPct(best.pnl_pct) : "", best ? cls(best.pnl_pct) : "dim"),
    statTile("Needs watch", worst ? worst.sector : "—", worst ? signedPct(worst.pnl_pct) : "", worst ? cls(worst.pnl_pct) : "dim"),
    statTile("Top weight", weight ? weight.ticker : "—", weight ? `${pct(weight.allocation_pct)} of book` : "", "dim"),
  ];
}

function renderHero(view) {
  const host = $("hero");
  host.innerHTML = "";
  host.appendChild(heroValueTile(view));
  host.appendChild(donutTile());
  heroStats(view).forEach((t) => host.appendChild(t));
  // The donut SVG itself is drawn by renderCharts, so a resize re-measures it.
}

/* The allocation donut: top sectors + "Others", each arc drawn in with anime.js
 * (a stroke-dashoffset sweep). Falls back to a static ring under reduced motion or
 * with no anime. Cool ramp only -- allocation is a share, never a gain or a loss. */
function donutChart(animate = true) {
  const host = $("chart-donut");
  const legendHost = $("donut-legend");
  if (!host || !S.view) return;
  host.innerHTML = "";
  if (legendHost) legendHost.innerHTML = "";

  const all = (S.view.sectors || [])
    .filter((s) => num(s.allocation_pct) > 0)
    .slice()
    .sort((a, b) => num(b.allocation_pct) - num(a.allocation_pct));
  if (!all.length) {
    host.innerHTML = '<p class="empty">No holdings yet.</p>';
    return;
  }

  const TOP = 6;
  const slices = all.slice(0, TOP).map((s) => ({ name: s.sector, pct: num(s.allocation_pct) }));
  const rest = all.slice(TOP).reduce((a, s) => a + num(s.allocation_pct), 0);
  if (rest > 0.05) slices.push({ name: "Others", pct: rest });
  const total = slices.reduce((a, s) => a + s.pct, 0) || 1;

  const size = 184, r = 72, cx = size / 2, cy = size / 2, sw = 22, C = 2 * Math.PI * r;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("height", size);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Sector allocation by share of invested amount");
  svg.classList.add("donut-svg");

  node(svg, "circle", { cx, cy, r, fill: "none", stroke: "var(--grid)", "stroke-width": sw });

  const doAnim = animate && !REDUCED && window.anime;
  let acc = 0;
  slices.forEach((s, i) => {
    const seg = Math.max(0.5, (s.pct / total) * C - 3); // a 3px gap between arcs
    const rot = -90 + (acc / total) * 360;
    const arc = node(svg, "circle", {
      cx, cy, r, fill: "none",
      stroke: DONUT[i % DONUT.length], "stroke-width": sw, "stroke-linecap": "round",
      "stroke-dasharray": `${seg} ${C}`,
      transform: `rotate(${rot} ${cx} ${cy})`,
    });
    arc.style.strokeDashoffset = doAnim ? seg : 0;
    s._arc = arc; s._seg = seg;
    acc += s.pct;
  });

  node(svg, "text", { x: cx, y: cy - 3, "text-anchor": "middle", class: "donut-num" }, String(S.view.totals.sector_count));
  node(svg, "text", { x: cx, y: cy + 15, "text-anchor": "middle", class: "donut-lbl" }, "sectors");
  host.appendChild(svg);

  if (doAnim) {
    slices.forEach((s, i) => {
      if (s._arc) anime.animate(s._arc, { strokeDashoffset: [s._seg, 0], duration: 900, delay: 120 + i * 110, ease: "out(3)" });
    });
  }

  if (legendHost) {
    slices.forEach((s, i) => {
      const row = el("div", "legend-row");
      const sw2 = el("span", "legend-sw");
      sw2.style.background = DONUT[i % DONUT.length];
      row.appendChild(sw2);
      row.appendChild(el("span", "legend-name", s.name));
      row.appendChild(el("span", "legend-pct", pct(s.pct)));
      legendHost.appendChild(row);
    });
  }
}

/* --- loading skeletons ------------------------------------------------------ */

function skeleton() {
  const k = $("hero");
  k.innerHTML = "";
  const big = el("div", "card hero-value");
  big.appendChild(el("div", "skel skel-s"));
  big.appendChild(el("div", "skel skel-l"));
  big.appendChild(el("div", "skel skel-s"));
  k.appendChild(big);
  const dn = el("div", "card hero-donut");
  dn.appendChild(el("div", "skel skel-l"));
  dn.appendChild(el("div", "skel skel-bar"));
  k.appendChild(dn);
  for (let i = 0; i < 4; i++) {
    const card = el("div", "card hero-stat");
    card.appendChild(el("div", "skel skel-s"));
    card.appendChild(el("div", "skel skel-l"));
    k.appendChild(card);
  }
  for (const id of ["chart-allocation", "chart-pnl"]) {
    const host = $(id);
    host.innerHTML = "";
    for (let i = 0; i < 5; i++) {
      const bar = el("div", "skel skel-bar");
      bar.style.width = `${88 - i * 15}%`;
      host.appendChild(bar);
    }
  }
}

/* --- notices -------------------------------------------------------------- */

function notices(view) {
  const host = $("notices");
  host.innerHTML = "";
  if (view.unpriced.length) {
    const b = el("div", "banner warn");
    b.appendChild(
      el("div", null,
        `No live price for ${view.unpriced.join(", ")}. Market value and P/L leave these out rather than guessing — allocation % is unaffected, because it is computed on cost basis.`)
    );
    host.appendChild(b);
  }
}

/* --- insights ------------------------------------------------------------- */

// A compact "3h ago" from an ISO timestamp — the timeline, without the noise of a date.
function ago(iso) {
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 90) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

async function insights() {
  const host = $("insights");
  const rows = await api(`/api/${S.market}/insights`);
  host.innerHTML = "";
  if (!rows.length) {
    host.appendChild(
      el("p", "empty",
        "No insights yet. Rule checks (concentration, small-cap, drift) run on every refresh; hit “Analyze now” for a news-driven read on your holdings.")
    );
    return;
  }
  rows.forEach((r) => {
    const b = el("div", `banner insight ${r.severity === "critical" ? "bad" : r.severity === "info" ? "" : "warn"}`);

    // Dismiss ✕ — removes the card and asks the server to keep it dismissed.
    const x = el("button", "dismiss", "✕");
    x.title = "Dismiss";
    x.onclick = async () => {
      b.style.opacity = "0.4";
      try {
        await api(`/api/insights/${r.id}/dismiss`, { method: "POST" });
        b.remove();
        if (!host.querySelector(".insight")) insights();
      } catch {
        b.style.opacity = "";
      }
    };
    b.appendChild(x);

    const body = el("div");
    body.appendChild(el("strong", null, r.title));
    body.appendChild(el("div", null, r.body));

    const meta = el("div", "insight-meta");
    const srcClass = r.source === "claude" ? "src-claude" : r.source === "groq" ? "src-groq" : r.source === "gemini" ? "src-gemini" : "";
    meta.appendChild(el("span", `tag ${srcClass}`, r.source));
    if (r.related_ticker) meta.appendChild(el("span", "tag", r.related_ticker));
    if (r.related_sector) meta.appendChild(el("span", "tag", r.related_sector));
    meta.appendChild(el("span", "tag when", ago(r.created_at)));
    body.appendChild(meta);

    b.appendChild(body);
    host.appendChild(b);
  });
}

/* --- Analyze now: rerun rules, ask the LLM analyst if the cooldown has elapsed --- */

async function analyzeNow() {
  const btn = $("analyze");
  btn.disabled = true;
  btn.classList.add("busy");
  try {
    const result = await api(`/api/${S.market}/analyze`, { method: "POST" });
    await insights();
    if (result.skipped_reason) {
      toast(result.skipped_reason);
    } else if (result.analyst_published) {
      toast(`Added ${result.analyst_published} news insight(s).`);
    } else {
      toast("No new news insights — nothing material in recent headlines.");
    }
  } catch (e) {
    toast(e.detail || e.message || "Analysis failed.");
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
  }
}

// A transient status line under the insights card — lighter than a banner.
function toast(text) {
  const host = $("insights");
  const existing = host.querySelector(".toast");
  if (existing) existing.remove();
  const t = el("p", "empty toast", text);
  host.prepend(t);
  setTimeout(() => t.remove(), 6000);
}

/* --- upload flow --------------------------------------------------------------
 * One dropzone, one dialog. Choosing a file opens the dialog, which asks only what the
 * file cannot say for itself: which market (a broker file has no idea), append vs
 * replace, and whether to keep unrecognised sector names. Errors render inside the
 * dialog so nothing is lost between attempts.
 */

const DLG = { file: null, kind: "upload" };

function renderIssues(host, issues, isWarning = false) {
  host.innerHTML = "";
  if (!issues?.length) return;
  const list = el("ul", "errors");
  issues.forEach((issue, i) => {
    const li = el("li");
    li.style.setProperty("--i", i);
    if (isWarning) li.style.borderLeftColor = "var(--warn)";
    if (issue.sheet !== undefined) {
      li.appendChild(el("code", null, `${issue.sheet} row ${issue.row}${issue.column ? " · " + issue.column : ""}`));
    }
    li.appendChild(el("span", null, issue.message));
    list.appendChild(li);
  });
  host.appendChild(list);
}

function showOk(message, warnings = []) {
  const host = $("upload-result");
  host.innerHTML = "";
  host.appendChild(el("div", "banner", message));
  if (!warnings.length) return;

  // The upload worked, but some holdings were reclassified. Say so. A sector allocation
  // that quietly moved a stock into "Others" is worse than one that refused to.
  const b = el("div", "banner warn");
  b.style.marginTop = "10px";
  b.appendChild(
    el("div", null, `${warnings.length} holding(s) had a sector we could not place:`)
  );
  host.appendChild(b);
  const list = el("div");
  renderIssues(list, warnings, true);
  host.appendChild(list);
}

function openDialog(file, kind) {
  DLG.file = file;
  DLG.kind = kind;
  $("dlg-title").textContent = kind === "upload" ? "Upload holdings" : "Remove holdings";
  $("dlg-file").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
  $("dlg-questions").hidden = kind !== "upload";
  $("dlg-errors").innerHTML = "";
  $("dlg-confirm").textContent = kind === "upload" ? "Upload" : "Remove";
  $("up-dialog").showModal();
}

async function submitDialog() {
  const confirm = $("dlg-confirm");
  const body = new FormData();
  body.append("file", DLG.file);
  if (DLG.kind === "upload") {
    body.append("mode", document.querySelector('input[name="dlg-mode"]:checked').value);
    body.append("market", document.querySelector('input[name="dlg-market"]:checked').value);
    body.append("keep_custom_sectors", $("dlg-keep").checked);
  }

  confirm.disabled = true;
  confirm.classList.add("busy");
  try {
    const path = DLG.kind === "upload" ? "/api/portfolio/upload" : "/api/portfolio/delete";
    const result = await api(path, { method: "POST", body });
    $("up-dialog").close();
    if (DLG.kind === "upload") {
      showOk(
        `Loaded ${result.transactions_added} holdings into ${result.markets.join(" and ")}.`,
        result.warnings
      );
    } else {
      const closed = result.removed.filter((r) => r.position_closed).length;
      showOk(`Removed ${result.removed.length} position(s)${closed ? `, ${closed} fully exited` : ""}.`);
    }
    load();
  } catch (e) {
    // Keep the dialog open with the row-level report: the user fixes the file and tries
    // again without losing their choices.
    const issues = e.detail?.errors || [{ message: e.detail || e.message }];
    renderIssues($("dlg-errors"), issues);
  } finally {
    confirm.disabled = false;
    confirm.classList.remove("busy");
  }
}

function wireUpload() {
  const zone = $("dropzone");
  const pickUp = $("file-up");
  const pickRm = $("file-rm");

  zone.onclick = () => pickUp.click();
  zone.onkeydown = (e) => (e.key === "Enter" || e.key === " ") && pickUp.click();
  $("remove-open").onclick = () => pickRm.click();

  pickUp.onchange = () => {
    if (pickUp.files[0]) openDialog(pickUp.files[0], "upload");
    pickUp.value = ""; // so the same file can be picked again after a fix
  };
  pickRm.onchange = () => {
    if (pickRm.files[0]) openDialog(pickRm.files[0], "remove");
    pickRm.value = "";
  };

  for (const evt of ["dragenter", "dragover"]) {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    });
  }
  for (const evt of ["dragleave", "drop"]) {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
    });
  }
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (!file) return;
    if (!/\.(xlsx|xlsm|csv)$/i.test(file.name)) {
      showOk("That doesn't look like a holdings file — use .xlsx or .csv.");
      return;
    }
    openDialog(file, "upload");
  });

  $("dlg-confirm").onclick = submitDialog;
  $("dlg-cancel").onclick = () => $("up-dialog").close();
}

/* --- render --------------------------------------------------------------- */

function marketToggle() {
  const host = $("market-toggle");
  host.innerHTML = "";
  S.markets.forEach((m) => {
    const b = el("button", null, m.label);
    b.setAttribute("aria-pressed", String(m.code === S.market));
    b.onclick = () => {
      S.market = m.code;
      localStorage.setItem("market", m.code);
      tickClock();
      load();
    };
    host.appendChild(b);
  });
}

async function load(refresh = false) {
  const button = $("refresh");
  button.disabled = true;
  button.classList.add("busy");
  if (refresh) button.textContent = "Fetching…";
  try {
    S.view = await api(`/api/${S.market}/dashboard${refresh ? "?refresh=true" : ""}`);
    marketToggle();
    $("market-label").textContent = `· ${S.view.label}`;
    notices(S.view);
    renderHero(S.view);
    renderCharts(true);
    stocksTable(S.view);
    sectorsTable(S.view);
    await insights();
    $("stamp").textContent = `prices as of ${new Date(S.view.generated_at).toLocaleString()} · allocation % is computed on invested amount, not market value`;
  } catch (e) {
    if (e.message !== "Signed out") {
      console.error(e);
      const host = $("notices");
      host.innerHTML = "";
      host.appendChild(
        el("div", "banner bad", `Couldn't load the dashboard: ${e.message}`)
      );
    }
  } finally {
    button.disabled = false;
    button.classList.remove("busy");
    button.textContent = "Refresh prices";
  }
}

/* --- theme ------------------------------------------------------------------
 * The pre-paint stamp in index.html applies the saved choice before first render; this
 * is just the toggle. An explicit choice is stored and wins over the OS preference. */

function effectiveDark() {
  const saved = localStorage.getItem("theme");
  return saved ? saved === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}

function paintThemeButton() {
  const b = $("theme");
  if (!b) return;
  const dark = effectiveDark();
  b.textContent = dark ? "☀" : "☾";
  b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}

/* --- auth (only when AUTH_ENABLED) ----------------------------------------
 * Talks to Supabase's auth API directly with the publishable key -- there is no SDK and
 * no CDN script. We never see a password: Supabase issues a JWT, the browser keeps it,
 * and our API only ever verifies it.
 *
 * Every row is scoped by the token's `sub`, so two accounts on the same deployment have
 * completely separate portfolios.
 */

async function supabaseAuth(path, body) {
  const res = await fetch(`${S.config.supabase_url}/auth/v1/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: S.config.supabase_anon_key,
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      data.error_description || data.msg || data.message || "Something went wrong."
    );
  }
  return data;
}

function renderAuth(mode = "signin") {
  const signup = mode === "signup";
  const wrap = document.querySelector(".wrap");
  wrap.innerHTML = "";

  const card = el("div", "card auth");
  card.appendChild(el("h1", null, "Portfolio"));
  card.appendChild(
    el("p", "empty", signup ? "Create an account." : "Sign in to your portfolio.")
  );

  const email = el("input");
  email.type = "email";
  email.placeholder = "you@example.com";
  email.autocomplete = "email";

  const password = el("input");
  password.type = "password";
  password.placeholder = signup ? "Password (8+ characters)" : "Password";
  password.autocomplete = signup ? "new-password" : "current-password";

  const message = el("p", "auth-msg");
  const submit = el("button", "action primary", signup ? "Create account" : "Sign in");

  const go = async () => {
    message.textContent = "";
    message.className = "auth-msg";

    if (signup && password.value.length < 8) {
      message.className = "auth-msg loss";
      message.textContent = "Use at least 8 characters.";
      return;
    }

    submit.disabled = true;
    submit.classList.add("busy");
    submit.textContent = signup ? "Creating…" : "Signing in…";
    try {
      const credentials = { email: email.value.trim(), password: password.value };
      const data = signup
        ? await supabaseAuth("signup", credentials)
        : await supabaseAuth("token?grant_type=password", credentials);

      if (data.access_token) {
        S.token = data.access_token;
        localStorage.setItem("token", S.token);
        location.reload();
        return;
      }

      // Signed up, but the project requires email confirmation, so there is no session
      // yet. Say so plainly instead of leaving them staring at a form that "worked".
      message.className = "auth-msg";
      message.textContent = `Check ${credentials.email} for a confirmation link, then sign in.`;
    } catch (e) {
      message.className = "auth-msg loss";
      message.textContent = e.message;
    } finally {
      submit.disabled = false;
      submit.classList.remove("busy");
      submit.textContent = signup ? "Create account" : "Sign in";
    }
  };

  submit.onclick = go;
  [email, password].forEach((input) => {
    // Only act on Enter. Returning the bare `&&` expression here would hand back `false`
    // for every other key, which a DOM0 handler treats as preventDefault() — silently
    // swallowing the keystroke so nothing can be typed. Keep it statement-bodied.
    input.onkeydown = (e) => {
      if (e.key === "Enter") go();
    };
    card.appendChild(input);
  });
  card.appendChild(submit);
  card.appendChild(message);

  const swap = el("button", "linkish", signup ? "Have an account? Sign in" : "New here? Create an account");
  swap.onclick = () => renderAuth(signup ? "signin" : "signup");
  card.appendChild(swap);

  wrap.appendChild(card);
}

/* --- boot ----------------------------------------------------------------- */

/* Supabase sends you back from a confirmation or recovery email with the session in the
 * URL *fragment* -- #access_token=...&refresh_token=... -- not the query string. Nothing
 * reads it unless we do, so without this you click the link in your email, land back on
 * the app, and are still looking at a login form.
 *
 * The fragment is stripped from the address bar immediately afterwards: it holds a live
 * bearer token, and leaving it there means it survives in history, in a screenshot, and
 * in anything the user copy-pastes out of the URL bar. */
function claimTokenFromUrl() {
  if (!location.hash.includes("access_token")) return false;

  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get("access_token");
  history.replaceState(null, "", location.pathname + location.search);

  if (!token) {
    const error = fragment.get("error_description") || fragment.get("error");
    if (error) console.warn("Supabase redirect returned an error:", error);
    return false;
  }

  S.token = token;
  localStorage.setItem("token", token);
  return true;
}

/* --- Explore -------------------------------------------------------------- */

// Which market the Explore lookup targets — its own toggle, independent of the
// dashboard's, because you research US names while holding an Indian book.
const EXP = { market: S.market };

const RATIO_ROWS = [
  ["pe", "P/E"], ["pb", "P/B"], ["roe_pct", "ROE", "%"], ["profit_margin_pct", "Margin", "%"],
  ["debt_to_equity", "Debt/Equity"], ["revenue_growth_pct", "Rev growth", "%"],
  ["dividend_yield_pct", "Div yield", "%"], ["beta", "Beta"],
];

function exploreMarketToggle() {
  const host = $("explore-market");
  host.innerHTML = "";
  S.markets.forEach((m) => {
    const b = el("button", null, m.code);
    b.type = "button";
    b.setAttribute("aria-pressed", String(m.code === EXP.market));
    b.onclick = () => {
      EXP.market = m.code;
      exploreMarketToggle();
    };
    host.appendChild(b);
  });
}

async function runExplore(refresh = false) {
  const ticker = $("explore-ticker").value.trim().toUpperCase();
  if (!ticker) return;
  const btn = $("explore-go");
  btn.disabled = true;
  btn.classList.add("busy");
  const host = $("explore-result");
  host.innerHTML = '<p class="empty">Fetching fundamentals…</p>';
  try {
    const q = refresh ? "?refresh=true" : "";
    const d = await api(`/api/${EXP.market}/explore/${encodeURIComponent(ticker)}${q}`);
    renderExplore(d);
  } catch (e) {
    host.innerHTML = "";
    host.appendChild(el("div", "banner bad", e.detail || e.message || "Lookup failed."));
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
  }
}

function renderExplore(d) {
  const host = $("explore-result");
  host.innerHTML = "";
  const usage = $("explore-usage");
  usage.textContent =
    d.usage.limit === null
      ? "Premium · unlimited analyses"
      : `Free plan · ${d.usage.used} of ${d.usage.limit} analyses used today`;

  const f = d.fundamentals;
  if (!f) {
    host.appendChild(el("div", "banner bad", d.error || "No data for that symbol."));
    return;
  }

  const title = el("div", "explore-title");
  title.appendChild(el("strong", null, `${f.name || d.ticker} (${d.ticker})`));
  title.appendChild(el("span", "dim", `${f.sector || "—"} · ${f.currency || ""}`));
  host.appendChild(title);

  // Ratio grid — a dash for anything the provider omitted.
  const ratios = el("div", "ratios");
  const val = (v, unit) => (v === null || v === undefined ? "—" : `${v}${unit || ""}`);
  RATIO_ROWS.forEach(([key, label, unit]) => {
    const cell = el("div", "ratio");
    cell.appendChild(el("div", "k", label));
    cell.appendChild(el("div", "v", val(f[key], unit)));
    ratios.appendChild(cell);
  });
  host.appendChild(ratios);

  if (d.read) {
    const reads = el("div", "reads");
    const add = (k, text, cls) => {
      if (!text) return;
      const box = el("div", `read ${cls || ""}`);
      box.appendChild(el("div", "k", k));
      box.appendChild(el("div", null, text));
      reads.appendChild(box);
    };
    add("Valuation", d.read.valuation);
    add("Profitability", d.read.profitability);
    add("Leverage", d.read.leverage);
    add("Momentum", d.read.momentum);
    add("Overall", d.read.overall, "overall");
    host.appendChild(reads);

    const foot = el("div", "insight-meta");
    foot.appendChild(el("span", `tag ${d.source === "claude" ? "src-claude" : "src-groq"}`, d.source));
    if (d.cached) {
      const refresh = el("button", "linkish", "Refresh analysis");
      refresh.onclick = () => runExplore(true);
      foot.appendChild(refresh);
    }
    host.appendChild(foot);
  } else if (d.error) {
    host.appendChild(el("div", "banner warn", d.error));
  }
}

/* --- Report --------------------------------------------------------------- */

function scoreColour(score) {
  if (score >= 75) return "var(--gain-text)";
  if (score >= 50) return "var(--warn)";
  return "var(--loss-text)";
}

async function loadReport() {
  const host = $("report-result");
  if (!host.dataset.loaded) host.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const d = await api(`/api/${S.market}/report`);
    renderReport(d);
  } catch (e) {
    host.innerHTML = "";
    host.appendChild(el("div", "banner bad", e.detail || e.message));
  }
}

function renderReport(d) {
  const host = $("report-result");
  host.dataset.loaded = "1";
  host.innerHTML = "";
  if (!d || d.health_score === undefined) {
    host.appendChild(
      el("p", "empty",
        "No report yet. One is generated automatically each week; hit “Generate report” for one now.")
    );
    return;
  }

  const head = el("div", "report-head");
  const ring = el("div", "score-ring");
  ring.style.setProperty("--p", d.health_score);
  ring.style.setProperty("--score-color", scoreColour(d.health_score));
  ring.appendChild(el("span", null, String(d.health_score)));
  head.appendChild(ring);

  const meta = el("div");
  meta.appendChild(el("strong", null, "Health score"));
  meta.appendChild(
    el("div", "meta", `week of ${d.period_start} · by ${d.generated_by} · ${ago(d.generated_at)}`)
  );
  head.appendChild(meta);
  host.appendChild(head);

  host.appendChild(el("p", "report-narrative", d.narrative));

  const list = el("div", "checklist");
  (d.red_flags || []).forEach((f) => {
    const row = el("div", `check ${f.ok ? "ok" : "flag"}`);
    row.appendChild(el("span", "mark", f.ok ? "✓" : "✕"));
    row.appendChild(el("span", null, f.text));
    list.appendChild(row);
  });
  host.appendChild(list);
}

async function generateReport() {
  const btn = $("report-go");
  btn.disabled = true;
  btn.classList.add("busy");
  try {
    const d = await api(`/api/${S.market}/report`, { method: "POST" });
    renderReport(d);
  } catch (e) {
    toastReport(e.detail || e.message);
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
  }
}

function toastReport(text) {
  const host = $("report-result");
  const t = el("div", "banner bad", text);
  host.prepend(t);
  setTimeout(() => t.remove(), 6000);
}

/* --- Briefing (the hierarchical agent read) -------------------------------
 * Cross-market: the orchestrator sits above both books, so this is one document, not one
 * per market. Direction (positive/negative/neutral) reuses the colourblind-safe blue/red
 * gain-loss palette — a positive is a gain, a negative is a loss, visually. */

const DIR = {
  positive: { cls: "up", arrow: "▲", label: "Positive" },
  negative: { cls: "down", arrow: "▼", label: "Negative" },
  neutral: { cls: "flat", arrow: "—", label: "Neutral" },
};

function dirChip(direction) {
  const d = DIR[direction] || DIR.neutral;
  const chip = el("span", `dir-chip ${d.cls}`);
  chip.appendChild(el("span", "dir-arrow", d.arrow));
  chip.appendChild(el("span", null, d.label));
  return chip;
}

async function loadBriefing() {
  const host = $("briefing-result");
  if (!host.dataset.loaded) host.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const d = await api("/api/briefing");
    renderBriefing(d);
  } catch (e) {
    host.innerHTML = "";
    host.appendChild(el("div", "banner bad", e.detail || e.message));
  }
}

function renderBriefing(d) {
  const host = $("briefing-result");
  host.dataset.loaded = "1";
  host.innerHTML = "";
  if (!d || d.headline === undefined) {
    host.appendChild(
      el("p", "empty",
        "No briefing yet. One is generated automatically each week; hit “Generate briefing” to run the agents now.")
    );
    return;
  }

  // Headline band: the overall verdict, up top.
  const head = el("div", "brief-head");
  const line = el("div", "brief-headline");
  line.appendChild(dirChip(d.overall_direction));
  line.appendChild(el("strong", null, d.headline));
  head.appendChild(line);
  head.appendChild(
    el("div", "meta", `week of ${d.period_start} · by ${d.generated_by} · ${ago(d.generated_at)}`)
  );
  host.appendChild(head);

  if (d.summary) host.appendChild(el("p", "brief-narrative", d.summary));

  // Positives / negatives, side by side.
  if ((d.positives || []).length || (d.negatives || []).length) {
    const cols = el("div", "brief-columns");
    cols.appendChild(pnColumn("What's working", d.positives, "up"));
    cols.appendChild(pnColumn("What to watch", d.negatives, "down"));
    host.appendChild(cols);
  }

  // Per-market sections, each with its sector cards.
  (d.markets || []).forEach((m) => {
    const sec = el("section", "brief-market");
    const mh = el("div", "brief-market-head");
    mh.appendChild(dirChip(m.overall_direction));
    mh.appendChild(el("h3", null, marketLabel(m.market)));
    sec.appendChild(mh);
    if (m.summary) sec.appendChild(el("p", "brief-market-summary", m.summary));
    if (m.cross_sector_notes) {
      const note = el("p", "brief-cross");
      note.appendChild(el("span", "cross-tag", "across sectors"));
      note.appendChild(el("span", null, m.cross_sector_notes));
      sec.appendChild(note);
    }
    const grid = el("div", "brief-sectors");
    (m.sectors || []).forEach((s) => grid.appendChild(sectorCard(s)));
    sec.appendChild(grid);
    host.appendChild(sec);
  });
}

function marketLabel(code) {
  const m = S.markets.find((x) => x.code === code);
  return m ? m.label : code;
}

function pnColumn(title, items, cls) {
  const col = el("div", `brief-col ${cls}`);
  col.appendChild(el("div", "brief-col-title", title));
  if (!(items || []).length) {
    col.appendChild(el("div", "brief-col-empty", "—"));
    return col;
  }
  const ul = el("ul");
  items.forEach((t) => ul.appendChild(el("li", null, t)));
  col.appendChild(ul);
  return col;
}

function sectorCard(s) {
  const card = el("div", `sector-card ${DIR[s.direction]?.cls || "flat"}`);
  const top = el("div", "sector-card-head");
  top.appendChild(el("strong", null, s.sector));
  top.appendChild(dirChip(s.direction));
  card.appendChild(top);

  const sig = el("div", "sig", `${s.significance} significance`);
  card.appendChild(sig);

  const wh = el("div", "sector-field");
  wh.appendChild(el("span", "field-label", "What happened"));
  wh.appendChild(el("span", null, s.what_happened));
  card.appendChild(wh);

  if (s.what_it_indicates) {
    const wi = el("div", "sector-field");
    wi.appendChild(el("span", "field-label", "What it indicates"));
    wi.appendChild(el("span", null, s.what_it_indicates));
    card.appendChild(wi);
  }

  if ((s.tickers || []).length) {
    const tags = el("div", "sector-tags");
    s.tickers.forEach((t) => tags.appendChild(el("span", "tag", t)));
    card.appendChild(tags);
  }
  return card;
}

async function generateBriefing() {
  const btn = $("briefing-go");
  btn.disabled = true;
  btn.classList.add("busy");
  try {
    const r = await api("/api/briefing", { method: "POST" });
    if (r.briefing) renderBriefing(r.briefing);
    if (r.skipped_reason) toastBriefing(r.skipped_reason);
  } catch (e) {
    toastBriefing(e.detail || e.message || "Briefing failed.");
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
  }
}

function toastBriefing(text) {
  const host = $("briefing-result");
  const t = el("div", "banner bad", text);
  host.prepend(t);
  setTimeout(() => t.remove(), 6000);
}

/* Billing / plan UI intentionally omitted. Plan tiering is still enforced
   server-side (select_model uses the user's stored plan); the app deliberately
   shows no upgrade, membership, or plan-status surface. */

/* --- boot ----------------------------------------------------------------- */

async function boot() {
  // Theme first: it must work on the auth screen too.
  paintThemeButton();
  $("theme").onclick = () => {
    const next = effectiveDark() ? "light" : "dark";
    localStorage.setItem("theme", next);
    document.documentElement.dataset.theme = next;
    paintThemeButton();
  };

  // The header earns its hairline once content scrolls beneath it.
  const header = document.querySelector("header");
  addEventListener("scroll", () => header.classList.toggle("stuck", scrollY > 4), {
    passive: true,
  });

  S.config = await (await fetch("/api/config")).json();
  claimTokenFromUrl();

  if (S.config.auth_enabled && !S.token) {
    renderAuth();
    return;
  }

  tickClock();
  setInterval(tickClock, 1000);
  wireTabs();
  wireUpload();
  skeleton();

  S.markets = await api("/api/portfolio/markets");

  if (S.config.auth_enabled) {
    const out = el("button", "action", "Sign out");
    out.onclick = () => {
      localStorage.removeItem("token");
      location.reload();
    };
    document.querySelector("header").appendChild(out);
  }

  $("refresh").onclick = () => load(true);
  $("export").onclick = () => {
    window.location = `/api/${S.market}/export`;
  };

  // Phase-2 wiring.
  $("analyze").onclick = analyzeNow;
  $("report-go").onclick = generateReport;
  $("briefing-go").onclick = generateBriefing;
  EXP.market = S.market;
  exploreMarketToggle();
  $("explore-form").onsubmit = (e) => {
    e.preventDefault();
    runExplore();
  };

  load();
}

boot();
