/* Hashmark front end. Plain JavaScript, no libraries.
   Every number comes from the Python server; this file only displays it. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ---------------------------------------------------------------- helpers
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const isNum = x => typeof x === "number" && isFinite(x);
const pct = (p, d = 1) => isNum(p) ? (p * 100).toFixed(d) + "%" : "n/a";
const pts = (x, d = 1) => isNum(x) ? (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) : "n/a";
const signed = (x, d = 1) => isNum(x) ? (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(d) : "n/a";
const american = x => isNum(x) ? (x > 0 ? "+" : "−") + Math.abs(Math.round(x)) : "n/a";
const cents = p => isNum(p) ? Math.round(p * 100) + "¢" : "n/a";
const clamp01 = x => Math.min(1, Math.max(0, x));

function spreadText(home, away, homeSpread) {
  if (!isNum(homeSpread)) return "n/a";
  if (Math.abs(homeSpread) < 0.05) return "PK";
  return `${homeSpread < 0 ? home : away} −${Math.abs(homeSpread).toFixed(1)}`;
}

function kickoff(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts.body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body),
  } : {});
  let data;
  try { data = await res.json(); } catch { throw new Error(`The server returned an unreadable reply (HTTP ${res.status}).`); }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked: fine */ } },
};

const FLAG_SVG = `<svg viewBox="0 0 12 14" aria-hidden="true"><path d="M1.5 1v12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M2 1.5h8l-2 3 2 3H2z" fill="currentColor"/></svg>`;
const chip = flag => flag ? `<span class="chip chip-flag">${FLAG_SVG}Flag</span>` : `<span class="chip chip-pass">Pass</span>`;

// ---------------------------------------------------------------- tooltip
const tip = $("#tip");
document.addEventListener("mousemove", e => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.hidden = true; return; }
  tip.innerHTML = t.dataset.tip;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
});
document.addEventListener("scroll", () => { tip.hidden = true; }, true);

// ---------------------------------------------------------------- strip
/* The "hashmark" strip: one 0–100% track with the model (circle), market
   consensus (bar), Kalshi price (diamond), and the ±10-point agreement band
   around the market that the FLAG rule requires. */
function strip(model, market, kalshi, kalshiLabel = "Kalshi") {
  const L = p => (clamp01(p) * 100).toFixed(2) + "%";
  let band = "";
  if (isNum(market)) {
    const lo = clamp01(market - 0.10), hi = clamp01(market + 0.10);
    band = `<div class="strip-band" style="left:${L(lo)};width:${((hi - lo) * 100).toFixed(2)}%"></div>`;
  }
  const lines = [`Model: ${pct(model)}`];
  if (isNum(market)) lines.push(`Market: ${pct(market)}`, `Gap: ${pts(model - market)} pts (limit ±10)`);
  if (isNum(kalshi)) lines.push(`${kalshiLabel}: ${cents(kalshi)}`);
  return `<div class="strip">
      <div class="strip-track"></div>${band}<div class="strip-mid"></div>
      ${isNum(market) ? `<div class="m m-market" style="left:${L(market)}"></div>` : ""}
      ${isNum(kalshi) ? `<div class="m m-kalshi" style="left:${L(kalshi)}"></div>` : ""}
      <div class="m m-model" style="left:${L(model)}"></div>
      <div class="strip-scale"><span>0%</span><span>50%</span><span>100%</span></div>
      <div class="strip-hit" data-tip="${esc(lines.join("<br>"))}"></div>
    </div>
    <div class="strip-vals"><span>Model <b>${pct(model)}</b></span><span>Market <b>${pct(market)}</b></span>${isNum(kalshi) ? `<span>${esc(kalshiLabel)} <b>${cents(kalshi)}</b></span>` : ""}</div>`;
}

const LEGEND = `<div class="legend" aria-label="Legend">
  <span><i class="mk mk-model"></i>Model win %</span>
  <span><i class="mk mk-market"></i>Market consensus (no-vig)</span>
  <span><i class="mk mk-kalshi"></i>Kalshi YES ask (home)</span>
  <span><i class="mk mk-band"></i>Within 10 pts of market</span>
</div>`;

// ---------------------------------------------------------------- status
let STATUS = null;
async function loadStatus() {
  try { STATUS = await api("/api/status"); } catch { STATUS = null; }
  const el = $("#status");
  if (!STATUS) { el.innerHTML = `<span class="pill missing"><i class="dot"></i>Server unreachable</span>`; return; }
  const p = (ok, label, title) => `<span class="pill ${ok ? "ok" : "missing"}" title="${esc(title)}"><i class="dot"></i>${label}</span>`;
  el.innerHTML = p(STATUS.cfbd_key, "CFBD key", STATUS.cfbd_key ? "CFBD_API_KEY is set" : "CFBD_API_KEY is missing")
    + p(STATUS.odds_key, "Odds API key", STATUS.odds_key ? "ODDS_API_KEY is set" : "ODDS_API_KEY is missing")
    + `<span class="pill ok" title="Kalshi public data needs no key"><i class="dot"></i>Kalshi public</span>`
    + `<span class="pill" title="Optional hand-entered injuries"><i class="dot"></i>injuries.csv ${STATUS.injuries_file ? "loaded" : "none"}</span>`;
}

