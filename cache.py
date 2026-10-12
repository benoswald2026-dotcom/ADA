"""Tiny file cache for API responses.

Each response is stored in cache/<hash>.json together with the time it was
fetched. That fetch time is what the week table cites as the timestamp.
API keys are NEVER part of the cache key or the saved file.
"""
import contextlib
import contextvars
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests

import config

SECRET_PARAMS = {"apiKey", "api_key"}


class ApiError(RuntimeError):
    pass


# "Fresh" mode: set while a user-requested refresh runs. Live data (anything
# cached with a TTL) is re-downloaded; finished seasons (ttl=None) are kept.
_FRESH = contextvars.ContextVar("fresh", default=False)


@contextlib.contextmanager
def fresh():
    """with fresh(): ...  -> every live API call inside re-fetches right now."""
    token = _FRESH.set(True)
    try:
        yield
    finally:
        _FRESH.reset(token)


def _still_valid(ttl, age):
    if ttl is None:
        return True              # historical data never changes
    return not _FRESH.get() and age < ttl


def _cache_path(url, params):
    safe = {k: v for k, v in sorted((params or {}).items()) if k not in SECRET_PARAMS}
    raw = url + "?" + json.dumps(safe, sort_keys=True)
    return os.path.join(config.CACHE_DIR, hashlib.sha256(raw.encode()).hexdigest()[:24] + ".json")


def cached_get(url, params=None, headers=None, ttl=None, source="unknown"):
    """GET a JSON URL, using the cache when it is fresh enough.

    ttl=None means "never expires" (used for finished seasons).
    Returns (data, fetched_at_iso_string).
    Tries the request twice; if both fail, raises ApiError (the caller
    should stop and tell the user, per project rules).
    """
    path = _cache_path(url, params)
    if os.path.exists(path):
        with open(path) as f:
            entry = json.load(f)
        age = time.time() - entry["fetched_at_epoch"]
        if _still_valid(ttl, age):
            return entry["data"], entry["fetched_at"]

    last_error = None
    for _attempt in range(2):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                now = datetime.now(timezone.utc)
                os.makedirs(config.CACHE_DIR, exist_ok=True)
                with open(path, "w") as f:
                    json.dump({"source": source, "url": url,
                               "fetched_at": now.strftime("%Y-%m-%d %H:%M:%SZ"),
                               "fetched_at_epoch": now.timestamp(),
                               "data": data}, f)
                return data, now.strftime("%Y-%m-%d %H:%M:%SZ")
            # Do not include the URL: for the Odds API it contains the key.
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as e:
            last_error = type(e).__name__  # message may contain the URL/key
        time.sleep(1)
    raise ApiError(f"{source} request failed twice ({last_error}). Stopping.")


def cached_download(url, ttl=None, source="unknown", suffix=".csv"):
    """Download a file (e.g. a CSV) into the cache. Returns (local path, fetched_at).
    Same two-tries-then-stop rule as cached_get."""
    base = _cache_path(url, None)[:-5]
    path, meta = base + suffix, base + ".meta.json"
    if os.path.exists(path) and os.path.exists(meta):
        with open(meta) as f:
            m = json.load(f)
        if _still_valid(ttl, time.time() - m["fetched_at_epoch"]):
            return path, m["fetched_at"]

    last_error = None
    for _attempt in range(2):
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 200:
                now = datetime.now(timezone.utc)
                os.makedirs(config.CACHE_DIR, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(resp.content)
                with open(meta, "w") as f:
                    json.dump({"source": source, "url": url,
                               "fetched_at": now.strftime("%Y-%m-%d %H:%M:%SZ"),
                               "fetched_at_epoch": now.timestamp()}, f)
                return path, now.strftime("%Y-%m-%d %H:%M:%SZ")
            last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_error = type(e).__name__
        time.sleep(1)
    raise ApiError(f"{source} download failed twice ({last_error}). Stopping.")
