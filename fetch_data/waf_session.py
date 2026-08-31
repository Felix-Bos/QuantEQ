"""AWS WAF token acquisition for global.morningstar.com.

Morningstar's search endpoint (global.morningstar.com) sits behind an AWS WAF
Bot Control challenge that requires real JavaScript execution to solve — no
amount of header/TLS-fingerprint spoofing on a plain HTTP client gets past it.

This module solves the challenge once with a headless browser (Playwright),
caches the resulting ``aws-waf-token`` cookie to disk, and hands it back to
callers so they can keep making plain HTTP requests (via curl_cffi) without
paying the browser-launch cost on every call. The token is valid for several
days, so the browser only runs when the cache is missing, stale, or rejected.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).resolve().parent / ".waf_session_cache.json"
_WARMUP_URLS = (
    "https://www.morningstar.com/",
    "https://global.morningstar.com/en-gb",
)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_lock = Lock()


def _read_cache() -> dict | None:
    try:
        with _CACHE_PATH.open("r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_cache(cookies: dict) -> None:
    try:
        with _CACHE_PATH.open("w") as f:
            json.dump(cookies, f)
    except OSError as exc:
        logger.warning("Could not write WAF session cache: %s", exc)


def _is_expired(cached: dict) -> bool:
    expires_at = cached.get("expires_at", 0)
    return time.time() >= expires_at - 3600  # refresh an hour early


def _solve_challenge() -> dict | None:
    """Launch a headless browser to solve the WAF challenge, return cookies."""
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        logger.warning(
            "playwright is not installed; cannot solve the Morningstar WAF "
            "challenge. Install it with `pip install playwright` and run "
            "`playwright install chromium`."
        )
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        try:
            context = browser.new_context(
                user_agent=_USER_AGENT, viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            page.add_init_script(
                'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            )
            for url in _WARMUP_URLS:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

            cookies = context.cookies()
            waf_token = next(
                (c for c in cookies if c["name"] == "aws-waf-token"), None
            )
            if waf_token is None:
                logger.warning("WAF challenge did not yield an aws-waf-token cookie.")
                return None

            cookie_dict = {c["name"]: c["value"] for c in cookies}
            expires_values = [
                c["expires"] for c in cookies if c.get("expires", -1) > 0
            ]
            expires_at = min(expires_values) if expires_values else time.time() + 3600
            return {"cookies": cookie_dict, "expires_at": expires_at}
        finally:
            browser.close()


def get_waf_cookies(*, force_refresh: bool = False) -> dict:
    """Return a dict of cookies that satisfy Morningstar's WAF challenge.

    Cached to disk; refreshed automatically when missing, stale, or when
    *force_refresh* is set (e.g. after a caller sees a 202/challenge again).
    Returns an empty dict if the challenge cannot be solved (e.g. Playwright
    is unavailable), so callers should fall back gracefully.
    """
    with _lock:
        cached = None if force_refresh else _read_cache()
        if cached and not _is_expired(cached):
            return cached["cookies"]

        solved = _solve_challenge()
        if solved is None:
            return cached["cookies"] if cached else {}

        _write_cache(solved)
        return solved["cookies"]


_WAF_HOSTS = {"global.morningstar.com", "www.morningstar.com"}
_mstarpy_patched = False


def patch_requests_for_mstarpy() -> None:
    """Make the third-party ``requests`` calls inside mstarpy WAF-aware.

    mstarpy calls ``requests.get(...)`` directly (no shared session) against
    several endpoints behind the same AWS WAF challenge:
      - ``global.morningstar.com`` — e.g. ``Stock(...)`` always resolves the
        security through ``screener_universe()``, which calls
        ``search_field()`` against this domain.
      - ``www.morningstar.com`` — ``token_chart()`` scrapes a bearer token
        out of a fund/stock chart page on this domain before ``TimeSeries()``
        can fetch price history.
    Those calls have no way to carry our solved WAF cookies on their own, so
    this monkey-patches ``requests.api.request`` (what ``requests.get``/
    ``.post`` delegate to) to attach the cached cookies whenever the target
    host is one of the above. Idempotent — safe to call more than once.
    """
    global _mstarpy_patched
    if _mstarpy_patched:
        return

    import requests
    from urllib.parse import urlparse

    original_request = requests.api.request

    def patched_request(method, url, **kwargs):
        if urlparse(url).netloc in _WAF_HOSTS:
            cookies = get_waf_cookies()
            if cookies:
                merged = dict(cookies)
                merged.update(kwargs.get("cookies") or {})
                kwargs["cookies"] = merged
        return original_request(method, url, **kwargs)

    requests.api.request = patched_request
    requests.get = lambda url, **kw: patched_request("get", url, **kw)
    requests.post = lambda url, **kw: patched_request("post", url, **kw)
    _mstarpy_patched = True
