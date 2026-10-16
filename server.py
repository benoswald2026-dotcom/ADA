"""Local web app: `python main.py serve`, then open http://127.0.0.1:8000

Uses only Python's built-in http.server (no extra dependencies). Listens on
127.0.0.1, so only your own computer can reach it. Read-only like the CLI:
it never places trades, bets, or orders. API keys never leave the server;
/api/status only reports whether each key is set.
"""
import base64
import hmac
import json
import os
import threading
import traceback
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cache import ApiError
from config import MissingKeyError

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
MAX_BODY = 64 * 1024

# The backtest replays five seasons and can take a while the first time,
# so it runs in a background thread and the page polls for the result.
_backtests = {sp: {"state": "idle", "result": None, "error": None, "started": None} for sp in ("cfb", "nfl")}
_backtest_lock = threading.Lock()


def _sport(path):
    return "nfl" if "sport=nfl" in path else "cfb"


def _json_default(o):
    """NumPy numbers/bools -> plain Python so json.dumps can write them."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def _friendly(e):
    if isinstance(e, (MissingKeyError, ApiError)):
        return str(e)
    return f"Unexpected error: {type(e).__name__}: {e}"


def _run_backtest(sport):
    job = _backtests[sport]
    try:
        if sport == "nfl":
            import nfl
            import props
            s = nfl.run_backtest()
            s["props"] = props.run_backtest()["stats"]
        else:
            from backtest import run_backtest, summarize, verdict
            s = summarize(run_backtest())
            s["verdict"] = verdict(s["vs_market"])
        job.update(state="done", result=s, error=None)
    except Exception as e:  # report to the page instead of crashing the thread
        traceback.print_exc()
        job.update(state="error", error=_friendly(e))


_week_cache = {sp: {"at": 0.0, "value": None} for sp in ("cfb", "nfl")}
_week_lock = threading.Lock()
WEEK_CACHE_SECONDS = 5 * 60


def cached_week(sport="cfb", force=False):
    """force=True (the Refresh button) rebuilds AND re-downloads every live source."""
    if force:
        from cache import fresh
        with fresh():
            return _cached_week(sport, True)
    return _cached_week(sport, False)


def _cached_week(sport="cfb", force=False):
    """Rebuilding the board replays several seasons, so reuse it for a few minutes.
    API responses have their own cache too (cache.py). Returns (info, rows)."""
    import time
    c = _week_cache[sport]
    with _week_lock:
        if force or c["value"] is None or time.time() - c["at"] > WEEK_CACHE_SECONDS:
            if sport == "nfl":
                import nfl
                c["value"] = nfl.build_slate()
            else:
                from week import build_week
                week, rows = build_week()
                c["value"] = ({"sport": "cfb", "notes": week.get("notes", []),
                               "label": f"Week {week['week']}" + (" · Postseason" if week["season_type"] == "postseason" else ""),
                               "week": week}, rows)
            c["at"] = time.time()
        return c["value"]


_props_cache = {"at": 0.0, "value": None}
_props_lock = threading.Lock()


def cached_props(bankroll=1000.0, force=False):
    """Player props for today's NFL slate, rebuilt at most every 5 minutes."""
    import time
    import props
    with _props_lock:
        c = _props_cache
        if force or c["value"] is None or time.time() - c["at"] > WEEK_CACHE_SECONDS or c["value"][0] != bankroll:
            _info, rows = cached_week("nfl", force=force)
            if force:
                from cache import fresh
                with fresh():
                    c["value"] = (bankroll, props.build_props(rows, bankroll=bankroll))
            else:
                c["value"] = (bankroll, props.build_props(rows, bankroll=bankroll))
            c["at"] = time.time()
        return c["value"][1]


