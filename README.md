# College Football Model (learning project)

A command-line tool that predicts college football games and compares its
predictions with sportsbook odds and Kalshi prices.

> **Read-only.** This tool never places trades, bets, or orders anywhere.
> It is a learning project, not betting advice. Markets are hard to beat.
> Run `python main.py backtest` to see how the model does against the closing line.

---

## 1. Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your keys into .env
```

| Variable | Where to get it | Used for |
|---|---|---|
| `CFBD_API_KEY` | collegefootballdata.com (free) | games, scores, EPA, closing lines, calendar |
| `ODDS_API_KEY` | the-odds-api.com (free tier ≈ 500 credits/month) | live spreads, moneylines, totals |
| *(none)* | Kalshi public market data | game-winner contract prices |

Keys are read from environment variables only. They are never printed, and
they are never written to the cache.

## 2. Commands

```bash
python main.py week                               # this week's table
python main.py backtest                           # 2023–2025 validation report
python main.py parlay 401628374:Georgia 401628380:Texas            # 2 legs
python main.py parlay 401628374:Georgia 401628380:Texas --offered 260   # your book's price
```

`GAMEID` is the `ID` column printed by `week`. `parlay` accepts 2 or 3 legs.

### Local web app

```bash
python main.py serve             # opens http://127.0.0.1:8000 in your browser
python main.py serve --port 8080 # if 8000 is taken
```

| Tab | What it shows |
|---|---|
| **This week** | One row per game with a 0–100% strip: model (circle), market consensus (bar), Kalshi price (diamond), and the ±10-point band the FLAG rule needs. "Sources & timestamps" opens the citation for each number. |
| **Backtest** | Runs the 2023–2025 backtest in the background, then shows the verdict, accuracy / Brier / ATS, per-season table, and a calibration chart. |
| **Parlay** | Pick 2–3 teams. Shows model fair price, market fair price, offered price, and EV, with a warning for same-game legs. |
| **How it works** | Interactive version of the worked example below. Change any input and every step recalculates with the real Python functions. Works without API keys. |

The server uses only Python's built-in `http.server` (no new dependencies) and
listens on `127.0.0.1`, so only your own computer can open it. It is read-only like
the CLI, and API keys never reach the browser.

## 2b. NFL (added later)

```bash
python main.py week --sport nfl        # today's NFL games (add --days 6 for the rest of the week)
python main.py bets --sport nfl        # ranked Kalshi bets, sportsbook singles, parlays
python main.py backtest --sport nfl    # 2023-2025 NFL validation
python main.py parlay 2026_05_LV_NE:Patriots 2026_05_IND_PIT:Steelers --sport nfl
```

**Extra sources (approved):** nflverse (GitHub releases: schedules, scores,
closing lines, rest days, roof, recorded weather, weekly team EPA; no key),
Open-Meteo (kickoff forecast for outdoor stadiums; no key), The Odds API
(`americanfootball_nfl`, same key), Kalshi (`KXNFLGAME`, no key).

**How an NFL game is priced:**
1. Elo + opponent-adjusted EPA, as for college, with NFL constants in `config.NFL`
   (ratings start in 2012, home field 1.5 points). The EPA share is **fitted** each
   season on earlier seasons only: the preset 0.40 is replaced only if the data
   disagrees by |t| >= 3. It does (fitted ~0.12-0.14), so Elo carries most of the margin.
2. Starting-QB adjustment (`QBTracker`): each QB's EPA per dropback (decayed, shrunk
   toward a backup-level prior) vs. the QBs whose play the team's ratings already
   reflect. A backup starting = points the ratings haven't absorbed yet. Starters come
   from the nflverse schedule (projected starters for upcoming games). Fitted effect:
   0.79 pts per point of gap, t = 7.4.
3. Rest, travel, and weather adjustments. Their sizes are **fitted on 2015–2022 only**:
   - rest: points per day of rest advantage
   - travel: points per hour the away team traveled east
   - wind: points of total per mph above 10 (fitted on recorded weather plus a
     realistic forecast error, ±4 mph / ±3°F, because live picks only see forecasts)
   - cold: points of total at 32°F or colder
4. **Trust weight.** The bet probability is `(1 − w) × market + w × model`. `w`
   is fitted on 2015–2022 by log loss. If the model adds nothing beyond the
   market, `w` comes out near 0.
5. Your `injuries.csv` (team = city, nickname, or code like `GB`) is applied
   after the blend, because it's news the market may not have priced yet.
6. The 10-point rule compares the raw model with the market. A bigger gap
   means the model is probably missing news, so that game is left out.

**What the 2023–2025 backtest found:**
- The raw model loses to the closing market on Brier score: 0.2183 vs 0.2102
  (paired bootstrap 95% CI for the gap: +0.003 to +0.013).
- Fitting the EPA weight plus the QB adjustment improved it from 0.2236 to 0.2183
  (paired 95% CI −0.0099 to −0.0007, t = −2.2). Each change alone is not significant.
- Don't list QB changes in `injuries.csv` as well: the QB adjustment already counts them.

**Tried and not adopted** (so they aren't retried blindly):
| Idea | Result |
|---|---|
| Play-by-play EPA with garbage time removed | Tracked margins worse on 2015–2022 (corr 0.349–0.360 vs 0.371) |
| Kalman-filter team ratings instead of Elo (settings picked on 2015–2022) | Better on 2015–2022 (margin MSE 170.0 vs 171.2) but worse on 2023–2025 (Brier 0.2192 vs 0.2183) |
| Props: discount games for a previous team | Worse for rushing, receptions, receiving yards on 2021–2022 |
| Props: usage-based prior for passing yards | Worse on 2021–2022 (t = +4.7) |
- Spread bets use a key-number margin distribution (a margin of 3 is ~2.6× as common
  as a smooth curve says, 7 is ~1.7×). On 2023–2025 it predicts "home wins by more
  than t" better than the plain normal curve (paired, game-resampled t = −2.7) and gets
  pushes right (5.3% predicted vs 5.5% actual on whole-number lines; the normal curve says 0%).
- Play-by-play EPA with garbage time removed (`NFL_EPA_SOURCE = "pbp"`) was tried and
  tracked margins slightly worse on the 2015–2022 fit seasons, so weekly team EPA stays the default.
- It went 49.2% against the spread.
- The fitted trust weight is **0.00**.
- Wind does lower scoring (−0.35 points per mph above 10 on forecast-like weather,
  about 4 standard errors), but closing totals already account for it.
- So live NFL "edges" mainly come from Kalshi prices that disagree with the
  sportsbook consensus, or from injury news you enter before the market moves.

## 1a. Checking Kalshi

```bash
python main.py kalshi --sport nfl --days 4   # or --sport cfb (needs CFBD_API_KEY)
```
Fetches Kalshi right now and prints: open markets, how many seconds old the prices
are, thin markets (bid-ask spread over 5¢), and which slate games did or didn't match a
Kalshi market. `week` and `bets` also print a note for any game with no match, and skip
games that have already kicked off (in-game prices aren't pregame edges). College names
are matched through a fixed alias table in `names.py` (e.g. "Appalachian St." = "App State").

### Consensus picks (no API keys needed)

```bash
python main.py picks --sport cfb            # today's college games
python main.py picks --sport nfl --days 3   # through Sunday
python main.py picks --grade                # score logged picks against final scores
```
Pulls predictions from independent forecasters (ESPN FPI, Dratings, Sagarin, plus
Hashmark's own model for the NFL, and for college once `CFBD_API_KEY` is set) and
averages them: consensus winner and win %, predicted margin vs the DraftKings spread,
projected total vs the over/under, how many sources agree, and the Kalshi price for
the consensus winner. Fixed rules, set before any grading:
- **Strong**: 3+ sources, all on the same side, line off by 3+ pts (NFL 2+), spread under 28
- **Lean**: 2/3 of sources agree, line off by 1.5+ pts (NFL 1+); totals need 3+ pts (NFL 2+)

Strong/Lean picks are logged to `data/consensus_picks.csv` before kickoff and graded with
`--grade`. They are **not validated yet**: forecasters like these usually lose to the closing
line on their own, so trust the graded record after 100+ picks, not the label. Massey and
Sports-Reference block automated access, so they're left out.

### Kalshi scan (no API keys needed)

```bash
python main.py scan --sport cfb            # today + tomorrow (default --days 1)
python main.py scan --sport nfl --days 3   # through Sunday
python main.py scan --sport cfb --all      # every side, not just the top 15
```
Compares Kalshi's winner, spread and total contracts with DraftKings' moneyline,
spread and total on ESPN's public scoreboard (vig removed). Spreads and totals are
compared only where Kalshi has a contract at exactly DraftKings' line, on both sides
(YES and NO); other lines are skipped rather than estimated with an assumed margin curve. Same FLAG rule as `bets` (edge after fee ≥ 3 pts, price
≥ 20¢), quarter-Kelly stakes, started games skipped, FLAGs logged as paper trades.
DraftKings is one recreational book; `bets` with `ODDS_API_KEY` uses Pinnacle instead.

## 1b. When data updates

Hashmark never refreshes on its own. Data updates only when you ask:
- **App:** tap **Refresh** on This week, Best bets, or Props. That re-downloads
  every live source right then: Kalshi, sportsbook odds, weather, and nflverse
  schedules and stats. Finished seasons are never re-downloaded.
- **Command line:** add `--fresh`, e.g. `python main.py bets --sport nfl --fresh`.
  Without it, recent downloads are reused (Kalshi 5 min, odds 30 min,
  weather and nflverse 1 hour) to save your Odds API credits.

Every number's source line shows when it was fetched, down to the second.

## 2a. Run it online (Render)

`render.yaml` sets everything up. Steps (all doable from a phone):
1. Sign in at render.com with GitHub. Go to **New → Blueprint**, pick this repo, and confirm the branch.
2. When asked for secrets, enter:
   - `HASHMARK_PASSWORD`: anything you like; the site asks for it
   - `ODDS_API_KEY`
   - `CFBD_API_KEY`
3. Deploy. Render gives you an `https://hashmark-….onrender.com` URL. Open it and enter the password (any username).

