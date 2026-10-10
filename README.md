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
team,player,position,status,impact_points
Georgia,Example Player,QB,out,6.0
```
- `impact_points` = margin the team loses if the player is fully out.
- The status scales it: out 100%, doubtful 75%, questionable 50%, probable 10%.
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
Covers vig removal, Kalshi fee math, parlay math, and Elo updates.