function setupNotice(message) {
  const keyMissing = /is not set/.test(message);
  if (keyMissing) {
    return `<div class="notice warn">
      <h3>Add your API keys to see live games</h3>
      <p>${esc(message)}</p>
      <ol>
        <li>Get a free key at <a href="https://collegefootballdata.com/key" target="_blank" rel="noopener">collegefootballdata.com</a> and another at <a href="https://the-odds-api.com" target="_blank" rel="noopener">the-odds-api.com</a>.</li>
        <li>Copy <code>.env.example</code> to <code>.env</code> in the project folder and paste both keys after the <code>=</code> signs.</li>
        <li>Stop the server (Ctrl+C) and run <code>python main.py serve</code> again.</li>
      </ol>
      <div class="row"><button class="btn" data-goto="explain">Try the calculator meanwhile</button></div>
    </div>`;
  }
  return `<div class="notice bad">
    <h3>Couldn't load data</h3>
    <p>${esc(message)}</p>
    <p class="label">The rule is to stop after two failed requests, so nothing was retried. Check your network and keys, then refresh.</p>
  </div>`;
}

// ---------------------------------------------------------------- week
let WEEK = null, WEEK_ERR = null, weekPromise = null;

function loadWeek(force = false) {
  if (weekPromise && !force) return weekPromise;
  WEEK = null; WEEK_ERR = null; LEGS = null; PARLAY_RESULT = null; PARLAY_ERR = null;
  renderWeek();
  weekPromise = api("/api/week" + (force ? "?refresh=1" : "")).then(d => { WEEK = d; }).catch(e => { WEEK_ERR = e.message; }).finally(() => {
    renderWeek();
    if (!$("#view-parlay").hidden) renderParlay();
  });
  return weekPromise;
}

function renderWeek() {
  const body = $("#week-body");
  if (WEEK_ERR) { body.innerHTML = setupNotice(WEEK_ERR); return; }
  if (!WEEK) {
    body.innerHTML = `<p class="label" style="margin-bottom:10px">Loading games, odds, and Kalshi prices…</p><div class="games">${"<div class=\"skeleton\"></div>".repeat(5)}</div>`;
    return;
  }
  const { week, rows, generated } = WEEK;
  $("#week-title").textContent = `Week ${week.week}${week.season_type === "postseason" ? " · Postseason" : ""}`;
  const flags = rows.filter(r => r.flag).length;
  const withKalshi = rows.filter(r => r.kalshi).length;
  const withOdds = rows.filter(r => r.market).length;
  const onlyFlags = $("#flags-only").checked;
  const shown = onlyFlags ? rows.filter(r => r.flag) : rows;

  const summary = `<div class="summary">
    <div><div class="label">Games</div><div class="big">${rows.length}</div><div class="small">Upcoming, at least one FBS team</div></div>
    <div><div class="label">Flags</div><div class="big flag-count">${flags}</div><div class="small">Edge ≥ 3 pts and gap ≤ 10 pts</div></div>
    <div><div class="label">Kalshi markets</div><div class="big">${withKalshi}<span class="label"> / ${rows.length}</span></div><div class="small">Others show "no market"</div></div>
    <div><div class="label">Sportsbook odds</div><div class="big">${withOdds}<span class="label"> / ${rows.length}</span></div><div class="small">Board built ${esc(generated)}</div></div>
  </div>`;

  if (!shown.length) {
    body.innerHTML = summary + `<div class="games"><div class="empty">${onlyFlags ? "No games are flagged this week. That's normal: the rule is strict on purpose." : "No upcoming games found for this week."}</div></div>`;
    return;
  }

  body.innerHTML = summary + LEGEND + `<div class="games">${shown.map(gameRow).join("")}</div>`;
}

