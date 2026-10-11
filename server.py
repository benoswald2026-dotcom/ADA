"""Local web app: `python main.py serve`, then open http://127.0.0.1:8000

Uses only Python's built-in http.server (no extra dependencies). Listens on
127.0.0.1, so only your own computer can reach it. Read-only like the CLI:
it never places trades, bets, or orders. API keys never leave the server;
/api/status only reports whether each key is set.
"""
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
_backtest = {"state": "idle", "result": None, "error": None, "started": None}
_backtest_lock = threading.Lock()


def _friendly(e):
    if isinstance(e, (MissingKeyError, ApiError)):
        return str(e)
    return f"Unexpected error: {type(e).__name__}: {e}"


def _run_backtest():
    from backtest import run_backtest, summarize, verdict
    try:
        s = summarize(run_backtest())
        s["verdict"] = verdict(s["vs_market"])
        _backtest.update(state="done", result=s, error=None)
    except Exception as e:  # report to the page instead of crashing the thread
        traceback.print_exc()
        _backtest.update(state="error", error=_friendly(e))


_week_cache = {"at": 0.0, "value": None}
_week_lock = threading.Lock()
WEEK_CACHE_SECONDS = 5 * 60


def cached_week(force=False):
    """Rebuilding the board replays five seasons, so reuse it for a few minutes.
    API responses have their own cache too (cache.py)."""
    import time
    from week import build_week
    with _week_lock:
        if force or _week_cache["value"] is None or time.time() - _week_cache["at"] > WEEK_CACHE_SECONDS:
            _week_cache["value"] = build_week()
            _week_cache["at"] = time.time()
        return _week_cache["value"]


def week_json(force=False):
    week, rows = cached_week(force)
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
            "flag": r["flag"], "sources": r["sources"],
        })
    return {"week": week, "rows": out,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/status":
                return self._send(200, {
                    "cfbd_key": bool(os.environ.get("CFBD_API_KEY", "").strip()),
                    "odds_key": bool(os.environ.get("ODDS_API_KEY", "").strip()),
                    "injuries_file": os.path.exists("injuries.csv")})
            if path == "/api/week":
                return self._send(200, week_json(force="refresh=1" in self.path))
            if path == "/api/backtest":
                with _backtest_lock:
                    if _backtest["state"] in ("idle", "error") and "start=1" in self.path:
                        _backtest.update(state="running", error=None,
                                         started=datetime.now(timezone.utc).strftime("%H:%MZ"))
                        threading.Thread(target=_run_backtest, daemon=True).start()
                return self._send(200, _backtest)
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
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if path == "/api/explain":
                from explain import explain
                return self._send(200, explain(body))
            if path == "/api/parlay":
                from week import ParlayInputError, price_parlay
                picks = [(str(l["game_id"]), str(l["team"])) for l in body.get("legs", [])]
                offered = body.get("offered")
                _week, rows = cached_week()
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


def serve(port=8000, open_browser=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Serving on {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
