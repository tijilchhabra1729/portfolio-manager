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

/* --- api ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  const res = await fetch(path, { ...options, headers });

  if (res.status === 401 && S.config.auth_enabled) {
    S.token = "";
    localStorage.removeItem("token");
    renderLogin();
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

/* --- charts ---------------------------------------------------------------
 * Hand-rolled SVG rather than a charting library: it keeps the page dependency-free
 * and gives exact control over the mark specs -- 4px rounded data-ends anchored to the
 * baseline, a 2px surface gap between bars, recessive axes.
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

function svgRoot(host, height) {
  host.innerHTML = "";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 600 ${height}`);
  svg.setAttribute("height", height);
  host.appendChild(svg);
  return svg;
}

function node(svg, tag, attrs, text) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  svg.appendChild(n);
  return n;
}

const tip = $("tip");
function hoverable(mark, label) {
  mark.addEventListener("mousemove", (e) => {
    tip.textContent = label;
    tip.classList.add("on");
    tip.style.left = `${e.clientX + 14}px`;
    tip.style.top = `${e.clientY - 8}px`;
  });
  mark.addEventListener("mouseleave", () => tip.classList.remove("on"));
}

/* Sector allocation: magnitude, one hue, more-is-darker. Horizontal because sector
   names are long ("Pharma & Healthcare") and would never fit under vertical columns. */
function allocationChart(sectors) {
  const host = $("chart-allocation");
  if (!sectors.length) {
    host.innerHTML = '<p class="empty">No holdings yet.</p>';
    return;
  }
  const LABEL = 150;
  const VALUE = 52;
  const width = 600 - LABEL - VALUE;
  const height = sectors.length * ROW + 10;
  const svg = svgRoot(host, height);
  const max = Math.max(...sectors.map((s) => num(s.allocation_pct)));

  sectors.forEach((s, i) => {
    const y = i * ROW;
    const value = num(s.allocation_pct);
    const w = max > 0 ? (value / max) * width : 0;
    const colour = `var(${SEQ[Math.min(i, SEQ.length - 1)]})`;

    node(svg, "text", {
      x: LABEL - 10, y: y + ROW / 2 + 4, "text-anchor": "end", class: "tick",
    }, s.sector);

    const bar = node(svg, "path", {
      d: barPath(LABEL, y + GAP, w, ROW - GAP * 2 - 4, 1),
      fill: colour,
      class: "bar",
    });
    hoverable(bar, `${s.sector} — ${pct(s.allocation_pct)} of invested (${money(s.invested)}, ${s.stock_count} stock${s.stock_count === 1 ? "" : "s"})`);

    // Direct label on every bar: only a handful of sectors, so no clutter, and it
    // removes any need to read a value off an axis.
    node(svg, "text", {
      x: LABEL + w + 8, y: y + ROW / 2 + 4, class: "val",
    }, pct(s.allocation_pct));
  });
}

/* Profit/loss: polarity, so a diverging pair around a zero baseline. Blue for gain and
   red for loss rather than the conventional green/red -- under deuteranopia green and
   red measure dE 6.8, which is to say they are the same colour, and this is the one
   chart where that would matter. */
function pnlChart(sectors) {
  const host = $("chart-pnl");
  const priced = sectors.filter((s) => s.pnl !== null);
  if (!priced.length) {
    host.innerHTML = '<p class="empty">No prices available yet — hit Refresh.</p>';
    return;
  }
  const LABEL = 150;
  const width = 600 - LABEL - 60;
  const zero = LABEL + width / 2;
  const half = width / 2;
  const height = priced.length * ROW + 16;
  const svg = svgRoot(host, height);
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
    }, s.sector);

    const bar = node(svg, "path", {
      d: barPath(zero, y + GAP, w, ROW - GAP * 2 - 4, up ? 1 : -1),
      fill: up ? "var(--gain)" : "var(--loss)",
      class: "bar",
    });
    hoverable(bar, `${s.sector} — ${signed(s.pnl, 0)} (${signedPct(s.pnl_pct)})`);

    node(svg, "text", {
      x: up ? zero + w + 8 : zero - w - 8,
      y: y + ROW / 2 + 4,
      "text-anchor": up ? "start" : "end",
      class: "val",
    }, signedPct(s.pnl_pct));
  });
}

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
    const td = el("td", "empty", "Nothing here yet — upload a holdings file below.");
    td.colSpan = columns.length;
    empty.appendChild(td);
    tbody.appendChild(empty);
  }
  rows.forEach((row) => tbody.appendChild(cellsFor(row)));
  host.appendChild(tbody);
}