function gameRow(r) {
  const g = r.game, p = r.pred, m = r.market, k = r.kalshi, b = r.best;
  const kalshiHome = k && k.home ? k.home.yes_ask : null;
  let edgeLine;
  if (!k) edgeLine = "Kalshi: no market";
  else if (!b) edgeLine = "Kalshi: no ask right now";
  else edgeLine = `Edge <b>${pts(b.edge)} pts</b> on ${esc(b.team)} at ${cents(b.price)}`;
  const inj = p.injury_adj ? `<span class="tag" title="injuries.csv adjustment">inj ${signed(p.injury_adj)}</span>` : "";
  const id = `src-${esc(g.id)}`;
  return `<article class="game ${r.flag ? "is-flag" : ""}">
    <div class="matchup">
      <div class="teams">${esc(g.away)}<span class="at">@</span>${esc(g.home)}</div>
      <div class="meta"><span>${esc(kickoff(g.start))}</span>${g.neutral ? `<span class="tag">Neutral</span>` : ""}${inj}<span class="mono">ID ${esc(g.id)}</span></div>
    </div>
    <div class="spreads">
      <span class="k">Model</span><span class="v">${esc(spreadText(g.home, g.away, p.home_spread))}</span>
      <span class="k">Market</span><span class="v">${esc(spreadText(g.home, g.away, m ? m.home_spread : null))}</span>
    </div>
    <div class="strip-cell">${strip(p.home_prob, m ? m.home_prob : null, kalshiHome)}</div>
    <div class="verdict">${chip(r.flag)}<div class="edge">${edgeLine}</div></div>
    <button class="src-toggle" aria-expanded="false" aria-controls="${id}">Sources &amp; timestamps</button>
    <div class="sources" id="${id}" hidden>
      <div><span class="label">Model spread &amp; win %</span>${esc(r.sources.model)}</div>
      <div><span class="label">Market spread &amp; win %</span>${esc(r.sources.market)}</div>
      <div><span class="label">Kalshi price</span>${esc(r.sources.kalshi)}</div>
    </div>
  </article>`;
}

$("#week-body").addEventListener("click", e => {
  const t = e.target.closest(".src-toggle");
  if (!t) return;
  const box = document.getElementById(t.getAttribute("aria-controls"));
  const open = t.getAttribute("aria-expanded") === "true";
  t.setAttribute("aria-expanded", String(!open));
  box.hidden = open;
});
$("#flags-only").addEventListener("change", renderWeek);
$("#week-refresh").addEventListener("click", () => loadWeek(true));

// ---------------------------------------------------------------- backtest
let btTimer = null;

async function loadBacktest(start = false) {
  let s;
  try { s = await api("/api/backtest" + (start ? "?start=1" : "")); }
  catch (e) { $("#bt-body").innerHTML = setupNotice(e.message); return; }
  renderBacktest(s);
  clearTimeout(btTimer);
  if (s.state === "running") btTimer = setTimeout(() => loadBacktest(false), 2000);
}