Notes:
- **Kalshi** public prices need no Kalshi account or key. The server only reads
  them. Hashmark never logs in to Kalshi or places orders.
- **Free plan:** the server sleeps after about 15 minutes of no visits, and the
  first visit after that wakes it (about 30–60 s). The props model fit is slow
  on the free plan's small CPU, so a background warm-up starts it at launch.
  The $7 Starter plan is much faster.
- **Storage resets:** the cache and `data/paper_trades.csv` are wiped on each
  redeploy or restart. Copy the paper trades out first if you want to keep them.
- **Locally**, `python main.py serve` still only listens on your own computer.
  It switches to public mode only when the host sets `PORT`.

## 2b-1. NFL player props

```bash
python main.py props              # projections, fair lines (and edges when prop lines are available)
python main.py props --backtest   # walk-forward validation, 2023-2025
```

Stats: passing yards, rushing yards, receptions, and receiving yards (`props.py`).
1. **Baseline:** a recency-weighted average of the player's earlier games
   (4-game half-life), shrunk toward the league average when he has few games.
   Receiving yards instead shrink toward his recent targets × the league's yards per
   target, so a 9-target WR1 is shrunk toward a WR1. That was tested on 2021–2022 for all
   four stats and kept only where it clearly helped (receiving yards, t = −4.3). For
   passing yards it hurt: hot QB stretches really do cool off. Discounting games for a
   previous team also hurt, so it is off.
