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
  return d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";
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

// ---------------------------------------------------------------- sport
let SPORT = store.get("sport") === "cfb" ? "cfb" : "nfl";
const withSport = path => path + (path.includes("?") ? "&" : "?") + "sport=" + SPORT;

// One compact box for "this live source is offline" notes instead of a stack.
function notesBox(notes) {
  if (!notes || !notes.length) return "";
  const plain = n => {
    if (/prop prices/i.test(n)) return /not set/.test(n) ? "Prop prices: no ODDS_API_KEY yet, so projections only." : "Prop prices: couldn't reach The Odds API, so projections only.";
    if (/sportsbook odds/i.test(n)) return /not set/.test(n) ? "Live sportsbook odds: no ODDS_API_KEY yet, so markets use the nflverse consensus line." : "Live sportsbook odds: couldn't reach The Odds API, so markets use the nflverse consensus line.";
    if (/kalshi/i.test(n)) return "Kalshi: couldn't connect, so there are no Kalshi prices or edges.";
    if (/weather/i.test(n)) return "Weather forecast: couldn't connect, so no wind adjustment to totals.";
    return n;
  };
  return `<div class="notice warn notes-box"><h3>Some live data is offline</h3><ul>${notes.map(n => `<li title="${esc(n)}">${esc(plain(n))}</li>`).join("")}</ul></div>`;
}