function renderBacktest(s) {
  const body = $("#bt-body"), btn = $("#bt-run");
  btn.disabled = s.state === "running";
  btn.textContent = s.state === "done" ? "Run again" : s.state === "running" ? "Running…" : "Run backtest";
  if (s.state === "idle") {
    body.innerHTML = `<div class="panel">
      <h3>What you'll get</h3>
      <p class="lede">Straight-up accuracy, Brier score, log loss, against-the-spread record vs. closing lines, a calibration chart, and a plain verdict on whether the model beats the closing-line market. The first run downloads five seasons from CFBD and can take a few minutes. After that it reads from the cache.</p>
    </div>`;
    return;
  }
  if (s.state === "running") {
    body.innerHTML = `<div class="panel"><h3>Replaying 2021–2025, week by week</h3><p class="lede">Started ${esc(s.started)}. Each week is predicted before its results are learned.</p><div class="progress"><span></span></div></div>`;
    return;
  }
  if (s.state === "error") { body.innerHTML = setupNotice(s.error); return; }

  const r = s.result, all = r.seasons.find(x => x.label === "ALL"), vm = r.vs_market;
  const verdictBox = `<div class="notice ${vm && vm.model_worse ? "warn" : ""}"><h3>${vm ? (vm.model_worse ? "The market wins" : "Model ahead of the market") : "No market comparison"}</h3><p>${esc(r.verdict)}</p></div>`;
  const ats = all.ats;
  const tiles = `<div class="tiles">
    <div class="tile"><div class="label">Straight-up accuracy</div><div class="big">${pct(all.accuracy)}</div>
      <div class="vs">${vm ? `Market ${pct(vm.market.accuracy)} on ${vm.games} games with moneylines` : ""}</div>
      <div class="note">Share of games where the team given over 50% won.</div></div>
    <div class="tile"><div class="label">Brier score</div><div class="big">${all.brier.toFixed(4)}</div>
      <div class="vs">${vm ? `Model ${vm.model.brier.toFixed(4)} vs market ${vm.market.brier.toFixed(4)} (same games)` : ""}</div>
      <div class="note">Average squared miss. 0 is perfect; 0.25 is a coin flip. Lower is better.</div></div>
    <div class="tile"><div class="label">Against the spread</div><div class="big">${ats.pct == null ? "n/a" : pct(ats.pct)}</div>
      <div class="vs">${ats.w}–${ats.l}–${ats.p} (W–L–push) vs closing line</div>
      <div class="note">You need about 52.4% to break even at −110.</div></div>
  </div>`;
  const seasonTable = `<div class="table-wrap"><table>
    <thead><tr><th>Season</th><th>Games</th><th>SU acc.</th><th>Brier</th><th>Log loss</th><th>ATS</th></tr></thead>
    <tbody>${r.seasons.map(x => `<tr class="${x.label === "ALL" ? "total" : ""}"><td>${esc(x.label)}</td><td>${x.games}</td><td>${pct(x.accuracy)}</td><td>${x.brier.toFixed(4)}</td><td>${x.log_loss.toFixed(4)}</td><td>${x.ats.w}–${x.ats.l}–${x.ats.p}${x.ats.pct == null ? "" : ` (${pct(x.ats.pct)})`}</td></tr>`).join("")}</tbody>
  </table></div>`;
  const vmTable = vm ? `<div class="table-wrap" style="margin-top:14px"><table>
    <thead><tr><th>Head to head (${vm.games} games)</th><th>SU acc.</th><th>Brier</th><th>Log loss</th></tr></thead>
    <tbody>
      <tr><td>Model</td><td>${pct(vm.model.accuracy)}</td><td>${vm.model.brier.toFixed(4)}</td><td>${vm.model.log_loss.toFixed(4)}</td></tr>
      <tr><td>Closing market (no-vig)</td><td>${pct(vm.market.accuracy)}</td><td>${vm.market.brier.toFixed(4)}</td><td>${vm.market.log_loss.toFixed(4)}</td></tr>
    </tbody></table></div>` : "";
  const calTable = `<div class="table-wrap" style="margin-top:14px"><table>
    <thead><tr><th>Predicted</th><th>Games</th><th>Avg predicted</th><th>Actual</th><th>Gap</th></tr></thead>
    <tbody>${r.calibration.map(b => `<tr><td>${Math.round(b.lo * 100)}–${Math.round(b.hi * 100)}%</td><td>${b.games}</td><td>${pct(b.avg_pred)}</td><td>${pct(b.actual)}</td><td>${signed((b.actual - b.avg_pred) * 100)}</td></tr>`).join("")}</tbody>
  </table></div>`;

  body.innerHTML = verdictBox + tiles + `<div class="two-col">
      <div><h3 style="margin-bottom:10px">By season</h3>${seasonTable}${vmTable}</div>
      <div><h3 style="margin-bottom:4px">Calibration</h3><p class="label" style="margin-bottom:8px;text-transform:none;letter-spacing:0">Dots on the dashed line mean "70% predicted" really won about 70% of the time.</p>
        <div class="panel" style="padding:10px">${calibrationChart(r.calibration)}</div>${calTable}</div>
    </div>`;
}

function calibrationChart(bins) {
  const W = 480, H = 340, ml = 48, mr = 14, mt = 14, mb = 46;
  const x = v => ml + v * (W - ml - mr), y = v => mt + (1 - v) * (H - mt - mb);
  const ticks = [0, 0.2, 0.4, 0.6, 0.8, 1];
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Calibration chart: predicted vs actual home win rate">`;
  for (const t of ticks) {
    s += `<line class="grid" x1="${x(0)}" x2="${x(1)}" y1="${y(t)}" y2="${y(t)}"/>`;
    s += `<text x="${ml - 8}" y="${y(t) + 4}" text-anchor="end">${t * 100}%</text>`;
    s += `<text x="${x(t)}" y="${H - mb + 18}" text-anchor="middle">${t * 100}%</text>`;
  }
  s += `<line class="diag" x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}"/>`;
  s += `<text x="${x(0.78) + 8}" y="${y(0.78) + 16}" text-anchor="start">perfect</text>`;
  if (bins.length) {
    s += `<polyline class="series" points="${bins.map(b => `${x(b.avg_pred).toFixed(1)},${y(b.actual).toFixed(1)}`).join(" ")}"/>`;
    for (const b of bins) {
      const tipText = esc(`${Math.round(b.lo * 100)}–${Math.round(b.hi * 100)}% bucket<br>${b.games} games<br>Predicted ${pct(b.avg_pred)}<br>Actual ${pct(b.actual)}`);
      s += `<circle class="pt" cx="${x(b.avg_pred)}" cy="${y(b.actual)}" r="5"/>`;
      s += `<circle class="pt-hit" cx="${x(b.avg_pred)}" cy="${y(b.actual)}" r="14" data-tip="${tipText}"/>`;
    }
  }
  s += `<text class="axis-title" x="${(x(0) + x(1)) / 2}" y="${H - 6}" text-anchor="middle">Predicted home win %</text>`;
  s += `<text class="axis-title" transform="translate(12 ${(y(0) + y(1)) / 2}) rotate(-90)" text-anchor="middle">Actual home win %</text>`;
  return s + "</svg>";
}

