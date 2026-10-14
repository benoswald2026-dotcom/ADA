# Hashmark: project rules for Claude sessions

The owner is a freshman EE student. Keep answers direct, use bullets and
examples, and explain numbers in plain language. Correctness and honesty
matter more than features.

## Hard rules (from the owner)
- **Read-only.** Never place trades, bets, or orders on any platform.
- **Keys only from environment variables** (`CFBD_API_KEY`, `ODDS_API_KEY`,
  `HASHMARK_PASSWORD`). Never hardcode or print them.
- **Data sources: only these.** CollegeFootballData, The Odds API, Kalshi
  public market data (no key), nflverse (GitHub releases), Open-Meteo.
  Don't scrape anything else.
- **Dependencies:** requests, pandas, numpy, python-dotenv, tabulate, pytest.
  **Ask before adding any other.** (The owner declined the `anthropic` SDK.
  The chat is offline on purpose.)
- **Cite source + timestamp** for every number shown.
- **Data updates only when asked:** the app's Refresh buttons or `--fresh`.
  Never add auto-refresh.
- **Stop and ask** if an API errors twice, a needed field doesn't exist, or
  before deleting any file.
- Don't tune parameters on test seasons. Say plainly when the model loses to
  the market.

## Betting rules built into the code (keep them)
- FLAG only if edge after fees ≥ 3 pts AND raw model within 10 pts of market consensus.
- Kalshi fee = 0.07 × P × (1 − P); no FLAG on YES under 20¢ (favorite-longshot bias).
- Features and model weights are used only if |t| ≥ 3 (pre-registered; all trials reported).
- Walk-forward fits only: each season is fitted on earlier seasons.
- Stakes: quarter Kelly, caps 2% per bet / 3% per game / 10% per slate.
- Prop edges are labelled **unvalidated** (no historical prop lines exist).
- Every recommended bet is logged as a paper trade, scored later by CLV (`python main.py clv`).

## Where things are
- `nfl.py` NFL model, backtest, live slate · `props.py` player props ·
  `bets.py` bet finder · `strategy.py` Kelly/CLV/stats · `chat.py` offline chat ·
  `server.py` + `static/` web app · `data/research.json` research library.
- Commands: `python main.py week|bets|props|backtest|parlay|clv|serve`
  (`--sport nfl`, `--fresh`). Tests: `python -m pytest -q` (must all pass).
- Work on branch `claude/college-football-betting-model-wcgtge`.

## Current findings (so answers stay honest)
- NFL 2023–2025 walk-forward: the model ties the closing market (Brier 0.2102 vs 0.2102);
  the fitted model weight is ~0, so **bet probability = market**. Real edges can
  only come from Kalshi prices that disagree with the sportsbooks, or from news
  in `injuries.csv`.
- Only wind passed the t ≥ 3 test (≈ −0.4 total points per mph above 10).
- Props: calibrated (25/50/75% held ≈ 24/50/75%), but unvalidated for profit.