// ---------------------------------------------------------------- team badges
// Approximate team colors (decoration only). Very dark primaries use a brighter
// secondary so the badge reads on the night background.
const TEAM_COLORS = {
  ARI: "#97233F", ATL: "#A71930", BAL: "#241773", BUF: "#00338D", CAR: "#0085CA", CHI: "#C83803",
  CIN: "#FB4F14", CLE: "#FF3C00", DAL: "#003594", DEN: "#FB4F14", DET: "#0076B6", GB: "#203731",
  HOU: "#A71930", IND: "#002C5F", JAX: "#006778", KC: "#E31837", LV: "#A5ACAF", LAC: "#0080C6",
  LA: "#003594", MIA: "#008E97", MIN: "#4F2683", NE: "#C60C30", NO: "#D3BC8D", NYG: "#0B2265",
  NYJ: "#125740", PHI: "#004C54", PIT: "#FFB612", SF: "#AA0000", SEA: "#69BE28", TB: "#D50A0A",
  TEN: "#4B92DB", WAS: "#5A1414",
};
function textOn(hex) {
  const n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#111" : "#fff";
}
function teamBadge(code, name, small = false) {
  const label = code || (name || "?").split(/\s+/).map(w => w[0]).join("").slice(0, 3).toUpperCase();
  const c = TEAM_COLORS[code];
  const style = c ? ` style="--b:${c};--bt:${textOn(c)}"` : "";
  return `<span class="badge${small ? " sm" : ""}"${style} aria-hidden="true">${esc(label)}</span>`;
}

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
function strip(model, market, kalshi, kalshiLabel = "Kalshi", bet = null) {
  const L = p => (clamp01(p) * 100).toFixed(2) + "%";
  let band = "";
  if (isNum(market)) {
    const lo = clamp01(market - 0.10), hi = clamp01(market + 0.10);
    band = `<div class="strip-band" style="left:${L(lo)};width:${((hi - lo) * 100).toFixed(2)}%"></div>`;
  }
  const lines = [`Model: ${pct(model)}`];
  if (isNum(market)) lines.push(`Market: ${pct(market)}`, `Gap: ${pts(model - market)} pts (limit ±10)`);
  if (isNum(kalshi)) lines.push(`${kalshiLabel}: ${cents(kalshi)}`);
  if (isNum(bet) && Math.abs(bet - model) > 1e-9) lines.push(`Bet probability: ${pct(bet)}`);
  return `<div class="strip">
      <div class="strip-track"></div>${band}<div class="strip-mid"></div>
      ${isNum(market) ? `<div class="m m-market" style="left:${L(market)}"></div>` : ""}
      ${isNum(kalshi) ? `<div class="m m-kalshi" style="left:${L(kalshi)}"></div>` : ""}
      <div class="m m-model" style="left:${L(model)}"></div>
      <div class="strip-scale"><span>0%</span><span>50%</span><span>100%</span></div>
      <div class="strip-hit" data-tip="${esc(lines.join("<br>"))}"></div>
    </div>
    <div class="strip-vals"><span>Model <b>${pct(model)}</b></span><span>Market <b>${pct(market)}</b></span>${isNum(kalshi) ? `<span>${esc(kalshiLabel)} <b>${cents(kalshi)}</b></span>` : ""}${isNum(bet) && Math.abs(bet - model) > 1e-9 ? `<span title="Probability used for edges">Bet <b>${pct(bet)}</b></span>` : ""}</div>`;
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
  el.innerHTML = (SPORT === "nfl" ? `<span class="pill ok" title="nflverse data needs no key"><i class="dot"></i>nflverse</span>` : p(STATUS.cfbd_key, "CFBD key", STATUS.cfbd_key ? "CFBD_API_KEY is set" : "CFBD_API_KEY is missing"))
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
  WEEK = null; WEEK_ERR = null; LEGS = null; PARLAY_RESULT = null; PARLAY_ERR = null; BETS = null;
  renderWeek();
  const sport = SPORT;
  weekPromise = api(withSport("/api/week" + (force ? "?refresh=1" : ""))).then(d => { if (sport === SPORT) WEEK = d; })
    .catch(e => { if (sport === SPORT) WEEK_ERR = e.message; }).finally(() => {
    renderWeek();
    if (!$("#view-parlay").hidden) renderParlay();
    if (!$("#view-bets").hidden) renderBets();
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
  const { info, rows, generated } = WEEK;
  $("#week-title").textContent = info.label;
  const flags = rows.filter(r => r.flag).length;
  const withKalshi = rows.filter(r => r.kalshi).length;
  const withOdds = rows.filter(r => r.market).length;
  const onlyFlags = $("#flags-only").checked;
  const shown = onlyFlags ? rows.filter(r => r.flag) : rows;

  const summary = `<div class="summary">
    <div><div class="label">Games</div><div class="big">${rows.length}</div><div class="small">${SPORT === "nfl" ? "Not yet played" : "Upcoming, at least one FBS team"}</div></div>
    <div><div class="label">Flags</div><div class="big flag-count">${flags}</div><div class="small">Edge ≥ 3 pts and gap ≤ 10 pts</div></div>
    <div><div class="label">Kalshi markets</div><div class="big">${withKalshi}<span class="label"> / ${rows.length}</span></div><div class="small">Others show "no market"</div></div>
    <div><div class="label">Sportsbook odds</div><div class="big">${withOdds}<span class="label"> / ${rows.length}</span></div><div class="small">Board built ${esc(generated)}</div></div>
  </div>`;

  const notes = (info.notes || []).length ? `<div class="notes">${notesBox(info.notes)}</div>` : "";
  const trust = info.bet_basis ? `<div class="notice"><p><b>Bet probability = ${esc(info.bet_basis)}.</b> The Benter-style combination is refit every season and the model only gets weight if it clears t ≥ 3. Right now it doesn't, so edges can only come from Kalshi prices that differ from the sportsbook consensus, or from injuries.csv. The circle still shows the raw model.</p></div>` : "";
  if (!shown.length) {
    body.innerHTML = notes + trust + summary + `<div class="games"><div class="empty">${onlyFlags ? "No games are flagged this week. That's normal: the rule is strict on purpose." : "No upcoming games found for this week."}</div></div>`;
    return;
  }

  body.innerHTML = notes + trust + summary + LEGEND + `<div class="games">${shown.map(gameRow).join("")}</div>`;
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
      <div class="vs">
        <div class="team">${teamBadge(g.away_code, g.away)}<span>${esc(g.away)}</span></div>
        <div class="team home">${teamBadge(g.home_code, g.home)}<span>${esc(g.home)}</span> <span class="at-tag">home</span></div>
      </div>
      <div class="meta"><span>${esc(kickoff(g.start))}</span>${g.neutral ? `<span class="tag">Neutral</span>` : ""}${inj}<span class="mono">ID ${esc(g.id)}</span></div>
    </div>
    <div class="spreads">
      <span class="k">Model</span><span class="v">${esc(spreadText(g.home_code || g.home, g.away_code || g.away, p.home_spread))}</span>
      <span class="k">Market</span><span class="v">${esc(spreadText(g.home_code || g.home, g.away_code || g.away, m ? m.home_spread : null))}</span>
    </div>
    <div class="strip-cell">${strip(isNum(p.raw_home_prob) ? p.raw_home_prob : p.home_prob, m ? m.home_prob : null, kalshiHome, "Kalshi", p.home_prob)}</div>
    <div class="verdict">${chip(r.flag)}<div class="edge">${edgeLine}</div></div>
    ${r.env ? envChips(r) : ""}
    <button class="src-toggle" aria-expanded="false" aria-controls="${id}">Sources &amp; timestamps</button>
    <div class="sources" id="${id}" hidden>
      <div><span class="label">Model spread &amp; win %</span>${esc(r.sources.model)}</div>
      <div><span class="label">Market spread &amp; win %</span>${esc(r.sources.market)}</div>
      <div><span class="label">Kalshi price</span>${esc(r.sources.kalshi)}</div>
      ${r.sources.weather ? `<div><span class="label">Weather</span>${esc(r.sources.weather)}</div>` : ""}
    </div>
  </article>`;
}

function envChips(r) {
  const e = r.env, p = r.pred, g = r.game, fx = e.forecast;
  const chips = [];
  if (isNum(e.home_rest) && isNum(e.away_rest)) chips.push(`Rest <b>${e.away_rest}d / ${e.home_rest}d</b>`);
  if (e.tz) chips.push(`${esc(e.tz > 0 ? g.away : g.home)} traveled <b>${Math.abs(e.tz)}h east</b>`);
  if (g.roof && !["outdoors", "open"].includes(g.roof)) chips.push(`Roof <b>${esc(g.roof)}</b>`);
  else if (fx) chips.push(`Kickoff <b>${Math.round(fx.temp)}°F, wind ${Math.round(fx.wind)} mph${isNum(fx.gusts) ? ` (gusts ${Math.round(fx.gusts)})` : ""}, rain ${isNum(fx.precip_prob) ? fx.precip_prob + "%" : "n/a"}</b>`);
  else if (g.neutral) chips.push("Neutral site");
  else chips.push("Weather <b>forecast unavailable</b>");
  if (e.home_qb && e.away_qb) chips.push(`QBs <b>${esc(e.away_qb)} @ ${esc(e.home_qb)}</b>${e.qb_pts ? `: <b>${signed(e.qb_pts)} pts</b> to ${esc(g.home)}` : ""}`);
  if (p.env_margin) chips.push(`Rest/travel/QB <b>${signed(p.env_margin)} pts</b> to ${esc(g.home)}`);
  if (p.env_total) chips.push(`Weather <b>${signed(p.env_total)} pts</b> to total`);
  if (p.injury_adj) chips.push(`Injuries <b>${signed(p.injury_adj)} pts</b>`);
  return `<div class="env">${chips.map(c => `<span class="chip-env">${c}</span>`).join("")}</div>`;
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
  try { s = await api(withSport("/api/backtest" + (start ? "?start=1" : ""))); }
  catch (e) { $("#bt-body").innerHTML = setupNotice(e.message); return; }
  if (s.sport !== SPORT) return;
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
    body.innerHTML = `<div class="panel"><h3>Replaying ${SPORT === "nfl" ? "2012–2025" : "2021–2025"}, week by week</h3><p class="lede">Started ${esc(s.started)}. Each week is predicted before its results are learned.</p><div class="progress"><span></span></div></div>`;
    return;
  }
  if (s.state === "error") { body.innerHTML = setupNotice(s.error); return; }

  const r = s.result, all = r.seasons.find(x => x.label === "ALL"), vm = r.vs_market;
  const verdictBox = `<div class="notice ${vm && vm.model_worse ? "warn" : ""}"><h3>${vm ? (Math.abs(vm.model.brier - vm.market.brier) < 0.0005 ? "Tied with the market" : vm.model_worse ? "The market wins" : "Model ahead of the market") : "No market comparison"}</h3><p>${esc(r.verdict)}</p></div>`;
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

  body.innerHTML = verdictBox + tiles + (r.env ? nflExtras(r) : "") + `<div class="two-col">
      <div><h3 style="margin-bottom:10px">By season</h3>${seasonTable}${vmTable}</div>
      <div><h3 style="margin-bottom:4px">Calibration</h3><p class="label" style="margin-bottom:8px;text-transform:none;letter-spacing:0">Dots on the dashed line mean "70% predicted" really won about 70% of the time.</p>
        <div class="panel" style="padding:10px">${calibrationChart(r.calibration)}</div>${calTable}</div>
    </div>`;
}

function nflExtras(r) {
  const tag = ok => ok ? `<span class="chip-env" style="color:var(--good)"><b>Used</b></span>` : `<span class="chip-env">Rejected</span>`;
  const feats = ["margin", "total"].flatMap(g => Object.entries(r.env[g]).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${signed(v.coef, 3)}</td><td>${v.se.toFixed(3)}</td><td>${signed(v.t, 2)}</td><td>${tag(v.accepted)}</td><td style="text-align:left;white-space:normal">${esc(v.unit)}</td></tr>`)).join("");
  const wf = r.walk_forward.map(w => `<tr><td>${w.season}</td><td>${w.combo.games}</td><td>${w.combo.a1.toFixed(3)}</td><td>${signed(w.combo.a2, 3)}</td><td>${signed(w.combo.t_model, 2)}</td><td>${signed(w.combo.ll_gain_vs_market, 2)}</td><td>${w.combo.market_only.a1.toFixed(3)} (t ${signed(w.combo.market_only.t_bias, 2)})</td><td>${esc(w.combo.mode)}</td><td>${w.sigma.toFixed(2)}</td></tr>`).join("");
  const names = { raw_moneyline: "Raw model · moneyline", combo_moneyline: "Bet prob · moneyline", raw_spread: "Raw model · spread", combo_spread: "Bet prob · spread" };
  const sim = Object.entries(r.betting).map(([k, v]) => `<tr><td>${names[k] || esc(k)}</td><td>${v.bets}</td><td>${pct(v.roi)}</td><td>${v.ci95[0] == null ? "n/a" : `${signed(v.ci95[0] * 100)}% to ${signed(v.ci95[1] * 100)}%`}</td><td>${v.t == null ? "n/a" : signed(v.t, 2)}</td><td>${v.kelly_final.toFixed(3)}×</td><td>${pct(v.kelly_max_drawdown)}</td></tr>`).join("");
  const rm = r.raw_model, t = r.totals;
  const ou = x => `${x.w}–${x.l}–${x.p}${x.pct == null ? "" : ` (${pct(x.pct)})`}`;
  return `<div class="notice" style="margin-bottom:18px">
      <h3>How this was tested</h3>
      <p>Walk-forward: each test season is fitted only on earlier seasons (McLean &amp; Pontiff: edges decay, so refit). Features are decided in advance and used only if t ≥ 3 (Harvey, Liu &amp; Zhu). All ${r.trials} betting variants are shown, not just the best (Bailey &amp; López de Prado). The raw Elo+EPA model scored Brier ${rm.brier.toFixed(4)} on its own; see the Research tab for every source.</p>
    </div>
    <h3 style="margin-bottom:10px">Benter-style blend of market and model, refit each season</h3>
    <div class="table-wrap" style="margin-bottom:8px"><table><thead><tr><th>Season</th><th>Fit games</th><th>Market weight a1</th><th>Model weight a2</th><th>t (model)</th><th>LL gain</th><th>Market-only slope</th><th>Used</th><th>Spread σ</th></tr></thead><tbody>${wf}</tbody></table></div>
    <p class="label" style="margin-bottom:18px;text-transform:none;letter-spacing:0">A model weight near 0 means the model adds nothing beyond the market. A market-only slope above 1 would mean favorites are underpriced (favorite-longshot bias). "Used" is whatever cleared t ≥ 3.</p>
    <div class="two-col" style="margin-bottom:18px">
      <div><h3 style="margin-bottom:10px">Candidate features (latest fit)</h3>
        <div class="table-wrap"><table><thead><tr><th>Feature</th><th>Effect</th><th>Std err</th><th>t</th><th>Decision</th><th style="text-align:left">Meaning</th></tr></thead><tbody>${feats}</tbody></table></div></div>
      <div><h3 style="margin-bottom:10px">Betting at closing prices</h3>
        <div class="table-wrap"><table><thead><tr><th>Variant</th><th>Bets</th><th>ROI</th><th>95% CI</th><th>t</th><th>¼ Kelly</th><th>Max DD</th></tr></thead><tbody>${sim}</tbody></table></div>
        <p class="label" style="margin-top:8px;text-transform:none;letter-spacing:0">Flat 1-unit ROI with bootstrap 95% CI. An edge counts only if t ≥ 3 and the CI stays above 0.</p>
        ${r.props ? `<div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Player props</th><th>Avg miss</th><th>vs recent avg</th><th>Below 25/50/75% pts</th></tr></thead><tbody>${r.props.map(p => `<tr><td>${esc(p.label)}</td><td>${p.mae_model.toFixed(1)}</td><td>${p.mae_baseline.toFixed(1)}</td><td>${pct(p.coverage["0.25"])} / ${pct(p.coverage["0.5"])} / ${pct(p.coverage["0.75"])}</td></tr>`).join("")}</tbody></table></div>
        <p class="label" style="margin-top:6px;text-transform:none;letter-spacing:0">Props are calibrated when the last column reads about 25% / 50% / 75%.</p>` : ""}
        <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Totals</th><th>Avg miss (pts)</th><th>O/U vs close</th></tr></thead><tbody>
          <tr><td>Model, with wind</td><td>${t.mae_model.toFixed(2)}</td><td>${ou(t.ou_with_env)}</td></tr>
          <tr><td>Closing total</td><td>${t.mae_market.toFixed(2)}</td><td>n/a</td></tr>
        </tbody></table></div></div>
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
    PARLAY_RESULT = await api("/api/parlay", { body: { sport: SPORT, legs: LEGS.map(l => ({ game_id: l.gid, team: l.team })), offered: raw || null } });
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

// ---------------------------------------------------------------- bets
let BETS = null, BETS_ERR = null;

function bankroll() { const v = Number(store.get("bankroll")); return v > 0 ? v : 1000; }

async function loadBets(force = false) {
  if (force) { await loadWeek(true); }
  else if (!WEEK && !WEEK_ERR) { await loadWeek(); }
  if (WEEK_ERR) { renderBets(); return; }
  const sport = SPORT;
  try { const d = await api(withSport(`/api/bets?bankroll=${bankroll()}`)); if (sport === SPORT) { BETS = d; BETS_ERR = null; } }
  catch (e) { if (sport === SPORT) BETS_ERR = e.message; }
  renderBets();
}

function renderBets() {
  const body = $("#bets-body");
  if (WEEK_ERR) { body.innerHTML = setupNotice(WEEK_ERR); return; }
  if (BETS_ERR) { body.innerHTML = setupNotice(BETS_ERR); return; }
  if (!BETS) { body.innerHTML = `<div class="panel"><p class="label">Checking every game against the market and Kalshi…</p><div class="progress"><span></span></div></div>`; if (WEEK) loadBets(); return; }
  const { info, bets: b } = BETS;
  const money = x => x ? `$${x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–";
  const notes = notesBox(info.notes);
  const basis = info.bet_basis ? `<div class="notice ${info.bet_basis === "market" ? "warn" : ""}"><h3>Read this first</h3><p>Bet probability = <b>${esc(info.bet_basis)}</b>. The NFL backtest found the model adds nothing the closing market doesn't already know, so it gets no weight. Real opportunities here come from <b>Kalshi prices that disagree with the sportsbook consensus</b> or from injury news you add to injuries.csv before the market reacts. Every recommendation is logged as a paper trade, so you can see whether it beat the closing line (<code>python main.py clv</code>).</p></div>` : `<div class="notice"><p>Check the Backtest tab before trusting these. If the model loses to the closing line, its edges are mostly noise.</p></div>`;
  const controls = `<div class="panel" style="display:flex;gap:14px;align-items:end;flex-wrap:wrap">
      <label class="field inline"><span class="label">Bankroll ($)</span><input id="bankroll" type="number" min="0" step="50" value="${bankroll()}"></label>
      <p class="lede" style="margin:0;flex:1;min-width:240px">Stakes are quarter Kelly with caps of 2% per bet, 3% per game, and 10% per slate. Only bets with at least 3% edge get a stake. Suggested total: <b>${money(b.total_stake)}</b>.</p>
    </div>`;
  const kRows = b.kalshi.map(k => `<tr><td>${esc(k.team)}</td><td>${esc(k.matchup)}</td><td>${pct(k.model)}</td><td>${pct(k.market)}</td><td>${cents(k.price)}</td><td>$${k.fee.toFixed(4)}</td><td>${pts(k.edge)}</td><td>${k.longshot ? `<span class="chip chip-pass" title="Kalshi contracts under 20¢ lose far more than their price implies (Bürgi, Deng & Whelan)">Long shot</span>` : chip(k.flag)}</td><td>${money(k.stake)}</td></tr>`).join("");
  const evCell = ev => `<td class="${ev >= 0 ? "ev-pos" : "ev-neg"}">${ev >= 0 ? "+" : "−"}$${Math.abs(ev).toFixed(3)}</td>`;
  const bookRows = b.book.map(x => `<tr><td>${esc(x.team)}</td><td>${esc(x.matchup)}</td><td>${pct(x.model)}</td><td>${pct(x.market)}</td><td>${x.decimal.toFixed(2)}</td>${evCell(x.ev)}<td>${money(x.stake)}</td></tr>`).join("");
  const spRows = (b.spreads || []).map(x => `<tr><td>${esc(x.team)} ${signed(x.line)}</td><td>${esc(x.matchup)}</td><td>${pct(x.model)}</td><td>${x.decimal.toFixed(2)}</td>${evCell(x.ev)}<td>${money(x.stake)}</td></tr>`).join("");
  const parRows = b.parlays.map(x => `<tr><td style="white-space:normal;text-align:left">${x.legs.map(l => esc(l.team)).join(" + ")}</td><td>${pct(x.prob)}</td><td>${pct(x.market_prob)}</td><td>${x.decimal.toFixed(2)} (${american(x.decimal >= 2 ? (x.decimal - 1) * 100 : -100 / (x.decimal - 1))})</td><td class="ev-pos">+$${x.ev.toFixed(3)}</td></tr>`).join("");
  const empty = msg => `<div class="panel empty">${msg}</div>`;
  body.innerHTML = `<div class="notes">${basis}${notes}</div>${controls}
    <section class="bets-section"><h2>Kalshi</h2><p class="lede">Buy-YES edge after Kalshi's fee. A FLAG needs at least 3 points of edge and a price of at least 20¢. A resting limit order (maker) usually beats paying the ask.</p>
      ${kRows ? `<div class="table-wrap"><table><thead><tr><th>Team</th><th>Game</th><th>Bet %</th><th>Market %</th><th>YES ask</th><th>Fee</th><th>Edge (pts)</th><th>Call</th><th>Stake</th></tr></thead><tbody>${kRows}</tbody></table></div>` : empty("No Kalshi prices right now. Kalshi may have no markets for these games, or this computer can't reach it.")}</section>
    <section class="bets-section"><h2>Moneylines</h2><p class="lede">Sides where the bet probability beats the average book price. EV is expected profit per $1.</p>
      ${bookRows ? `<div class="table-wrap"><table><thead><tr><th>Team</th><th>Game</th><th>Bet %</th><th>Market %</th><th>Decimal</th><th>EV / $1</th><th>Stake</th></tr></thead><tbody>${bookRows}</tbody></table></div>` : empty("None. The bet probabilities don't beat any book price after the vig, which is expected when the bet probability is the market.")}</section>
    <section class="bets-section"><h2>Spreads</h2><p class="lede">Cover chance from the NFL's real margin shape: a bell curve (σ fitted on our data) with extra weight on 3, 7, 10 and 14. Pushes on whole-number lines refund the stake.</p>
      ${spRows ? `<div class="table-wrap"><table><thead><tr><th>Side</th><th>Game</th><th>Cover %</th><th>Decimal</th><th>EV / $1</th><th>Stake</th></tr></thead><tbody>${spRows}</tbody></table></div>` : empty("None.")}</section>
    <section class="bets-section"><h2>Parlays</h2><p class="lede">2–3 legs from different games, each leg already a positive-EV side. Not staked: the house margin compounds and the variance is large.</p>
      ${parRows ? `<div class="table-wrap"><table><thead><tr><th>Legs</th><th>Hit %</th><th>Market hit %</th><th>Payout</th><th>EV / $1</th></tr></thead><tbody>${parRows}</tbody></table></div>` : empty("None. A parlay needs at least two positive-EV legs from different games.")}</section>
    ${b.excluded_gap.length ? `<section class="bets-section"><h2>Left out</h2><p class="lede">Model and market more than 10 points apart, so the model is probably missing news (often a quarterback change):</p><p>${b.excluded_gap.map(esc).join(" · ")}</p></section>` : ""}`;
}
$("#bets-refresh").addEventListener("click", () => { BETS = null; renderBets(); loadBets(true); });
$("#bets-body").addEventListener("change", e => {
  if (e.target.id === "bankroll") { store.set("bankroll", String(Number(e.target.value) || 1000)); BETS = null; renderBets(); loadBets(); }
});

// ---------------------------------------------------------------- consensus picks
let PICKS = null, PICKS_ERR = null, picksLoading = false;

async function loadPicks(force = false) {
  if (picksLoading) return;
  picksLoading = true; PICKS_ERR = null;
  if (force) PICKS = null;
  renderPicks();
  const sport = SPORT;
  try { const d = await api(withSport(`/api/picks${force ? "?refresh=1" : ""}`)); if (sport === SPORT) PICKS = d; }
  catch (e) { if (sport === SPORT) PICKS_ERR = e.message; }
  picksLoading = false; renderPicks();
}

function renderPicks() {
  const body = $("#picks-body");
  if (PICKS_ERR) { body.innerHTML = setupNotice(PICKS_ERR); return; }
  if (!PICKS || PICKS.sport !== SPORT) {
    body.innerHTML = `<div class="panel"><p class="label">Collecting predictions from ESPN FPI, Dratings, Sagarin, Dimers and OddsShark…</p><div class="progress"><span></span></div></div>`;
    if (!picksLoading) loadPicks();
    return;
  }
  const rec = PICKS.record || {};
  const recLine = k => { const r = rec[k]; if (!r) return "–"; const n = r.W + r.L; return `${r.W}-${r.L}-${r.P}${n ? ` · ${(100 * r.W / n).toFixed(1)}%` : ""}`; };
  const strong = PICKS.best.filter(b => b.tier === "Strong");
  const notes = PICKS.notes.length ? `<div class="notice"><ul class="notes-box-list">${PICKS.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div>` : "";
  const kbet = k => !k ? "–" : `${k.side} “${esc(k.contract)}” @ ${cents(k.ask)}${k.line_gain > 0 ? ` <span class="tag">+${k.line_gain.toFixed(1)} vs DK</span>` : k.line_gain < 0 ? ` <span class="tag">${k.line_gain.toFixed(1)} vs DK</span>` : ""}`;
  const bestRows = PICKS.best.map(b => `<tr class="${b.tier === "Strong" ? "is-flag" : ""}"><td class="player-td"><b>${esc(b.pick)}</b></td>
      <td class="player-td">${esc(b.matchup)}</td><td>${esc(b.type)}</td><td>${b.tier === "Strong" ? chip(true).replace("Flag", "Strong") : `<span class="chip chip-pass">Lean</span>`}</td>
      <td>${Math.abs(b.gap).toFixed(1)} pts</td><td>${b.agree}/${b.n}</td><td class="player-td">${kbet(b.kalshi_bet)}</td></tr>`).join("");
  const ctxNote = g => {
    const c = g.context || {}, out = [];
    for (const side of ["home", "away"]) { const q = (c.qb || {})[side]; if (q && q.status) out.push(`${esc(g[side])} QB ${esc(q.name)}: ${esc(q.status)}`); }
    const w = c.weather || {};
    if (w.indoor) out.push("indoors"); else if (isNum(w.gust)) out.push(`gusts ${Math.round(w.gust)} mph`);
    return out.join("<br>") || "–";
  };
  const kLine = (dk, k, fmt) => `${fmt(dk)}${isNum(k) ? ` <span class="tag">K ${fmt(k)}</span>` : ""}`;
  const srcCell = g => Object.entries(g.sources).map(([k, v]) => `${esc(k)}: ${isNum(v.margin) ? esc(g.home) + " " + signed(v.margin) : pct(v.p)}`).join("<br>");
  const gameRows = PICKS.games.map(g => `<tr><td>${kickoff(g.kickoff)}</td><td class="player-td">${esc(g.matchup)}</td>
      <td class="player-td">${esc(g.winner)} ${pct(g.winner_p, 0)} <span class="tag">${g.winner_agree}/${g.n_sources}</span></td>
      <td>${isNum(g.margin) ? esc(g.home) + " " + signed(g.margin) : "–"}</td>
      <td>${g.ats ? kLine(g.ats.line, (g.kalshi || {}).home_line, x => signed(x, 1)) : "–"}</td>
      <td class="player-td">${g.ats ? `${esc(g.ats.pick)} <span class="tag">${signed(g.ats.gap)} · ${g.ats.agree}/${g.ats.n}</span>` : "–"}</td>
      <td>${g.ou ? kLine(g.ou.line, (g.kalshi || {}).total, x => x.toFixed(1)) : "–"}</td>
      <td class="player-td">${g.ou ? `${esc(g.ou.pick)} <span class="tag">proj ${g.total.toFixed(1)}</span>` : "–"}</td>
      <td class="player-td" style="font-size:.72rem">${ctxNote(g)}</td>
      <td class="player-td" style="font-size:.72rem;color:var(--muted)">${srcCell(g)}</td></tr>`).join("");
  body.innerHTML = `<div class="notes">${notes}</div>
    <div class="summary">
      <div><div class="label">Strong picks</div><div class="big flag-count">${strong.length}</div><div class="small">all sources agree, line off 3+</div></div>
      <div><div class="label">Lean picks</div><div class="big">${PICKS.best.length - strong.length}</div><div class="small">2/3 agree</div></div>
      <div><div class="label">Graded record</div><div class="big">${recLine("all").split(" · ")[0]}</div><div class="small">${recLine("all").includes("·") ? recLine("all").split(" · ")[1] + " · need 52.4% at −110" : "no finished picks yet"}</div></div>
      <div><div class="label">Games</div><div class="big">${PICKS.games.length}</div><div class="small">with 1+ forecast</div></div>
    </div>
    <section class="bets-section"><h2>Best bets</h2><p class="lede">Picks where the forecasters agree and the line is off. Not validated yet: judge them by the graded record after 100+ picks.</p>
      ${bestRows ? `<div class="table-wrap"><table><thead><tr><th>Pick</th><th class="l">Game</th><th>Type</th><th>Tier</th><th>Line off by</th><th>Agree</th><th class="l">On Kalshi</th></tr></thead><tbody>${bestRows}</tbody></table></div>` : `<div class="panel empty">No Strong or Lean picks right now.</div>`}</section>
    <section class="bets-section"><h2>Every game</h2><p class="lede">Consensus winner, predicted margin (home team's view), DraftKings' line next to Kalshi's own line (K, where its contract prices cross 50¢), spread and total picks, starting-QB injuries and weather, and each source's number.</p>
      <div class="table-wrap"><table><thead><tr><th>Kickoff</th><th class="l">Game</th><th class="l">Winner</th><th>Margin</th><th>Home line (DK · K)</th><th class="l">Spread pick</th><th>Total (DK · K)</th><th class="l">Total pick</th><th class="l">QB / weather</th><th class="l">Sources</th></tr></thead><tbody>${gameRows}</tbody></table></div></section>`;
}
$("#picks-refresh").addEventListener("click", () => loadPicks(true));

// ---------------------------------------------------------------- kalshi scan
let SCAN = null, SCAN_ERR = null, scanLoading = false;

async function loadScan(force = false) {
  if (scanLoading) return;
  scanLoading = true; SCAN_ERR = null;
  if (force) SCAN = null;
  renderScan();
  const sport = SPORT;
  const days = SPORT === "nfl" ? 3 : 1;   // NFL games are mostly Sunday; college Friday-Saturday
  try { const d = await api(withSport(`/api/scan?days=${days}${force ? "&refresh=1" : ""}`)); if (sport === SPORT) SCAN = d; }
  catch (e) { if (sport === SPORT) SCAN_ERR = e.message; }
  scanLoading = false; renderScan();
}

function renderScan() {
  const body = $("#scan-body");
  if (SCAN_ERR) { body.innerHTML = setupNotice(SCAN_ERR); return; }
  if (!SCAN || SCAN.sport !== SPORT) {
    body.innerHTML = `<div class="panel"><p class="label">Fetching Kalshi and sportsbook prices…</p><div class="progress"><span></span></div></div>`;
    if (!scanLoading) loadScan();
    return;
  }
  const all = $("#scan-all").checked;
  const flags = SCAN.rows.filter(r => r.flag);
  const rows = all ? SCAN.rows : SCAN.rows.slice(0, 15);
  const money = x => x ? `$${x.toFixed(2)}` : "–";
  const call = r => r.flag ? chip(true) : (r.longshot && r.edge >= 0.03
    ? `<span class="chip chip-pass" title="Kalshi contracts under 20¢ lose far more than their price implies">Long shot</span>` : chip(false));
  const bet = r => r.market_type === "moneyline" ? `${esc(r.team)} to win` : `${esc(r.team)} <span class="tag">${r.side.toUpperCase()}</span>`;
  const book = r => r.market_type === "moneyline" ? american(r.book_ml) : (r.market_type === "total" ? "O/U " : "") + signed(r.book_line, 1).replace("+", r.market_type === "total" ? "" : "+");
  const tr = r => `<tr class="${r.flag ? "is-flag" : ""}"><td class="player-td">${bet(r)}</td><td class="player-td">${esc(r.matchup)}</td>
      <td>${kickoff(r.kickoff)}</td><td>${book(r)}</td><td>${pct(r.market)}</td><td>${cents(r.price)}</td>
      <td>${isNum(r.bid) ? cents(r.bid) : "–"}</td><td class="${r.edge >= 0 ? "ev-pos" : "ev-neg"}">${pts(r.edge)}</td><td>${call(r)}</td><td>${money(r.stake)}</td></tr>`;
  const scanNotes = SCAN.notes.length ? `<div class="notice"><ul class="notes-box-list">${SCAN.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div>` : "";
  body.innerHTML = `<div class="notes">${scanNotes}</div>
    <div class="summary">
      <div><div class="label">Flags</div><div class="big flag-count">${flags.length}</div><div class="small">edge ≥ 3 pts after fee</div></div>
      <div><div class="label">Games compared</div><div class="big">${SCAN.games}</div><div class="small">Kalshi × ${esc(SCAN.rows[0]?.book || "sportsbook")}</div></div>
      <div><div class="label">Best edge</div><div class="big">${SCAN.rows.length ? pts(SCAN.rows[0].edge) : "–"}</div><div class="small">${SCAN.rows.length ? esc(SCAN.rows[0].team) : ""}</div></div>
      <div><div class="label">Prices as of</div><div class="big clock-big">${esc((SCAN.kalshi_stamp || "").slice(11, 16))}<span>Z</span></div><div class="small">Kalshi ${esc(SCAN.kalshi_stamp || "")}</div></div>
    </div>
    ${SCAN.rows.length ? `<div class="table-wrap"><table><thead><tr><th>Bet</th><th class="l">Game</th><th>Kickoff</th><th>Book line</th><th>Book fair</th><th>YES ask</th><th>YES bid</th><th>Edge (pts)</th><th>Call</th><th>Stake</th></tr></thead>
      <tbody>${rows.map(tr).join("")}</tbody></table></div>` : `<div class="panel empty">No upcoming games with both a Kalshi market and sportsbook odds.</div>`}
    <p class="lede foot-note">Bet probability here is the sportsbook's price, not the model. DraftKings is one recreational book; with an Odds API key, Best bets uses Pinnacle instead. Prices move: recheck before kickoff.</p>`;
}
$("#scan-refresh").addEventListener("click", () => loadScan(true));
$("#scan-all").addEventListener("change", renderScan);

// ---------------------------------------------------------------- props
let PROPS = null, PROPS_ERR = null, propsLoading = false;
const PROPS_FILTER = { stat: "all", game: "all", q: "" };

async function loadProps(force = false) {
  if (propsLoading) return;
  if (force) PROPS = null;
  propsLoading = true; PROPS_ERR = null; renderProps();
  try { PROPS = await api(`/api/props?bankroll=${bankroll()}${force ? "&refresh=1" : ""}`); } catch (e) { PROPS_ERR = e.message; }
  propsLoading = false; renderProps();
}

function renderProps() {
  const body = $("#props-body");
  if (SPORT !== "nfl") { body.innerHTML = `<div class="panel empty">Player props are NFL-only for now. Switch to NFL at the top.</div>`; return; }
  if (PROPS_ERR) { body.innerHTML = setupNotice(PROPS_ERR); return; }
  if (!PROPS) {
    body.innerHTML = `<div class="panel"><h3>Building player projections</h3><p class="lede">The first load fits the model on 2019–2025 (about 30–60 seconds). After that it's cached.</p><div class="progress"><span></span></div></div>`;
    if (!propsLoading) loadProps();
    return;
  }
  const money = x => x ? `$${x.toFixed(2)}` : "–";
  const games = [...new Set(PROPS.rows.map(r => r.matchup))];
  const stats = [...new Map(PROPS.rows.map(r => [r.stat, r.label])).entries()];
  const q = PROPS_FILTER.q.trim().toLowerCase();
  const rows = PROPS.rows.filter(r => (PROPS_FILTER.stat === "all" || r.stat === PROPS_FILTER.stat)
    && (PROPS_FILTER.game === "all" || r.matchup === PROPS_FILTER.game) && (!q || r.player.toLowerCase().includes(q)));
  const hasLines = PROPS.rows.some(r => r.market);
  const hasKalshi = PROPS.rows.some(r => r.kalshi);
  const notes = PROPS.notes.length ? `<div class="notice"><ul class="notes-box-list">${PROPS.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div>` : "";
  const warn = `<div class="notice"><p><b>Unvalidated edges.</b> The projections are tested: on 2023–2025 their 25/50/75% points held about 24/50/75% of the time (see Backtest). Kalshi's prop prices have no free history, so edges against them are logged and graded after each game; the learned trust (now ${(PROPS.trust ?? 1).toFixed(2)}) shrinks the model toward Kalshi's price if they keep missing.</p></div>`;
  const flagged = PROPS.rows.filter(r => r.flag && r.kalshi).sort((a, b) => b.kalshi.edge - a.kalshi.edge);
  const kc = r => r.kalshi ? `${r.kalshi.side} “${esc(r.kalshi.contract)}” @ ${cents(r.kalshi.ask)}` : "–";
  const kvs = r => r.kalshi ? `${pct(r.kalshi.p_model, 0)} vs ${pct(r.kalshi.side === "YES" ? r.kalshi.mid : 1 - r.kalshi.mid, 0)}` : "–";
  const best = hasKalshi ? `<section class="bets-section"><h2>Best prop bets</h2><p class="lede">FLAG = edge after Kalshi's fee of 3+ points, price 20–80¢, and the model within 10 points of Kalshi.</p>
    ${flagged.length ? `<div class="table-wrap"><table><thead><tr><th>Player</th><th class="l">Game</th><th>Stat</th><th class="l">Buy on Kalshi</th><th>Model vs Kalshi</th><th>Edge (pts)</th><th>Stake</th></tr></thead><tbody>
    ${flagged.slice(0, 20).map(r => `<tr class="is-flag"><td class="player-td"><b>${esc(r.player)}</b></td><td class="player-td">${esc(r.matchup)}</td><td>${esc(r.label)}</td><td class="player-td">${kc(r)}</td><td>${kvs(r)}</td><td class="ev-pos">${pts(r.kalshi.edge)}</td><td>${money(r.stake)}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="panel empty">No prop clears the bar right now.</div>`}</section>` : "";
  const filters = `<div class="panel" style="display:flex;gap:12px;flex-wrap:wrap;align-items:end">
      <label class="field"><span class="label">Stat</span><select id="pf-stat"><option value="all">All stats</option>${stats.map(([k, l]) => `<option value="${k}" ${PROPS_FILTER.stat === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <label class="field" style="flex:1;min-width:200px"><span class="label">Game</span><select id="pf-game"><option value="all">All games</option>${games.map(g => `<option ${PROPS_FILTER.game === g ? "selected" : ""}>${esc(g)}</option>`).join("")}</select></label>
      <label class="field"><span class="label">Player</span><input id="pf-q" type="text" placeholder="Search" value="${esc(PROPS_FILTER.q)}"></label>
    </div>`;
  const head = `<tr><th>Player</th><th>Stat</th><th>Median</th><th style="text-align:left">Middle 50%${hasKalshi ? " · Kalshi line" : hasLines ? " · book line" : ""}</th>${hasKalshi ? "<th>Kalshi line</th><th class=\"l\">Best Kalshi contract</th><th>Model vs Kalshi</th><th>Edge (pts)</th><th>Stake</th>" : `<th>Fair line</th>${hasLines ? "<th>Book line</th><th>P(over)</th><th>Pick</th><th>EV / $1</th><th>Stake</th>" : ""}`}</tr>`;
  // Range bars share one scale per stat so players can be compared at a glance.
  const scale = {};
  PROPS.rows.forEach(r => { scale[r.stat] = Math.max(scale[r.stat] || 1, r.q75 * 1.15, r.market ? r.market.line * 1.15 : 0, isNum(r.kalshi_median) ? r.kalshi_median * 1.15 : 0); });
  const lineOf = r => isNum(r.kalshi_median) ? r.kalshi_median : (r.market ? r.market.line : null);
  const rbar = r => {
    const X = v => Math.max(0, Math.min(100, v / scale[r.stat] * 100)).toFixed(1);
    const L = lineOf(r);
    return `<div class="rbar" data-tip="${esc(`${r.player} · ${r.label}<br>Middle 50%: ${r.q25.toFixed(0)}–${r.q75.toFixed(0)}<br>Median: ${r.median.toFixed(1)}${L !== null ? `<br>${isNum(r.kalshi_median) ? "Kalshi" : "Book"} line: ${L.toFixed(1)}` : ""}`)}">
      <div class="rt"></div><div class="rb" style="left:${X(r.q25)}%;width:${(X(r.q75) - X(r.q25)).toFixed(1)}%"></div>
      <div class="rm" style="left:${X(r.median)}%"></div>${L !== null ? `<div class="rl" style="left:${X(L)}%"></div>` : ""}</div>`;
  };
  const kcells = r => r.kalshi
    ? `<td>${isNum(r.kalshi_median) ? r.kalshi_median.toFixed(1) : "–"}</td><td class="player-td">${kc(r)}${r.flag ? " ★" : ""}</td><td>${kvs(r)}</td><td class="${r.kalshi.edge >= 0 ? "ev-pos" : "ev-neg"}">${pts(r.kalshi.edge)}</td><td>${money(r.stake)}</td>`
    : `<td colspan="5" class="src">not on Kalshi</td>`;
  const tr = r => `<tr class="${r.flag ? "is-flag" : ""}"><td class="player-td"><div class="player-cell">${teamBadge(r.team, r.team, true)}<div><b>${esc(r.player)}</b> <span class="src">${esc(r.position)}${r.injury ? " · " + esc(r.injury) : ""}</span></div></div></td><td>${esc(r.label)}</td><td>${r.median.toFixed(1)}</td><td style="min-width:160px">${rbar(r)}<span class="src">${r.q25.toFixed(0)}–${r.q75.toFixed(0)}</span></td>${hasKalshi ? kcells(r) : `<td>${r.fair_line}</td>${hasLines ? (r.market ? `<td>${r.market.line}</td><td>${pct(r.p_over)}</td><td>${esc(r.pick)}${r.flag ? " ★" : ""}</td><td class="${r.ev >= 0 ? "ev-pos" : "ev-neg"}">${signed(r.ev, 3)}</td><td>${money(r.stake)}</td>` : `<td colspan="5" class="src">no line</td>`) : ""}`}</tr>`;
  body.innerHTML = `<div class="notes">${notes}${warn}</div>${best}<section class="bets-section"><h2>Every projection</h2></section>${filters}
    <p class="label" style="margin:12px 0 8px;text-transform:none;letter-spacing:0">${rows.length} projections · ${esc(PROPS.source)}${hasKalshi || hasLines ? " · ★ = FLAG (3+ pt edge, within 10 pts of the market)" : ""}</p>
    ${rows.length ? `<div class="table-wrap"><table><thead>${head}</thead><tbody>${rows.map(tr).join("")}</tbody></table></div>` : `<div class="panel empty">No players match those filters.</div>`}`;
}
$("#props-body").addEventListener("change", e => {
  if (e.target.id === "pf-stat") { PROPS_FILTER.stat = e.target.value; renderProps(); }
  if (e.target.id === "pf-game") { PROPS_FILTER.game = e.target.value; renderProps(); }
});
$("#props-body").addEventListener("input", e => {
  if (e.target.id === "pf-q") { PROPS_FILTER.q = e.target.value; const pos = e.target.selectionStart; renderProps(); const i = $("#pf-q"); i.focus(); i.setSelectionRange(pos, pos); }
});
$("#props-refresh").addEventListener("click", () => loadProps(true));

// ---------------------------------------------------------------- ask (chat)
const CHAT = [];   // {role: "user"|"bot", data}
const CHAT_STARTERS = ["What are the best bets right now?", "How much should I bet?", "Should I bet a parlay?",
  "Is the model better than the market?", "Why avoid cheap Kalshi contracts?", "What does wind do to totals?"];

function botHtml(d) {
  const list = (xs, cls = "") => xs.length ? `<ul class="${cls}">${xs.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const cites = d.citations.length ? `<div><p class="label">From the research</p><div class="cites">${d.citations.map(c =>
    `<div class="cite"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a> <span class="src">${esc(c.authors)}${c.year ? `, ${c.year}` : ""}</span><br>${esc(c.snippet)}</div>`).join("")}</div></div>` : "";
  const facts = d.facts.length ? `<div><p class="label">From the app's data</p>${list(d.facts, "facts")}</div>` : "";
  const src = d.sources.length ? `<p class="src">Sources: ${d.sources.map(esc).join(" · ")}</p>` : "";
  return `<div class="msg-bot">${list(d.answer)}${facts}${cites}${src}</div>`;
}

function renderChat(pending = false) {
  const log = $("#chat-log");
  log.innerHTML = CHAT.map(m => m.role === "user" ? `<div class="msg-user">${esc(m.data)}</div>` : botHtml(m.data)).join("")
    + (pending ? `<div class="msg-bot typing">Looking through the research and today's numbers…</div>` : "");
  const last = CHAT.length ? CHAT[CHAT.length - 1] : null;
  const sugg = !CHAT.length ? CHAT_STARTERS : (last && last.role === "bot" && last.data.suggestions.length ? last.data.suggestions : []);
  $("#chat-suggest").innerHTML = sugg.map(q => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  if (!CHAT.length) log.innerHTML = `<div class="msg-bot"><ul><li>Hi! I answer from ${SPORT === "nfl" ? "today’s NFL slate" : "this week’s college slate"}, the backtest (once it has run), and the studies in the research library. Every answer shows its sources.</li></ul></div>`;
  if (CHAT.length) log.lastElementChild?.scrollIntoView({ block: "nearest" });
}

async function ask(q) {
  q = (q || "").trim();
  if (!q) return;
  CHAT.push({ role: "user", data: q });
  renderChat(true);
  $("#chat-send").disabled = true;
  try {
    const d = await api("/api/chat", { body: { message: q, sport: SPORT, bankroll: bankroll() } });
    CHAT.push({ role: "bot", data: d });
  } catch (e) {
    CHAT.push({ role: "bot", data: { answer: [`Couldn't answer: ${e.message}`], facts: [], citations: [], sources: [], suggestions: [] } });
  }
  $("#chat-send").disabled = false;
  renderChat();
}
$("#chat-form").addEventListener("submit", e => { e.preventDefault(); const i = $("#chat-input"); ask(i.value); i.value = ""; });
$("#chat-suggest").addEventListener("click", e => { const b = e.target.closest("[data-q]"); if (b) ask(b.dataset.q); });
$("#ask-clear").addEventListener("click", () => { CHAT.length = 0; renderChat(); });

// ---------------------------------------------------------------- research
let RESEARCH = null;
async function renderResearch() {
  const body = $("#research-body");
  if (!RESEARCH) {
    body.innerHTML = `<div class="panel"><p class="label">Loading the research library…</p></div>`;
    try { RESEARCH = await api("/api/research"); } catch (e) { body.innerHTML = `<div class="notice bad"><p>${esc(e.message)}</p></div>`; return; }
  }
  const conf = { "peer-reviewed": "Peer-reviewed", primary: "Author's own report", reported: "Reported (book/press)", practitioner: "Practitioner practice" };
  const groups = {};
  RESEARCH.studies.forEach(x => (groups[x.domain] = groups[x.domain] || []).push(x));
  body.innerHTML = `<p class="lede" style="margin-bottom:16px">${esc(RESEARCH.about)}</p>` + Object.entries(groups).map(([d, xs]) => `
    <section class="bets-section"><h2>${esc(d)}</h2>
      <div class="games">${xs.map(x => `<article class="study">
        <div class="study-head"><h3>${esc(x.title)}</h3><span class="chip-env">${esc(conf[x.confidence] || x.confidence)}</span><span class="chip-env">${esc(x.scale)} scale</span></div>
        <p class="label" style="text-transform:none;letter-spacing:0">${esc(x.authors)}${x.year ? ` · ${x.year}` : ""} · ${esc(x.venue)}</p>
        <p><b>Found:</b> ${esc(x.finding)}</p>
        <p><b>Why it worked:</b> ${esc(x.why_it_worked)}</p>
        <p><b>Used in Hashmark:</b> ${esc(x.used_in)}</p>
        <p><a href="${esc(x.url)}" target="_blank" rel="noopener">Source</a> · <span class="mono">${esc(x.id)}</span></p>
      </article>`).join("")}</div>
    </section>`).join("");
}

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
const TABS = ["week", "picks", "bets", "scan", "props", "backtest", "parlay", "ask", "research", "explain"];
function setTab(name) {
  if (!TABS.includes(name)) name = "week";
  for (const t of TABS) {
    $(`#tab-${t}`).setAttribute("aria-selected", String(t === name));
    $(`#view-${t}`).hidden = t !== name;
  }
  store.set("tab", name);
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "week") loadWeek();
  if (name === "bets") renderBets();
  if (name === "backtest") loadBacktest(false);
  if (name === "parlay") renderParlay();
  if (name === "explain") initExplain();
  if (name === "research") renderResearch();
  if (name === "ask") renderChat();
  if (name === "props") renderProps();
  if (name === "scan") renderScan();
  if (name === "picks") renderPicks();
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

// ---------------------------------------------------------------- clock & theme
function tickClock() {
  const el = $("#clock");
  if (el) el.textContent = new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";
}
tickClock(); setInterval(tickClock, 15000);
// Hash marks on the field ribbon: one tick per yard-line gap, two rows like a real field.
$("#hash").innerHTML = Array.from({ length: 99 }, (_, i) => (i + 1) * 10).filter(x => x % 100)
  .map(x => `<line x1="${x}" y1="8" x2="${x}" y2="12"/><line x1="${x}" y1="22" x2="${x}" y2="26"/>`).join("");
const SUN = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`;
const MOON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
const isDark = () => document.documentElement.dataset.theme === "dark" ||
  (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
function paintThemeBtn() {
  const dark = isDark();
  $("#theme-btn").innerHTML = dark ? SUN : MOON;
  $("#theme-btn").title = dark ? "Light" : "Dark";
}
$("#theme-btn").addEventListener("click", () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("theme", next);
  paintThemeBtn();
});
paintThemeBtn();

function setSport(sp) {
  SPORT = sp === "cfb" ? "cfb" : "nfl";
  store.set("sport", SPORT);
  $$(".sport-seg button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.sport === SPORT)));
  $("#week-lede").textContent = SPORT === "nfl"
    ? "This week's NFL games: model vs. sportsbook consensus vs. Kalshi, with QB, weather, rest, and travel. Percentages are for the home team."
    : "Model vs. sportsbook consensus vs. Kalshi, for every game this week. Percentages are for the home team.";
  $("#bt-title").textContent = SPORT === "nfl" ? "NFL backtest, 2023–2025" : "College backtest, 2023–2025";
  weekPromise = null; WEEK = null; WEEK_ERR = null; BETS = null; BETS_ERR = null; LEGS = null; PARLAY_RESULT = null; PARLAY_ERR = null;
  PROPS_ERR = null; SCAN = null; SCAN_ERR = null; PICKS = null; PICKS_ERR = null;
  clearTimeout(btTimer);
  loadStatus();
  setTab(store.get("tab") || "week");
}
$(".sport-seg").addEventListener("click", e => { const b = e.target.closest("[data-sport]"); if (b && b.dataset.sport !== SPORT) setSport(b.dataset.sport); });

(async function boot() {
  await loadStatus();
  const wanted = location.hash.slice(1) || store.get("tab");
  if (TABS.includes(wanted)) store.set("tab", wanted);
  setSport(SPORT);
})();