2. **Adjustments**, each used only if t ≥ 3:
   - the opponent's defense against that stat
   - the team's expected points from the market spread and total
3. **Outcome range:** sd = s0 + s1 × projection, using the actual shape of past
   misses rather than a normal curve. Yards are skewed, and a normal curve
   would lean toward overs.
4. **Prop lines:** with an Odds API key, it averages the books' over/under
   prices after removing the vig and compares P(over) with them. The same rules
   apply (EV ≥ 3%, within 10 pts of the market, capped Kelly stakes), and each
   pick is logged as a paper trade.

**Validation (2023–2025, walk-forward):**
- Predicted 25% / 50% / 75% points held about 24–25% / 48–51% / 75–76% of the time.
- Projections miss by 1–3% less than the plain recent average.
- There is **no free history of prop lines**, so prop edges are labelled
  *unvalidated* until paper trades build a record.

## 2b-2. Ask Hashmark (chat)

The **Ask** tab answers questions in plain English. It runs offline on your
computer with **no AI model and no extra dependencies** (`chat.py`):

1. **Search:** scores each study in `data/research.json` against your question
   (TF-IDF, a standard keyword-weighting method, plus a small synonym list)
   and quotes the best-matching sentence, with a link.
2. **Question types:** recognizes common questions and answers them from the
   app's live numbers:
   - best bets
   - a team ("What about the Packers?")
   - a parlay ("parlay Patriots and Steelers")
   - stake sizing
   - Kalshi fees ("fee at 55c")
   - model accuracy (after you run the backtest)
   - weather
   - closing line value

Every answer lists its sources. It never invents numbers. If it can't match a
question, it says so and suggests ones it can answer.

## 2c. What worked elsewhere, and how it's built in