$("#bt-run").addEventListener("click", () => loadBacktest(true));

// ---------------------------------------------------------------- parlay
let LEGS = null, PARLAY_RESULT = null, PARLAY_ERR = null;

function renderParlay() {
  const body = $("#parlay-body");
  if (WEEK_ERR) { body.innerHTML = setupNotice(WEEK_ERR); return; }
  if (!WEEK) { body.innerHTML = `<div class="panel"><p class="label">Loading this week's games…</p><div class="progress"><span></span></div></div>`; loadWeek(); return; }
  const rows = WEEK.rows;
  if (rows.length < 2) { body.innerHTML = `<div class="panel empty">A parlay needs at least two games this week.</div>`; return; }
  if (!LEGS) {
    LEGS = rows.slice(0, 2).map(r => ({ gid: String(r.game.id), team: r.pred.home_prob >= 0.5 ? r.game.home : r.game.away }));
    priceParlay();
  }
  const opt = gid => rows.map(r => `<option value="${esc(r.game.id)}" ${String(r.game.id) === gid ? "selected" : ""}>${esc(r.game.away)} @ ${esc(r.game.home)}</option>`).join("");
  const legHtml = LEGS.map((l, i) => {
    const r = rows.find(x => String(x.game.id) === l.gid) || rows[0];
    const btn = team => `<button type="button" data-leg="${i}" data-team="${esc(team)}" aria-pressed="${team === l.team}">${esc(team)}</button>`;
    return `<div class="leg">
      <span class="n">${i + 1}</span>
      <select id="leg-game-${i}" data-leg="${i}" aria-label="Game for leg ${i + 1}">${opt(l.gid)}</select>
      <div class="seg" role="group" aria-label="Team for leg ${i + 1}">${btn(r.game.away)}${btn(r.game.home)}</div>
      ${LEGS.length > 2 ? `<button type="button" class="icon-btn" data-remove="${i}" aria-label="Remove leg ${i + 1}">×</button>` : "<span></span>"}
    </div>`;
  }).join("");

  body.innerHTML = `<div class="panel">
      <div class="legs">${legHtml}</div>
      <div class="parlay-form-foot">
        ${LEGS.length < 3 ? `<button type="button" class="btn" id="leg-add">+ Add a third leg</button>` : ""}
        <label class="field inline"><span class="label">Your book's price (optional)</span><input id="parlay-offered" type="text" inputmode="numeric" placeholder="e.g. +260" value="${esc(store.get("parlay-offered") || "")}"></label>
        <button type="button" class="btn btn-primary" id="parlay-go">Price parlay</button>
      </div>
    </div>
    <div id="parlay-result">${parlayResult()}</div>`;
}

function parlayResult() {
  if (PARLAY_ERR) return `<div class="notice bad" style="margin-top:16px"><h3>Can't price this parlay</h3><p>${esc(PARLAY_ERR)}</p></div>`;
  const r = PARLAY_RESULT;
  if (!r) return "";
  const o = r.offered, m = r.model, k = r.market;
  const warn = r.warning ? `<div class="notice warn" style="margin-top:16px"><h3>Correlated legs</h3><p>${esc(r.warning)}</p></div>` : "";
  return warn + `<div class="price-board">
      <div class="tile"><div class="label">Model fair price</div><div class="big">${american(m.american)}</div><div class="vs">${pct(m.prob)} to hit · decimal ${m.decimal.toFixed(2)}</div></div>
      <div class="tile"><div class="label">Market fair price</div><div class="big">${k ? american(k.american) : "n/a"}</div><div class="vs">${k ? `${pct(k.prob)} · from no-vig consensus` : "Needs moneylines on every leg"}</div></div>
      <div class="tile"><div class="label">Offered</div><div class="big">${o ? american(o.american) : "n/a"}</div>
        <div class="vs">${o ? `implies ${pct(o.implied)} · ${esc(o.how)}` : "Enter your book's price above"}</div>
        ${o ? `<div class="note">Model EV per $1: <b class="mono ${o.ev >= 0 ? "ev-pos" : "ev-neg"}">${o.ev >= 0 ? "+" : "−"}$${Math.abs(o.ev).toFixed(3)}</b></div>` : ""}</div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>Leg</th><th>Game</th><th>Model win %</th><th>Market win %</th><th>Avg decimal odds</th></tr></thead>
      <tbody>${r.legs.map(l => `<tr><td>${esc(l.team)}</td><td>${esc(l.matchup)}</td><td>${pct(l.p_model)}</td><td>${pct(l.p_market)}</td><td>${isNum(l.decimal) ? l.decimal.toFixed(3) : "n/a"}</td></tr>`).join("")}</tbody>
    </table></div>
    <p class="label" style="margin-top:10px;text-transform:none;letter-spacing:0">Model = CFBD-based ratings; market and book odds = The Odds API. A positive EV here is only as good as the model, and the backtest shows how good that is.</p>`;
}