function meter(value) {
  const wrap = el("div", "meter");
  wrap.appendChild(el("span", null, pct(value)));
  const track = el("div", "track");
  const fill = el("div", "fill");
  fill.style.width = `${Math.min(100, num(value))}%`;
  track.appendChild(fill);
  wrap.appendChild(track);
  return wrap;
}

function stocksTable(view) {
  table(
    $("stocks"),
    ["Sno", "Stock", "Sector", "Units", "Invested", "Market value", "P/L", "P/L %", "Allocation %"],
    view.stocks,
    (r) => {
      const tr = el("tr");
      tr.appendChild(el("td", "dim", String(r.sno)));

      const name = el("td");
      name.appendChild(el("span", "ticker", r.ticker));
      name.appendChild(el("div", "dim", r.name));
      tr.appendChild(name);

      tr.appendChild(el("td", null, r.sector));
      tr.appendChild(el("td", null, r.units));
      tr.appendChild(el("td", null, money(r.invested, 2)));

      const mv = el("td");
      if (r.market_value === null) {
        mv.appendChild(el("span", "chip", "no price"));
      } else {
        mv.appendChild(el("span", null, money(r.market_value, 2)));
        if (r.stale_price) mv.appendChild(el("span", "chip", "stale"));
      }
      tr.appendChild(mv);

      tr.appendChild(el("td", cls(r.pnl), r.pnl === null ? "—" : signed(r.pnl, 2)));
      tr.appendChild(el("td", cls(r.pnl_pct), signedPct(r.pnl_pct)));

      const alloc = el("td");
      alloc.appendChild(meter(r.allocation_pct));
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
        mv.appendChild(el("span", "chip", `${r.unpriced_count} unpriced`));
      }
      tr.appendChild(mv);

      tr.appendChild(el("td", cls(r.pnl), r.pnl === null ? "—" : signed(r.pnl, 2)));
      tr.appendChild(el("td", cls(r.pnl_pct), signedPct(r.pnl_pct)));

      const alloc = el("td");
      alloc.appendChild(meter(r.allocation_pct));
      tr.appendChild(alloc);
      return tr;
    }
  );
}

/* --- KPI row: stat tiles, because these are headline numbers, not a chart --- */

function kpis(view) {
  const t = view.totals;
  const host = $("kpis");
  host.innerHTML = "";

  const tile = (label, value, sub, klass) => {
    const card = el("div", "card kpi");
    card.appendChild(el("div", "label", label));
    card.appendChild(el("div", `value ${klass || ""}`, value));
    card.appendChild(el("div", "sub", sub));
    host.appendChild(card);
  };

  tile("Invested", money(t.invested), `${t.stock_count} stocks · ${t.sector_count} sectors`);
  tile(
    "Market value",
    t.market_value === null ? "—" : money(t.market_value),
    view.unpriced.length ? `${view.unpriced.length} holding(s) unpriced` : "All holdings priced"
  );
  tile("Profit / loss", t.pnl === null ? "—" : signed(t.pnl), "Against cost basis", cls(t.pnl));
  tile("Return", signedPct(t.pnl_pct), "On priced holdings", cls(t.pnl_pct));
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

/* --- insights: empty until an agent writes a row --------------------------- */

async function insights() {
  const host = $("insights");
  const rows = await api(`/api/${S.market}/insights`);
  host.innerHTML = "";
  if (!rows.length) {
    host.appendChild(
      el("p", "empty",
        "No insights yet. This is where the analysis agents will report sector concentration, small-cap exposure and macro impact once they land.")
    );
    return;
  }
  rows.forEach((r) => {
    const b = el("div", `banner ${r.severity === "critical" ? "bad" : "warn"}`);
    const body = el("div");
    body.appendChild(el("strong", null, r.title));
    body.appendChild(el("div", null, r.body));
    b.appendChild(body);
    host.appendChild(b);
  });
}

/* --- upload / delete ------------------------------------------------------- */

function showErrors(detail) {
  const host = $("upload-result");
  host.innerHTML = "";
  const errors = detail?.errors;
  if (!errors) {
    host.appendChild(el("div", "banner bad", String(detail || "Something went wrong.")));
    return;
  }
  const list = el("ul", "errors");
  errors.forEach((e) => {
    const li = el("li");
    li.appendChild(el("code", null, `${e.sheet} row ${e.row} · ${e.column}`));
    li.appendChild(el("span", null, e.message));
    list.appendChild(li);
  });
  const b = el("div", "banner bad", "Nothing was saved — fix these and upload again.");
  host.appendChild(b);
  host.appendChild(list);
}

function showOk(message) {
  const host = $("upload-result");
  host.innerHTML = "";
  host.appendChild(el("div", "banner", message));
}

async function send(path, input, extra) {
  if (!input.files.length) {
    showErrors("Choose a file first.");
    return;
  }
  const body = new FormData();
  body.append("file", input.files[0]);
  if (extra) for (const [k, v] of Object.entries(extra)) body.append(k, v);

  try {
    const result = await api(path, { method: "POST", body });
    input.value = "";
    return result;
  } catch (e) {
    showErrors(e.detail || e.message);
  }
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
      load();
    };
    host.appendChild(b);
  });
}