The full library, with sources, is in `data/research.json` (also the **Research** tab).

| Lesson | Source | Where it lives |
|---|---|---|
| Combine your model with the market and fit the weights | Benter 1994; Bates & Granger 1969 | `nfl.fit_combo`, `nfl.combo_prob` |
| Published edges decay, so refit walk-forward | McLean & Pontiff 2016 | `nfl.run_backtest` (each test season fitted on earlier seasons only) |
| Demand t ≥ 3, decide features in advance, report every trial | Harvey, Liu & Zhu 2016; Bailey & López de Prado 2014 | `nfl.MARGIN_FEATURES`, `nfl.TOTAL_FEATURES`, `strategy.T_STAT_BAR` |
| Size by edge, but use fractional Kelly with caps | Kelly 1956; MacLean, Thorp & Ziemba | `strategy.size_bets` (¼ Kelly; 2% per bet, 3% per game, 10% per slate) |
| Cheap prediction-market contracts are overpriced | Bürgi, Deng & Whelan; Thaler & Ziemba 1988 | `strategy.LONGSHOT_MIN_PRICE` (no Kalshi FLAG under 20¢) |
| NFL margins are roughly normal around the spread, with lumps at 3, 7, 10, 14 | Stern 1991 | `strategy.margin_dist`: discrete normal (σ fitted, ≈12.8) times key-number weights fitted on earlier seasons (`nfl.fit_key_weights`), centered so the line is the median |
| Momentum | Jegadeesh & Titman 1993 | "form" candidate feature |
| Many small edges, explicit costs, correlation and leverage risk | Medallion (Zuckerman); LTCM (Lowenstein) | caps, same-game warnings, net-of-fee EV |
| Score yourself by closing line value | practitioner consensus | `data/paper_trades.csv`, `python main.py clv` |

**What the NFL data said (walk-forward 2023–2025):**
- The model's weight in the Benter blend is about −0.1 (t ≈ −1.1 to −1.8), so it isn't used. The bet probability is the closing market itself.
- NFL closing moneylines show no significant favorite-longshot bias (slope 1.03–1.06, t < 1.1).
- Of the five pre-registered features, only wind (t ≈ −4) is used. Rest, travel, form, and cold were rejected.
- Betting the raw model at closing prices lost: moneyline −1.3% ROI and spread −3.4%, with t near 0 and confidence intervals that include 0.
- Conclusion: in the NFL, the defensible edges are Kalshi prices that disagree with the sportsbook consensus, and news (injuries.csv) the market hasn't priced yet. Paper trades and CLV will show whether those hold up.

## 3. How data flows

```
 CFBD ──────► ratings.py (Elo + EPA + home field) ──► model spread / win% / total
                              ▲                                   │
                       injuries.csv (optional)                    ▼
 The Odds API ─► odds.py (remove vig, average books) ──► market win% ──► compare
 Kalshi ───────► kalshi.py (fee, edge) ───────────────► Kalshi price ──► FLAG / PASS
        all API calls go through cache.py ─► cache/*.json (with fetch time)
```

| File | Job |
|---|---|
| `config.py` | Every constant, plus API-key loading |
| `cache.py` | Saves each API response with the time it was fetched |
| `api_clients.py` | GET-only clients for CFBD, The Odds API, and Kalshi |
| `names.py` | Matches team names across APIs ("Ohio St." = "Ohio State") |
| `ratings.py` | Elo, the EPA adjustment, and the predictions |
| `odds.py` | American odds → probability, vig removal, consensus |
| `kalshi.py` | Fee, edge, the FLAG rule, and market matching |
| `injuries.py` | Reads `injuries.csv` and turns it into spread changes |
| `parlay.py` | Parlay probability, fair odds, and the same-game warning |
| `backtest.py` | Walk-forward test and the report |
| `week.py` | Assembles the weekly comparison |
| `main.py` | Command-line entry point |
| `server.py` | Local web app server (`python main.py serve`) |
| `explain.py` | Step-by-step calculator behind the "How it works" tab |
| `static/` | The web app's page, styles, and script |
| `nfl.py` | NFL data, model, weather/rest/travel, trust weight, backtest, today's slate |
| `nfl_teams.py` | NFL team names, time zones, stadium coordinates |
| `bets.py` | Bet finder: Kalshi singles, moneyline and spread singles, parlays, Kelly stakes |
| `strategy.py` | Kelly sizing, exposure caps, longshot guard, cover probability, bootstrap/t-stats, paper trades and CLV |
| `data/research.json` | The research library: findings, why they worked, where they're used |
| `chat.py` | Offline Ask Hashmark chat: research search + answers from live app data |
| `props.py` | NFL player props: projections, calibrated ranges, prop-line edges |