async function priceParlay() {
  const raw = (store.get("parlay-offered") || "").replace(/[^\d+\-.]/g, "");
  try {
    PARLAY_RESULT = await api("/api/parlay", { body: { legs: LEGS.map(l => ({ game_id: l.gid, team: l.team })), offered: raw || null } });
    PARLAY_ERR = null;
  } catch (e) { PARLAY_RESULT = null; PARLAY_ERR = e.message; }
  const box = $("#parlay-result");
  if (box) box.innerHTML = parlayResult();
}

$("#parlay-body").addEventListener("click", e => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.team !== undefined) { LEGS[+t.dataset.leg].team = t.dataset.team; renderParlay(); priceParlay(); }
  else if (t.dataset.remove !== undefined) { LEGS.splice(+t.dataset.remove, 1); renderParlay(); priceParlay(); }
  else if (t.id === "leg-add") {
    const used = new Set(LEGS.map(l => l.gid));
    const r = WEEK.rows.find(x => !used.has(String(x.game.id))) || WEEK.rows[0];
    LEGS.push({ gid: String(r.game.id), team: r.game.home }); renderParlay(); priceParlay();
  } else if (t.id === "parlay-go") { priceParlay(); }
});
$("#parlay-body").addEventListener("change", e => {
  const s = e.target.closest("select[data-leg]");
  if (s) {
    const r = WEEK.rows.find(x => String(x.game.id) === s.value);
    LEGS[+s.dataset.leg] = { gid: s.value, team: r.game.home };
    renderParlay(); priceParlay();
  }
});
$("#parlay-body").addEventListener("input", e => {
  if (e.target.id === "parlay-offered") store.set("parlay-offered", e.target.value);
});

// ---------------------------------------------------------------- explain
const EX_FIELDS = [
  { legend: "Teams", fields: [["home", "Home team", "text"], ["away", "Away team", "text"]], extra: `<label class="toggle"><input type="checkbox" id="ex-neutral" name="neutral"> Neutral site (no home field)</label>` },
  { legend: "Elo ratings", fields: [["elo_home", "Home Elo", "number", 1], ["elo_away", "Away Elo", "number", 1]], hint: "1500 is an average FBS team." },
  { legend: "Opponent-adjusted EPA / play", fields: [["home_off", "Home offense", "number", 0.01], ["home_def", "Home defense", "number", 0.01], ["away_off", "Away offense", "number", 0.01], ["away_def", "Away defense", "number", 0.01]], hint: "Offense: higher is better. Defense is EPA allowed: lower is better." },
  { legend: "Games played", fields: [["games", "Fewest games either team has played", "number", 1]], hint: "EPA reaches full weight (40%) after 6 games." },
  { legend: "Injuries (points lost)", fields: [["injury_home", "Home", "number", 0.5], ["injury_away", "Away", "number", 0.5]], hint: "impact_points × status weight from injuries.csv." },
  { legend: "Sportsbook moneylines", fields: [["b0h", "Book A home", "number", 5], ["b0a", "Book A away", "number", 5], ["b1h", "Book B home", "number", 5], ["b1a", "Book B away", "number", 5], ["b2h", "Book C home", "number", 5], ["b2a", "Book C away", "number", 5]], hint: "American odds. Leave a book blank to skip it." },
  { legend: "Kalshi YES ask ($)", fields: [["kalshi_home", "Home", "number", 0.01], ["kalshi_away", "Away", "number", 0.01]] },
];
let EX_DEFAULT = null, exTimer = null;

function buildExForm(x) {
  const val = k => {
    const m = /^b(\d)([ha])$/.exec(k);
    if (m) { const b = x.books[+m[1]]; return b ? b[m[2] === "h" ? 0 : 1] : ""; }
    return x[k];
  };
  $("#ex-form").innerHTML = `<p class="label">Example inputs (made up)</p>` + EX_FIELDS.map(g => `<fieldset>
      <legend class="label">${esc(g.legend)}</legend>
      <div class="${g.fields.length > 1 ? "pair" : ""}">${g.fields.map(([k, label, type, step]) => `<label>${esc(label)}<input id="ex-${k}" name="${k}" type="${type}" ${step ? `step="${step}"` : ""} value="${esc(val(k))}"></label>`).join("")}</div>
      ${g.extra || ""}${g.hint ? `<span class="hint">${esc(g.hint)}</span>` : ""}
    </fieldset>`).join("");
  $("#ex-neutral").checked = !!x.neutral;
}

