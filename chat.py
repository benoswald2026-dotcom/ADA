"""Offline "Ask Hashmark" chat. No AI model and no extra dependencies.

How it answers:
  1. Search: scores every study in data/research.json against the question
     (TF-IDF with a small synonym list) and quotes the best-matching sentences.
  2. Intents: recognizes common question types (best bets, a team, a parlay,
     stake sizing, Kalshi fees, model accuracy, weather, CLV) and answers them
     from the app's own live numbers.
Everything shown comes from those two places, with its source. Nothing is
made up, and nothing places a bet.
"""
import json
import math
import os
import re
from collections import Counter

import config
from names import normalize

RESEARCH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "research.json")

STOP = set("""a an the and or of to in on for is are was were be been it its this that these those
what which who whom how why when where do does did can could should would will i me my we you your
with at by from as about into than then so if not no yes any some there their them they he she his
her our us vs versus get got make makes made tell show give use used much many more most""".split())

# Words people use -> words the studies use (query expansion).
SYNONYMS = {
    "parlay": ["correlated", "independent", "legs", "compounding"],
    "size": ["kelly", "stake", "bankroll"], "stake": ["kelly", "bankroll", "size"],
    "bankroll": ["kelly", "stake"], "bet": ["wager", "betting"], "much": ["kelly", "stake"],
    "longshot": ["favorite", "cheap", "bias"], "underdog": ["longshot", "cheap", "bias"],
    "cheap": ["longshot", "bias"], "kalshi": ["prediction", "contract", "maker", "taker"],
    "fee": ["kalshi", "cost"], "market": ["odds", "public", "closing"],
    "trust": ["combination", "market", "model"], "combine": ["combination", "logit", "market"],
    "overfit": ["mining", "multiple", "testing", "trials", "selection"],
    "backtest": ["overfitting", "trials", "sharpe", "testing"], "edge": ["predictability", "profit", "returns"],
    "decay": ["publication", "predictability"], "momentum": ["winners", "losers", "form"],
    "hot": ["momentum", "form"], "streak": ["momentum", "form"], "form": ["momentum"],
    "spread": ["margin", "normal", "deviation"], "cover": ["spread", "margin"],
    "clv": ["closing", "line", "value"], "closing": ["clv", "line"], "sharp": ["closing", "pinnacle"],
    "risk": ["leverage", "correlation", "drawdown"], "leverage": ["ltcm", "risk"],
    "renaissance": ["medallion"], "simons": ["medallion"], "medallion": ["signals", "breadth"],
    "stock": ["returns", "predictability"], "elo": ["rating"], "rating": ["elo"],
    "horse": ["benter", "racetrack"], "vig": ["bookmakers", "cost"], "bookie": ["bookmakers"],
}

QUICK_QUESTIONS = [
    "What are the best bets right now?",
    "How much should I bet?",
    "Should I bet a parlay?",
    "Is the model better than the market?",
    "Why avoid cheap Kalshi contracts?",
    "What does wind do to totals?",
    "How do player props work here?",
]


# ------------------------------------------------------------- search -----

def _stem(w):
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [_stem(w) for w in words if w not in STOP and len(w) > 1]


def load_research():
    with open(RESEARCH_PATH) as f:
        return json.load(f)["studies"]


class ResearchIndex:
    """Tiny TF-IDF search over the research library."""
    FIELDS = ("title", "finding", "why_it_worked", "used_in", "domain", "authors")

    def __init__(self, studies):
        self.studies = studies
        self.docs = [Counter(tokens(" ".join(str(s.get(f, "")) for f in self.FIELDS) + " " + s["id"].replace("-", " ")))
                     for s in studies]
        n = len(self.docs)
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.norms = [math.sqrt(sum((tf * self.idf[t]) ** 2 for t, tf in d.items())) for d in self.docs]

    def search(self, question, k=3, min_score=0.08):
        q = tokens(question)
        expanded = list(q)
        for w in q:
            expanded += [_stem(x) for x in SYNONYMS.get(w, [])]
        qv = Counter(expanded)
        qn = math.sqrt(sum((c * self.idf.get(t, 0)) ** 2 for t, c in qv.items())) or 1
        scored = []
        for i, d in enumerate(self.docs):
            dot = sum(c * self.idf.get(t, 0) * d.get(t, 0) * self.idf.get(t, 0) for t, c in qv.items())
            score = dot / (qn * (self.norms[i] or 1))
            if score >= min_score:
                scored.append((score, i))
        scored.sort(reverse=True)
        out = []
        for score, i in scored[:k]:
            s = self.studies[i]
            out.append({"id": s["id"], "title": s["title"], "authors": s["authors"], "year": s["year"],
                        "url": s["url"], "confidence": s["confidence"], "score": round(score, 3),
                        "snippet": best_sentence(s, set(expanded))})
        return out