## 4. How each number is calculated

### 4.1 Elo rating
- Every team has one number. New FBS teams start at **1500** and new FCS teams at **1200**.
- **Expected result** of A vs B: `E = 1 / (1 + 10^((R_B − R_A) / 400))`.
  A 100-point Elo edge gives A a 64% expected result.
- **After each game:** `change = K × MOV × (actual − E)`, where
  - `K = 25` sets how fast ratings move,
  - `actual` = 1 for a win and 0 for a loss,
  - `MOV = ln(|margin| + 1) × 2.2 / (0.001 × winner's Elo edge + 2.2)` makes
    big wins count more, but not as much more when a heavy favorite wins big.
  - Whatever one team gains, the other loses (zero-sum).
- The home team gets **+2.5 points × 25 = 62.5 Elo** when computing `E`.
  Neutral-site games get none.
- **Seeding from the prior season:** each offseason, every rating moves 1/3
  of the way back toward the average (1650 → 1600). This accounts for roster turnover.

### 4.2 Opponent-adjusted EPA/play
- **EPA** (expected points added) per play measures how much each play changed
  a team's expected points. CFBD calls it "PPA".
- Raw EPA flatters teams that played weak schedules. The adjustment repeats this 5 times:
  - offense rating = average of (game offensive EPA − league average − that opponent's defense rating)
  - defense rating = the same calculation, on the defensive side
- Result:
  - `adj_off`: higher is better
  - `adj_def`: EPA allowed, so lower is better
- Only **this season's games played so far** are used.

### 4.3 Predicted spread
```
Elo margin = (R_home − R_away) / 25 + home_field
EPA margin = 65 plays × [(adj_off_home + adj_def_away) − (adj_off_away + adj_def_home)] + home_field
EPA weight = 0.4 × min(games played by the team with fewer games, 6) / 6
             (0 before either team has a game; 0.4 by week 6)
Margin     = (1 − weight) × Elo margin + weight × EPA margin + injury adjustment
Spread     = −Margin   (betting convention: "Georgia −7" means Georgia favored by 7)
```

### 4.4 Win probability
`P(home wins) = 1 / (1 + 10^(−Margin × 25 / 400))`. This is the same Elo curve
as above, with the margin converted back into Elo points.

### 4.5 Predicted total
- Each team's expected points = league average + (its points scored − average) + (opponent's points allowed − average).
- Both tilts are shrunk toward average by `games / (games + 4)`, so one weird game doesn't dominate.
- Total = the sum for both teams.
- The total is not used in the FLAG rule.