function readExForm() {
  const f = $("#ex-form"), n = k => { const v = f.elements[k].value; return v === "" ? null : Number(v); };
  const books = [0, 1, 2].map(i => [n(`b${i}h`), n(`b${i}a`)]).filter(([h, a]) => h !== null && a !== null && h !== 0 && a !== 0 && Math.abs(h) >= 100 && Math.abs(a) >= 100);
  return {
    home: f.elements.home.value.trim() || "Home", away: f.elements.away.value.trim() || "Away", neutral: f.elements.neutral.checked,
    elo_home: n("elo_home") ?? 1500, elo_away: n("elo_away") ?? 1500,
    home_off: n("home_off") ?? 0, home_def: n("home_def") ?? 0, away_off: n("away_off") ?? 0, away_def: n("away_def") ?? 0,
    games: Math.max(0, Math.round(n("games") ?? 0)), injury_home: n("injury_home") ?? 0, injury_away: n("injury_away") ?? 0,
    books, kalshi_home: n("kalshi_home"), kalshi_away: n("kalshi_away"),
  };
}

async function runExplain() {
  try { renderSteps(await api("/api/explain", { body: readExForm() })); }
  catch (e) { $("#ex-steps").innerHTML = `<div class="notice bad"><h3>Can't calculate</h3><p>${esc(e.message)}</p></div>`; }
}

function renderSteps(d) {
  const x = d.inputs, p = d.pred, H = esc(d.home), A = esc(d.away);
  const fmt = (v, n = 2) => (v < 0 ? "−" : "") + Math.abs(v).toFixed(n);
  const leader = v => v >= 0 ? `${H} by ${Math.abs(v).toFixed(1)}` : `${A} by ${Math.abs(v).toFixed(1)}`;
  const hfa = p.hfa ? ` + ${p.hfa} home field` : " (neutral site)";
  const steps = [];

  steps.push(["Elo margin", `(${Math.round(p.elo_home)} − ${Math.round(p.elo_away)}) / 25${hfa}`, `<b>${leader(p.elo_margin)}</b> points`]);

  if (p.epa_margin === null) {
    steps.push(["EPA margin", "No games played yet, so EPA is not used.", "Weight <b>0</b>"]);
  } else {
    steps.push(["EPA margin", `65 plays × [(${fmt(x.home_off)} + ${fmt(x.away_def)}) − (${fmt(x.away_off)} + ${fmt(x.home_def)})]${hfa}`, `<b>${leader(p.epa_margin)}</b> points`]);
  }

  const blendF = p.epa_margin === null ? `margin = Elo margin ${fmt(p.elo_margin)}` :
    `weight = 0.4 × min(${x.games}, 6) / 6 = ${p.epa_weight.toFixed(2)} → ${(1 - p.epa_weight).toFixed(2)} × ${fmt(p.elo_margin)} + ${p.epa_weight.toFixed(2)} × ${fmt(p.epa_margin)}`;
  const injF = p.injury_adj ? ` · injuries ${signed(p.injury_adj)} (away lost ${x.injury_away}, home lost ${x.injury_home})` : "";
  steps.push(["Predicted spread", blendF + injF, `Margin <b>${fmt(p.margin)}</b> → <b>${esc(spreadText(d.home, d.away, p.home_spread))}</b>`]);

  steps.push(["Win probability", `1 / (1 + 10^(−${fmt(p.margin)} × 25 / 400))`, `${H} wins <b>${pct(p.home_prob)}</b> · ${A} <b>${pct(1 - p.home_prob)}</b>`]);

  if (d.books.length) {
    const t = `<div class="table-wrap"><table><thead><tr><th>Book</th><th>Home ML</th><th>Away ML</th><th>Implied H</th><th>Implied A</th><th>Sum</th><th>No-vig H</th></tr></thead><tbody>${d.books.map((b, i) => `<tr><td>${"ABC"[i]}</td><td>${american(b.home_ml)}</td><td>${american(b.away_ml)}</td><td>${pct(b.implied_home)}</td><td>${pct(b.implied_away)}</td><td>${pct(b.overround)}</td><td>${pct(b.fair_home)}</td></tr>`).join("")}</tbody></table></div>`;
    steps.push(["Market consensus", "Each side's implied % ÷ the pair's sum removes the vig; then average across books.", `Consensus: ${H} <b>${pct(d.consensus)}</b>${t}`]);
  } else {
    steps.push(["Market consensus", "Enter at least one book's moneylines.", "<b>n/a</b>"]);
  }

  if (d.kalshi.length) {
    const t = `<div class="table-wrap"><table><thead><tr><th>Side</th><th>YES ask</th><th>Fee 0.07·P·(1−P)</th><th>Model</th><th>Edge after fee</th></tr></thead><tbody>${d.kalshi.map(s => `<tr><td>${esc(s.team)}</td><td>${cents(s.price)}</td><td>$${s.fee.toFixed(4)}</td><td>${pct(s.model)}</td><td>${pts(s.edge)} pts</td></tr>`).join("")}</tbody></table></div>`;
    steps.push(["Kalshi edge", "edge = model probability − price − fee", `Best side: <b>${esc(d.best.team)} ${pts(d.best.edge)} pts</b>${t}`]);
  } else {
    steps.push(["Kalshi edge", "Enter a Kalshi price between $0.01 and $0.99.", "<b>no market</b>"]);
  }

  const b = d.best;
  let rule;
  if (!b) rule = `<div class="checks"><div class="check"><span class="no-ok">✕</span>No Kalshi price, so the game is a PASS.</div></div>${chip(false)}`;
  else {
    const gap = isNum(b.market) ? b.model - b.market : null;
    const c1 = b.edge >= 0.03, c2 = gap !== null && Math.abs(gap) <= 0.10;
    rule = `<div class="checks">
      <div class="check"><span class="${c1 ? "ok" : "no-ok"}">${c1 ? "✓" : "✕"}</span>Edge ${pts(b.edge)} pts ${c1 ? "≥" : "<"} 3 pts</div>
      <div class="check"><span class="${c2 ? "ok" : "no-ok"}">${c2 ? "✓" : "✕"}</span>${gap === null ? "No market consensus to compare against" : `Model vs market gap ${pts(gap)} pts ${c2 ? "is within" : "is outside"} ±10`}</div>
    </div>
    <div style="margin-top:6px">${chip(b.flag)}</div>
    ${!c2 && gap !== null ? `<p class="lede" style="margin-top:6px">A gap this big usually means the market knows something the model doesn't (injuries, weather, a QB change). If you just added an injury the market already priced in, you counted it twice.</p>` : ""}`;
  }
  steps.push(["Rule check", "FLAG only when edge ≥ 3 pts AND model within 10 pts of market", rule + `<div style="margin-top:12px">${LEGEND}${strip(p.home_prob, d.consensus, d.kalshi.find(s => s.side === "home")?.price ?? null)}</div>`]);

  $("#ex-steps").innerHTML = steps.map(([title, formula, result], i) => `<div class="step">
      <div class="no">${i + 1}</div><h3>${title}</h3><div class="formula">${formula}</div><div class="result">${result}</div>
    </div>`).join("");
}