def best_sentence(study, qset):
    """The sentence from the study that overlaps the question most."""
    best, best_n = study["finding"], -1
    for field in ("finding", "why_it_worked", "used_in"):
        for sent in re.split(r"(?<=[.!?])\s+", study.get(field, "")):
            n = len(qset & set(tokens(sent)))
            if n > best_n:
                best, best_n = sent, n
    return best


_INDEX = None


def index():
    global _INDEX
    if _INDEX is None:
        _INDEX = ResearchIndex(load_research())
    return _INDEX


# ------------------------------------------------------------ helpers -----

def _pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def _has(text, *words):
    return any(re.search(rf"\b{w}", text) for w in words)


def find_teams(question, rows):
    """Return [(row, side)] for teams named in the question, in the order mentioned."""
    q = " " + normalize(re.sub(r"[^\w&().' ]", " ", question)) + " "
    hits = []
    for r in rows:
        g = r["game"]
        for side in ("home", "away"):
            names = {normalize(g[side])}
            code = g.get(f"{side}_code")
            if code:
                import nfl_teams
                names |= {a for a in nfl_teams.aliases(code) if len(a) > 2}
            for n in names:
                pos = q.find(" " + n + " ")
                if n and pos >= 0:
                    hits.append((pos, r, side))
                    break
    hits.sort(key=lambda h: h[0])
    seen, out = set(), []
    for _, r, side in hits:
        key = (r["game"]["id"], side)
        if key not in seen:
            seen.add(key)
            out.append((r, side))
    return out


def game_facts(r, side):
    """Plain-language facts about one team's game, all from the slate."""
    g, p, m = r["game"], r["pred"], r.get("market") or {}
    home = side == "home"
    team, opp = g[side], g["away" if home else "home"]
    raw = p.get("raw_home_prob", p["home_prob"])
    raw, bet = (raw, p["home_prob"]) if home else (1 - raw, 1 - p["home_prob"])
    mkt = m.get("home_prob")
    mkt = None if mkt is None else (mkt if home else 1 - mkt)
    lines = [f"{team} {'vs' if home else 'at'} {opp}: raw model {_pct(raw)}, market {_pct(mkt)}, "
             f"bet probability {_pct(bet)}."]
    if mkt is not None and abs(raw - mkt) > config.MAX_MODEL_MARKET_GAP:
        lines.append(f"The model and market disagree by {abs(raw - mkt) * 100:.0f} points, more than the 10-point "
                     "limit, so this game is left out. A gap that big usually means news the model hasn't seen "
                     "(often a quarterback change).")
    k = (r.get("kalshi") or {}).get(side)
    if k and k.get("yes_ask"):
        from kalshi import edge_after_fee
        lines.append(f"Kalshi YES ask {k['yes_ask'] * 100:.0f}¢ → edge after fee {edge_after_fee(bet, k['yes_ask']) * 100:+.1f} pts"
                     f" ({'FLAG' if r.get('flag') and r.get('best') and r['best'][1] == team else 'PASS'}).")
    else:
        lines.append("No Kalshi price for this game right now.")
    env = r.get("env") or {}
    if env.get("forecast"):
        fx = env["forecast"]
        lines.append(f"Kickoff forecast: {fx['temp']:.0f}°F, wind {fx['wind']:.0f} mph.")
    if p.get("injury_adj"):
        lines.append(f"Your injuries.csv moves this game {p['injury_adj']:+.1f} pts toward {g['home']}.")
    src = r.get("sources", {})
    sources = [v for k_, v in src.items() if k_ in ("model", "market", "kalshi")]
    return lines, sources


# ------------------------------------------------------------- answer -----

def find_players(question, props_rows):
    """Players named in the question (full name, or a last name that is unique)."""
    q = " " + normalize(re.sub(r"[^\w&().' ]", " ", question)) + " "
    names = sorted({r["player"] for r in props_rows})
    last = {}
    for n in names:
        last.setdefault(normalize(n).split()[-1], []).append(n)
    hits = [n for n in names if " " + normalize(n) + " " in q]
    for ln, ns in last.items():
        if len(ns) == 1 and len(ln) > 3 and " " + ln + " " in q and ns[0] not in hits:
            hits.append(ns[0])
    return hits