### 4.6 Injuries (`injuries.csv`, optional; you fill it in by hand)
```
team,player,position,status,impact_points,entered
Georgia,Example Player,QB,out,6.0,2026-10-14 09:30
```
- `impact_points` = margin the team loses if the player is fully out.
- The status scales it: out 100%, doubtful 75%, questionable 50%, probable 10%.
- `entered` (optional) = when you added the row, Eastern time (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`).
  Rows older than 48 hours are ignored (college and NFL), because the market has priced the news by then
  and applying it on top of the market would count it twice. Rows without a date never expire.
- Home injuries lower the home margin; away injuries raise it.
- See `injuries.example.csv`. The `team` name must match CFBD's spelling.

### 4.7 Removing the vig and the market consensus
- American odds → implied probability:
  - `−150` → `150/250 = 60.0%`
  - `+130` → `100/230 = 43.5%`
- The two sides add up to **more than 100%**. The extra (here 3.5%) is the
  book's cut, called the **vig**.
- Remove it by dividing each side by the total: `60.0 / 103.5 = 58.0%`.
- **Consensus** = average of every book's no-vig home probability.
- **Market spread** = median home spread across books.

### 4.8 Kalshi fee and edge
- A Kalshi YES contract costs `P` dollars and pays $1 if the team wins.
- **Fee per contract = 0.07 × P × (1 − P)**. At P = $0.50 that is $0.0175.
  (Kalshi rounds an order's total fee up to the next cent.)
- **Edge after fees = model probability − P − fee**. This is the expected profit per $1 contract.
- `P` is the **ask** (the price you'd actually pay to buy). The edge is computed
  for both teams, and the table shows the better side.

### 4.9 The FLAG rule
**FLAG** only if both are true:
1. Edge after fees ≥ **3 percentage points**, and
2. |model win% − market consensus win%| ≤ **10 points**.

A bigger gap usually means the model is missing something (injuries, weather,
a QB change), not that the market is wrong. Games with no Kalshi market show
"no market" and are always PASS.

### 4.10 Parlays
- Fair probability = product of the legs (assumes they are independent):
  `0.60 × 0.70 = 0.42`.
- Fair American odds: `+138` (= 100 × 0.58 / 0.42).
- Offered price = your `--offered` number, or else each leg's average book
  decimal moneyline multiplied together (how books price standard parlays).
- **EV per $1 = P × decimal odds − 1.**
- Two legs from the same game are **correlated**, so multiplying is wrong. The
  tool prints a warning when that happens.

## 5. Worked example (made-up inputs to show the math)

Every input below is invented for illustration. It is not real data.
"Home U" hosts "Away State"; both have played 6+ games.

| Input | Value |
|---|---|
| Elo | Home U 1650, Away State 1580 |
| Adjusted EPA | Home U off +0.10, def −0.05; Away State off +0.06, def +0.01 |
| Book A moneyline | Home −200 / Away +170 |
| Book B moneyline | Home −210 / Away +175 |
| Kalshi YES ask | Home U $0.64, Away State $0.38 |

**Step 1: Elo margin**
`(1650 − 1580)/25 + 2.5 = 2.8 + 2.5 = 5.3` points for Home U.

**Step 2: EPA margin**
`65 × [(0.10 + 0.01) − (0.06 + (−0.05))] + 2.5 = 65 × 0.10 + 2.5 = 9.0`.

**Step 3: Blend**
Weight = 0.4 (both teams have 6+ games), so `0.6 × 5.3 + 0.4 × 9.0 = 6.78`.
**Model spread: Home U −6.8.**

**Step 4: Win probability**
`1 / (1 + 10^(−6.78 × 25/400)) = 72.6%`.

**Step 5: Market consensus**
- Book A: 66.7% + 37.0% = 103.7% → no-vig home = 66.7 / 103.7 = **64.3%**
- Book B: 67.7% + 36.4% = 104.1% → no-vig home = **65.1%**
- Consensus = (64.3 + 65.1)/2 = **64.7%**

**Step 6: Kalshi edge (Home U YES at $0.64)**
- Fee = `0.07 × 0.64 × 0.36 = $0.0161`
- Edge = `0.726 − 0.640 − 0.016 = +0.070` → **+7.0 points**
- The Away State side: `0.274 − 0.38 − fee` is negative.

**Step 7: Rule check**
- Edge 7.0 ≥ 3 ✔
- Model − market gap = 72.6 − 64.7 = 7.9 ≤ 10 ✔
- → **FLAG**

**Twist: add an injury.** Suppose you list Away State's QB as `out`, with
`impact_points = 5`:
- Margin = 6.78 + 5 = 11.78 → win probability **84.5%**.
- The edge grows to 18.9 points, but the gap to the market is now 19.8 > 10 → **PASS**.
- This is the rule doing its job. If the market already knows about the injury,
  it's priced in; counting it again on top of the market makes the model overconfident.

## 6. Backtest method (no lookahead)
- 2021–2022 only warm up the ratings and are never scored.
- For 2023, 2024, and 2025, each week is **predicted first**, using only
  earlier games. Then that week's results are fed in.
- Scored games: completed FBS-vs-FBS only.
- Closing lines come from CFBD `/lines`:
  - closing spread = median across providers
  - market probability = average no-vig moneyline
- Reported:
  - straight-up accuracy, Brier score, and log loss
  - ATS record vs the closing spread (break-even ≈ 52.4% at −110)
  - a calibration table
  - a head-to-head with the closing market on Brier score
- If the model is worse than the market, the report says so in plain words.
- All parameters in `config.py` were set before any backtest was run and
  were not tuned on 2023–2025.

## 7. Sources and timestamps
- Each cached response stores its fetch time (UTC).
- The `week` command prints a second table, keyed by game ID, that names the
  source and timestamp behind every number:
  - CFBD fetch time for the model columns
  - The Odds API's latest book update and number of books for the market columns
  - Kalshi event ticker and fetch time for the Kalshi column
- Cache lifetimes: odds 30 min, Kalshi 5 min, current-season CFBD 6 h,
  finished seasons forever.

## 8. Tests
```bash
python -m pytest -q
```
Covers vig removal, Kalshi fee math, parlay math, Elo updates, and the README worked example.