async function initExplain() {
  if (EX_DEFAULT) return;
  try {
    const d = await api("/api/example");
    EX_DEFAULT = d.inputs;
    buildExForm(EX_DEFAULT);
    renderSteps(d);
  } catch (e) { $("#ex-steps").innerHTML = `<div class="notice bad"><h3>Can't reach the server</h3><p>${esc(e.message)}</p></div>`; }
}
$("#ex-form").addEventListener("input", () => { clearTimeout(exTimer); exTimer = setTimeout(runExplain, 180); });
$("#ex-form").addEventListener("submit", e => e.preventDefault());
$("#ex-reset").addEventListener("click", () => { if (EX_DEFAULT) { buildExForm(EX_DEFAULT); runExplain(); } });

// ---------------------------------------------------------------- tabs
const TABS = ["week", "backtest", "parlay", "explain"];
function setTab(name) {
  if (!TABS.includes(name)) name = "week";
  for (const t of TABS) {
    $(`#tab-${t}`).setAttribute("aria-selected", String(t === name));
    $(`#view-${t}`).hidden = t !== name;
  }
  store.set("tab", name);
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "week") loadWeek();
  if (name === "backtest") loadBacktest(false);
  if (name === "parlay") renderParlay();
  if (name === "explain") initExplain();
}
$(".tabs").addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); });
$(".tabs").addEventListener("keydown", e => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const i = TABS.indexOf(store.get("tab") || "week");
  const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
  setTab(next); $(`#tab-${next}`).focus();
});
document.addEventListener("click", e => { const g = e.target.closest("[data-goto]"); if (g) setTab(g.dataset.goto); });
addEventListener("hashchange", () => setTab(location.hash.slice(1)));

(async function boot() {
  await loadStatus();
  const wanted = location.hash.slice(1) || store.get("tab");
  const keysReady = STATUS && STATUS.cfbd_key && STATUS.odds_key;
  setTab(TABS.includes(wanted) ? wanted : (keysReady ? "week" : "explain"));
})();