def week_json(sport="cfb", force=False):
    info, rows = cached_week(sport, force)
    out = []
    for r in rows:
        best = r["best"]
        out.append({
            "game": r["game"], "pred": {k: v for k, v in r["pred"].items()},
            "market": r["market"],
            "kalshi": None if not r["kalshi"] else {
                side: {"ticker": m["ticker"], "event": m["event_ticker"], "yes_ask": m["yes_ask"],
                       "yes_bid": m["yes_bid"]} for side, m in r["kalshi"].items()},
            "best": None if not best else {"edge": best[0], "team": best[1], "model": best[2],
                                           "market": best[3], "price": best[4]},
            "flag": r["flag"], "sources": r["sources"], "env": r.get("env"),
        })
    return {"info": info, "rows": out,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=_json_default).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(n) or b"{}")

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "Not found"})
        with open(full, "rb") as f:
            self._send(200, f.read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def _authorized(self):
        """If HASHMARK_PASSWORD is set, require it (HTTP Basic auth, any username).
        The host serves over https, so the password is encrypted in transit."""
        password = os.environ.get("HASHMARK_PASSWORD", "")
        if not password or self.path.split("?", 1)[0] == "/healthz":
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                _user, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                if hmac.compare_digest(given.encode(), password.encode()):
                    return True
            except (ValueError, UnicodeDecodeError):
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Hashmark", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/status":
                return self._send(200, {
                    "cfbd_key": bool(os.environ.get("CFBD_API_KEY", "").strip()),
                    "odds_key": bool(os.environ.get("ODDS_API_KEY", "").strip()),
                    "injuries_file": os.path.exists("injuries.csv")})
            if path == "/api/week":
                return self._send(200, week_json(_sport(self.path), force="refresh=1" in self.path))
            if path == "/api/bets":
                from urllib.parse import parse_qs, urlparse
                from bets import find_bets
                q = parse_qs(urlparse(self.path).query)
                try:
                    bankroll = max(0.0, float(q.get("bankroll", ["1000"])[0]))
                except ValueError:
                    bankroll = 1000.0
                sport = _sport(self.path)
                info, rows = cached_week(sport)
                return self._send(200, {"info": info, "bets": find_bets(rows, bankroll=bankroll, log_sport=sport)})
            if path == "/api/props":
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                try:
                    bankroll = max(0.0, float(q.get("bankroll", ["1000"])[0]))
                except ValueError:
                    bankroll = 1000.0
                return self._send(200, cached_props(bankroll, force="refresh=1" in self.path))
            if path == "/api/research":
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "research.json")) as f:
                    return self._send(200, json.load(f))
            if path == "/api/clv":
                from strategy import clv_report
                return self._send(200, clv_report())
            if path == "/api/backtest":
                sport = _sport(self.path)
                job = _backtests[sport]
                with _backtest_lock:
                    if job["state"] in ("idle", "error", "done") and "start=1" in self.path:
                        job.update(state="running", error=None,
                                   started=datetime.now(timezone.utc).strftime("%H:%MZ"))
                        threading.Thread(target=_run_backtest, args=(sport,), daemon=True).start()
                return self._send(200, {**job, "sport": sport})
            if path == "/api/example":
                from explain import EXAMPLE, explain
                return self._send(200, explain(EXAMPLE))
            return self._static(path)
        except (MissingKeyError, ApiError) as e:
            self._send(400 if isinstance(e, MissingKeyError) else 502, {"error": _friendly(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": _friendly(e)})

    def do_POST(self):
        if not self._authorized():
            return
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if path == "/api/explain":
                from explain import explain
                return self._send(200, explain(body))
            if path == "/api/chat":
                import chat
                sport = "nfl" if body.get("sport") == "nfl" else "cfb"
                rows = info = slate_error = None
                try:
                    info, rows = cached_week(sport)
                except Exception as e:  # the research half still works without live data
                    slate_error = _friendly(e)
                job = _backtests[sport]
                try:
                    bankroll = max(0.0, float(body.get("bankroll") or 1000))
                except (TypeError, ValueError):
                    bankroll = 1000.0
                props_rows = _props_cache["value"][1]["rows"] if (sport == "nfl" and _props_cache["value"]) else None
                return self._send(200, chat.answer(str(body.get("message", ""))[:500], rows, info,
                                                   job["result"] if job["state"] == "done" else None,
                                                   bankroll, slate_error, props_rows=props_rows))
            if path == "/api/parlay":
                from week import ParlayInputError, price_parlay
                picks = [(str(l["game_id"]), str(l["team"])) for l in body.get("legs", [])]
                offered = body.get("offered")
                _info, rows = cached_week("nfl" if body.get("sport") == "nfl" else "cfb")
                try:
                    return self._send(200, price_parlay(rows, picks,
                                                        float(offered) if offered not in (None, "") else None))
                except ParlayInputError as e:
                    return self._send(400, {"error": str(e)})
            self._send(404, {"error": "Not found"})
        except (MissingKeyError, ApiError) as e:
            self._send(400 if isinstance(e, MissingKeyError) else 502, {"error": _friendly(e)})
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, {"error": f"Bad input: {e}"})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": _friendly(e)})


def _warm_up():
    """Load today's NFL board and props in the background so the first visitor
    doesn't wait for the whole model fit (slow on small cloud machines)."""
    try:
        cached_week("nfl")
        cached_props()
        print("Warm-up done: NFL board and props are cached.")
    except Exception as e:  # never crash the server over a warm-up
        print(f"Warm-up skipped: {_friendly(e)}")


def serve(port=8000, open_browser=True, host="127.0.0.1", warm=False):
    """Local by default (127.0.0.1). In the cloud, main.py passes host 0.0.0.0
    and the PORT the host assigns."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{'127.0.0.1' if host == '127.0.0.1' else host}:{port}"
    print(f"Serving on {url}  (Ctrl+C to stop)"
          + ("  [password required]" if os.environ.get("HASHMARK_PASSWORD") else ""))
    if host != "127.0.0.1" and not os.environ.get("HASHMARK_PASSWORD"):
        print("WARNING: listening publicly with no HASHMARK_PASSWORD set. Anyone with the URL can use it.")
    if warm:
        threading.Thread(target=_warm_up, daemon=True).start()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