async function load(refresh = false) {
  const button = $("refresh");
  button.disabled = true;
  button.textContent = refresh ? "Fetching…" : "Refresh prices";
  try {
    S.view = await api(`/api/${S.market}/dashboard${refresh ? "?refresh=true" : ""}`);
    marketToggle();
    $("market-label").textContent = `· ${S.view.label}`;
    notices(S.view);
    kpis(S.view);
    allocationChart(S.view.sectors);
    pnlChart(S.view.sectors);
    stocksTable(S.view);
    sectorsTable(S.view);
    await insights();
    $("stamp").textContent = `Prices as of ${new Date(S.view.generated_at).toLocaleString()} · allocation is computed on invested amount, not market value`;
  } catch (e) {
    if (e.message !== "Signed out") console.error(e);
  } finally {
    button.disabled = false;
    button.textContent = "Refresh prices";
  }
}

/* --- auth (only when AUTH_ENABLED) ---------------------------------------- */

function renderLogin() {
  document.querySelector(".wrap").innerHTML = "";
  const card = el("div", "card");
  card.style.maxWidth = "360px";
  card.style.margin = "80px auto";
  card.appendChild(el("h2", null, "Sign in"));

  const email = el("input");
  email.type = "email";
  email.placeholder = "you@example.com";
  const password = el("input");
  password.type = "password";
  password.placeholder = "Password";
  [email, password].forEach((i) => {
    i.style.cssText = "width:100%;padding:9px;margin-bottom:8px;border-radius:8px;border:1px solid var(--hairline);background:var(--plane);color:var(--ink);font:inherit";
    card.appendChild(i);
  });

  const go = el("button", "action", "Sign in");
  go.style.width = "100%";
  const error = el("p", "empty");
  go.onclick = async () => {
    const res = await fetch(`${S.config.supabase_url}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: S.config.supabase_anon_key },
      body: JSON.stringify({ email: email.value, password: password.value }),
    });
    const data = await res.json();
    if (!res.ok) {
      error.textContent = data.error_description || data.msg || "Sign-in failed.";
      return;
    }
    S.token = data.access_token;
    localStorage.setItem("token", S.token);
    location.reload();
  };
  card.appendChild(go);
  card.appendChild(error);
  document.querySelector(".wrap").appendChild(card);
}

/* --- boot ----------------------------------------------------------------- */

async function boot() {
  S.config = await (await fetch("/api/config")).json();
  if (S.config.auth_enabled && !S.token) {
    renderLogin();
    return;
  }
  S.markets = await api("/api/portfolio/markets");

  $("refresh").onclick = () => load(true);
  $("export").onclick = () => {
    window.location = `/api/${S.market}/export`;
  };
  $("upload-go").onclick = async () => {
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const result = await send("/api/portfolio/upload", $("upload-file"), { mode });
    if (result) {
      showOk(`Loaded ${result.transactions_added} holdings into ${result.markets.join(" and ")}.`);
      load();
    }
  };
  $("delete-go").onclick = async () => {
    const result = await send("/api/portfolio/delete", $("delete-file"));
    if (result) {
      const closed = result.removed.filter((r) => r.position_closed).length;
      showOk(`Removed ${result.removed.length} position(s)${closed ? `, ${closed} fully exited` : ""}.`);
      load();
    }
  };

  load();
}

boot();