def answer(question, rows=None, info=None, backtest=None, bankroll=1000.0, slate_error=None, props_rows=None):
    """Build an answer. rows/info = current slate (or None), backtest = finished
    backtest summary (or None). Returns a dict the web app renders."""
    q = (question or "").strip()
    ql = q.lower()
    out = {"question": q, "answer": [], "facts": [], "sources": [], "citations": [], "suggestions": []}
    if not q:
        out["answer"].append("Ask about this week's bets, a team, a parlay, stake sizing, Kalshi fees, "
                             "or anything in the research library.")
        out["suggestions"] = QUICK_QUESTIONS
        return out
    rows = rows or []
    teams = find_teams(q, rows) if rows else []
    handled = False

    players = find_players(q, props_rows) if props_rows else []
    if players or _has(ql, "prop", "player"):
        handled = True
        if players:
            for name in players[:2]:
                for x in [r for r in props_rows if r["player"] == name]:
                    line = (f"{name} ({x['team']}) {x['label'].lower()}: median {x['median']:.1f}, middle 50% "
                            f"{x['q25']:.0f}–{x['q75']:.0f}, fair line {x['fair_line']}.")
                    if x.get("market"):
                        line += (f" Book line {x['market']['line']}: model P(over) {x['p_over']:.0%} vs market "
                                 f"{x['market']['p_over']:.0%} → {x['pick']} EV {x['ev']:+.3f}"
                                 + (" (FLAG, unvalidated)" if x.get("flag") else "") + ".")
                    out["facts"].append(line)
            out["answer"].append("Player projections from the props model (calibrated on 2023–2025: its 25/50/75% "
                                 "points held about 24/50/75% of the time). Prop edges are unvalidated because "
                                 "there's no free history of prop lines.")
            out["sources"].append("nflverse player stats; props model (props.py)")
        elif props_rows is None:
            out["answer"].append("Open the Props tab once to load today's player projections, then ask about a player "
                                 "by name, e.g. \"Bijan Robinson rushing\".")
        else:
            out["answer"].append("Name a player from today's games, e.g. \"Jared Goff passing yards\".")

    if _has(ql, "clv", "closing line", "paper trade"):
        handled = True
        from strategy import clv_report
        r = clv_report()
        if r["scored"]:
            out["answer"].append(f"{r['scored']} paper trades scored: average CLV EV {r['avg_clv_ev']:+.3f} per $1, "
                                 f"beat the closing price {r['beat_close_rate']:.0%} of the time.")
            out["sources"].append(r["source"])
        else:
            out["answer"].append("No paper trades have been scored yet. The Best bets tab logs every recommended bet; "
                                 "after the games, `python main.py clv` compares each price with the closing line.")

    if _has(ql, "accura", "backtest", "better than", "beat the market", "beats the market", "how good",
            "trust the model", "does the model", "is the model"):
        handled = True
        if backtest:
            vm = backtest["vs_market"]
            out["answer"].append(backtest["verdict"])
            out["facts"].append(f"Test seasons: bet-probability Brier {vm['model']['brier']:.4f} vs closing market "
                                f"{vm['market']['brier']:.4f} on {vm['games']} games (lower is better).")
            if backtest.get("combo"):
                c = backtest["combo"]
                out["facts"].append(f"Benter-style blend: model weight {c['a2']:+.3f} (t {c['t_model']:+.2f}); "
                                    f"used: {c['mode']}. The model gets weight only if t ≥ 3.")
            if backtest.get("betting"):
                b = backtest["betting"]["raw_moneyline"]
                out["facts"].append(f"Betting the raw model at closing moneylines: {b['bets']} bets, ROI {_pct(b['roi'])}"
                                    + (f", t {b['t']:+.2f}" if b["t"] is not None else "") + ".")
            out["sources"].append(backtest.get("source", "Backtest tab"))
        else:
            out["answer"].append("Run the Backtest tab first (one click). Then I can quote how the model did "
                                 "against the closing market.")

    fee_hit = _has(ql, "fee")
    if fee_hit:
        handled = True
        from kalshi import kalshi_fee
        m = re.search(r"(\d{1,2})\s*(?:c\b|¢|cent)|0?\.(\d{2})", ql)
        price = (int(m.group(1)) / 100 if m.group(1) else int(m.group(2)) / 100) if m else 0.50
        out["answer"].append(f"Kalshi's fee is 0.07 × P × (1 − P) per contract. At {price * 100:.0f}¢ that's "
                             f"${kalshi_fee(price):.4f}, so the contract really costs ${price + kalshi_fee(price):.4f}.")
        out["facts"].append("The fee is largest at 50¢ (1.75¢) and shrinks toward 0¢ and 100¢.")

    if _has(ql, "kelly", "how much", "stake", "bankroll", "size"):
        handled = True
        from strategy import KELLY_FRACTION, MAX_BET_FRACTION, MAX_GAME_FRACTION, MAX_SLATE_FRACTION
        out["answer"].append(f"Stakes are {KELLY_FRACTION:g} × Kelly, capped at {MAX_BET_FRACTION:.0%} of bankroll per bet, "
                             f"{MAX_GAME_FRACTION:.0%} per game and {MAX_SLATE_FRACTION:.0%} per slate. With a "
                             f"${bankroll:,.0f} bankroll the most on one bet is ${bankroll * MAX_BET_FRACTION:,.0f}.")
        out["facts"].append("Full Kelly = (p × decimal odds − 1) / (decimal odds − 1). Fractional Kelly is used because "
                            "errors in the estimated edge make full Kelly over-bet.")

    if _has(ql, "parlay"):
        handled = True
        if len(teams) >= 2 and rows:
            from week import ParlayInputError, price_parlay
            try:
                pr = price_parlay(rows, [(r["game"]["id"], r["game"][s]) for r, s in teams[:3]])
                mo, off = pr["model"], pr["offered"]
                out["answer"].append(f"{' + '.join(l['team'] for l in pr['legs'])}: chance to hit {_pct(mo['prob'])}, "
                                     f"fair price {mo['american']:+.0f}."
                                     + (f" Offered {off['american']:+.0f} → EV {off['ev']:+.3f} per $1." if off else ""))
                if pr["warning"]:
                    out["answer"].append(pr["warning"])
            except ParlayInputError as e:
                out["answer"].append(str(e))
        else:
            out["answer"].append("A parlay multiplies the legs' chances, and the house margin compounds with every leg. "
                                 "So most parlays lose more over time than single bets. Name 2–3 teams and I'll price one.")

    if teams and not _has(ql, "parlay"):
        handled = True
        if not out["answer"]:
            out["answer"].append("Here's what the app has on " + ", ".join(r["game"][sd] for r, sd in teams[:3]) + ":")
        for r, side in teams[:3]:
            lines, srcs = game_facts(r, side)
            out["facts"] += lines
            out["sources"] += srcs

    if _has(ql, "best bet", "what should i bet", "recommend", "pick", "good bet", "any bet", "flag") and rows:
        handled = True
        from bets import find_bets
        b = find_bets(rows, bankroll=bankroll)
        flags = [k for k in b["kalshi"] if k["flag"]]
        staked = [x for x in b["book"] + b.get("spreads", []) if x.get("stake")]
        if flags or staked:
            for k in flags[:3]:
                out["answer"].append(f"Kalshi: {k['team']} YES at {k['price'] * 100:.0f}¢, edge {k['edge'] * 100:+.1f} pts, "
                                     f"suggested stake ${k['stake']:,.2f}.")
            for x in staked[:3]:
                out["answer"].append(f"{x['market_type'].title()}: {x['team']} at {x['decimal']:.2f}, EV {x['ev']:+.3f}, "
                                     f"stake ${x['stake']:,.2f}.")
        else:
            out["answer"].append("Nothing qualifies right now: no Kalshi edge of 3 points or more, and no book price beats the "
                                 "bet probability after the vig.")
        if info and info.get("bet_basis"):
            out["facts"].append(f"Bet probability = {info['bet_basis']}. In the backtest the model didn't earn any weight "
                                "beyond the market, so edges can only come from Kalshi prices that disagree with the "
                                "sportsbook consensus, or from your injuries.csv.")
        if b["excluded_gap"]:
            out["facts"].append("Left out (model and market more than 10 points apart): " + "; ".join(b["excluded_gap"]) + ".")

    if _has(ql, "wind", "weather", "cold", "rain"):
        handled = True
        env = (info or {}).get("env")
        if env and env.get("total", {}).get("wind_x"):
            w = env["total"]["wind_x"]
            out["answer"].append(f"Wind: {w['coef']:+.2f} points of total per mph above {config.WIND_THRESHOLD_MPH} "
                                 f"(t {w['t']:+.1f}, fitted on {env['seasons'][0]}–{env['seasons'][1]}). "
                                 "Closing totals already account for it, so it hasn't produced a betting edge.")
        else:
            out["answer"].append("Wind is the only weather factor that passed the t ≥ 3 test (about −0.4 points of total per "
                                 "mph above 10). Cold and rain did not pass. Switch to NFL for the live numbers.")

    if slate_error and (teams or _has(ql, "best bet", "recommend", "parlay")):
        out["facts"].append(f"Live slate unavailable: {slate_error}")

    out["citations"] = index().search(q, k=3)
    if not handled:
        if out["citations"]:
            out["answer"].append("Here's what the research library says:")
        else:
            out["answer"].append("I couldn't match that to the research library or the app's data. Try one of these:")
            out["suggestions"] = QUICK_QUESTIONS
    out["answer"].append("Hashmark only reads data and never places bets. Not betting advice.")
    out["sources"] = list(dict.fromkeys(out["sources"]))
    return out
