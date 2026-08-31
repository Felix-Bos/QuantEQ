"""Morningstar data client — 3-layer architecture.

Layer 1 — ``StockAPI``:  thin mstarpy wrapper (one ``ms.Stock`` per ISIN).
Layer 2 — ``extract_*``: stateless extractors (raw dict → clean dict).
Layer 3 — ``MorningstarClient``: orchestrator composing L1 + L2.

Public API (unchanged)
----------------------
search_assets           – autocomplete search
fetch_asset_data        – full fundamentals for the detail page
find_comparables        – screener-based peer discovery + multiples

get_valuation_multiples – PE, EV/EBIT, EV/EBITDA
get_base_financials     – EBITDA, EBIT, EPS, shares, net debt
get_dcf_financials      – full 12-year time-series for FCFF DCF
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote

import requests as http_requests
from decouple import config
try:
    from curl_cffi import requests as browser_requests
except ModuleNotFoundError:  # pragma: no cover - optional production hardening
    browser_requests = None

MSTARPY_API_KEY = config("MSTARPY_API_KEY")
_MSTARPY_MODULE = None
_MSTARPY_LOAD_ATTEMPTED = False

from .parsers import DataFormatter, TableParser
from . import waf_session

waf_session.patch_requests_for_mstarpy()

logger = logging.getLogger(__name__)


def _load_mstarpy():
    """Import mstarpy only where its process-level signal handlers are legal."""
    import threading

    if threading.current_thread() is not threading.main_thread():
        return None

    global _MSTARPY_LOAD_ATTEMPTED, _MSTARPY_MODULE
    if not _MSTARPY_LOAD_ATTEMPTED:
        _MSTARPY_LOAD_ATTEMPTED = True
        try:
            import mstarpy

            _MSTARPY_MODULE = mstarpy
        except Exception as exc:
            logger.warning("mstarpy import failed: %s", exc)
    return _MSTARPY_MODULE

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_SEARCH_URL = "https://global.morningstar.com/api/v1/en-gb/legacy-search/securities"
_MS_WEBSITE_BASE = "https://www.morningstar.com"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SEARCH_HEADERS = {
    "User-Agent": _BROWSER_USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://global.morningstar.com/en-gb",
}
_SEARCH_UNIVERSE_TYPE = {
    "EQ": "STOCK",
    "FE": "ETF",
    "FO": "FUND",
    "FC": "FUND",
    "FM": "FUND",
    "FV": "FUND",
    "V1": "FUND",
    "XI": "FUND",
}
_MS_BASE_PARAMS = {"clientId": "MDC", "version": "4.71.0"}
_MS_HEADERS = {
    "apikey": MSTARPY_API_KEY,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://global.morningstar.com",
    "Referer": "https://global.morningstar.com/",
    "User-Agent": _BROWSER_USER_AGENT,
}
_MS_STOCK_API_BASE = "https://api-global.morningstar.com/sal-service/v1/stock"
_MS_FUND_API_BASE = "https://api-global.morningstar.com/sal-service/v1"
_MS_REALTIME_BASE = "https://www.morningstar.com/api/v2/stores/realtime"

_PAGE_EXCHANGE_ALIASES: dict[str, tuple[str, ...]] = {
    "LSE": ("xlon", "lse"),
    "MIL": ("xmil", "mil"),
    "PAR": ("xpar", "par"),
    "SWX": ("xswx", "swx"),
    "GER": ("xetr", "xfra"),
}

_VALUATION_IDS: dict[str, str] = {
    "price.earnings.label": "pe",
    "enterprise.value.ebit.label": "ev_to_ebit",
    "enterprise.value.ebitda.label": "ev_to_ebitda",
}

_PREFERRED_EXCHANGES = ("XPAR", "XAMS", "XLON", "XFRA", "XNYS", "XNAS")

_COUNTRY_ISO3: dict[str, str] = {
    "france": "FRA",
    "united states": "USA",
    "united kingdom": "GBR",
    "germany": "DEU",
    "netherlands": "NLD",
    "switzerland": "CHE",
    "sweden": "SWE",
    "spain": "ESP",
    "italy": "ITA",
    "japan": "JPN",
    "canada": "CAN",
    "australia": "AUS",
    "china": "CHN",
    "india": "IND",
    "brazil": "BRA",
    "south korea": "KOR",
    "hong kong": "HKG",
    "denmark": "DNK",
    "norway": "NOR",
    "finland": "FIN",
    "belgium": "BEL",
    "austria": "AUT",
    "portugal": "PRT",
    "ireland": "IRL",
    "luxembourg": "LUX",
    "singapore": "SGP",
    "new zealand": "NZL",
    "taiwan": "TWN",
    "israel": "ISR",
    "mexico": "MEX",
    "south africa": "ZAF",
    "indonesia": "IDN",
    "thailand": "THA",
    "malaysia": "MYS",
    "poland": "POL",
    "turkey": "TUR",
    "greece": "GRC",
}

_COUNTRY_NAME_BY_ISO3: dict[str, str] = {
    value: key.title()
    for key, value in _COUNTRY_ISO3.items()
}

_ISO3_TO_ECONOMY: dict[str, str] = {
    # Europe
    "FRA": "Europe", "GBR": "Europe", "DEU": "Europe", "NLD": "Europe",
    "CHE": "Europe", "SWE": "Europe", "ESP": "Europe", "ITA": "Europe",
    "DNK": "Europe", "NOR": "Europe", "FIN": "Europe", "BEL": "Europe",
    "AUT": "Europe", "PRT": "Europe", "IRL": "Europe", "LUX": "Europe",
    "POL": "Europe", "GRC": "Europe", "TUR": "Europe",
    # North America
    "USA": "North America", "CAN": "North America",
    # Asia Pacific
    "JPN": "Asia Pacific", "CHN": "Asia Pacific", "IND": "Asia Pacific",
    "KOR": "Asia Pacific", "HKG": "Asia Pacific", "SGP": "Asia Pacific",
    "TWN": "Asia Pacific", "THA": "Asia Pacific", "MYS": "Asia Pacific",
    "IDN": "Asia Pacific", "AUS": "Asia Pacific", "NZL": "Asia Pacific",
    # Latin America
    "BRA": "Latin America", "MEX": "Latin America",
    # Africa & Middle East
    "ZAF": "Africa & Middle East", "ISR": "Africa & Middle East",
}

_COMP_FILTER_DEFAULTS: dict[str, Any] = {
    "mc_min_pct": 10,    # keep peers with MC >= 10% of base
    "mc_max_pct": 1000,  # keep peers with MC <= 10× base
    "pe_min_pct": 50,    # keep peers with PE >= 50% of base
    "pe_max_pct": 150,   # keep peers with PE <= 150% of base  (±50% window)
    "pb_min_pct": 10,    # keep peers with PB >= 10% of base
    "pb_max_pct": 1000,  # keep peers with PB <= 10× base
}

_DCF_KEYS = (
    "revenue",
    "ebit",
    "ebitda",
    "da",
    "capex",
    "delta_nwc",
    "tax_rate",
    "fcff",
    "shares_m",
    "cash",
    "total_debt",
    "net_debt",
)

# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════


def safe_float(v: Any) -> float | None:
    """Coerce any value to float, returning ``None`` for sentinels / errors."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
            return None if f != f else f  # NaN check
        except (OverflowError, ValueError):
            return None
    s = str(v).strip()
    if s in ("", "_PO_", "N/A", "\u2014", "nan", "None"):
        return None
    try:
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


def last_value(data_list: list[dict], *keys: str) -> float | None:
    """Most recent non-None float for any of *keys* in a dataList."""
    for entry in reversed(data_list):
        for key in keys:
            v = safe_float(entry.get(key))
            if v is not None:
                return v
    return None


def series_from(
    data_list: list[dict],
    n: int,
    *keys: str,
) -> list[float | None]:
    """Build a time-series of length *n*, trying *keys* in priority order."""
    result: list[float | None] = []
    for entry in data_list or []:
        val = None
        for key in keys:
            v = safe_float(entry.get(key))
            if v is not None:
                val = v
                break
        result.append(val)
    if len(result) < n:
        result += [None] * (n - len(result))
    return result[:n]


def pct_bound(base: float | None, pct: float | None) -> float | None:
    """``base * pct / 100`` if both are set."""
    if base is None or pct is None:
        return None
    try:
        return base * float(pct) / 100
    except (ValueError, TypeError):
        return None


def _sf(item: dict, key: str) -> Any:
    """Extract a value from the screener's nested ``fields`` structure."""
    try:
        return item["fields"][key]["value"]
    except (KeyError, TypeError):
        return None


def _exchange_rank(exchange: str) -> int:
    try:
        return _PREFERRED_EXCHANGES.index(exchange)
    except ValueError:
        return len(_PREFERRED_EXCHANGES)


def _looks_like_isin(term: str) -> bool:
    return bool(term) and len(term) == 12 and term[:2].isalpha()


def _http_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
):
    merged_headers = headers or {}
    if browser_requests is not None:
        return browser_requests.get(
            url,
            params=params,
            headers=merged_headers,
            impersonate="chrome124",
            timeout=timeout,
        )
    return http_requests.get(url, params=params, headers=merged_headers, timeout=timeout)


def _search_assets_http(query: str, limit: int = 12) -> list[dict]:
    """Search Morningstar via the legacy-search API, behind an AWS WAF challenge.

    The search endpoint requires a solved ``aws-waf-token`` cookie (see
    ``waf_session.py``). On a rejected/empty response we force one refresh
    and retry once, since the cached token may have been revoked server-side.
    """
    escaped_query = query.replace('"', '\\"')
    params = {
        "fields": "isin,name,ticker,exchange",
        "limit": limit,
        "page": 1,
        "query": f'name ~= "{escaped_query}"',
    }

    for force_refresh in (False, True):
        cookies = waf_session.get_waf_cookies(force_refresh=force_refresh)
        if not cookies:
            return []
        if browser_requests is not None:
            resp = browser_requests.get(
                _SEARCH_URL,
                params=params,
                headers=_SEARCH_HEADERS,
                cookies=cookies,
                impersonate="chrome124",
                timeout=15,
            )
        else:
            resp = http_requests.get(
                _SEARCH_URL, params=params, headers=_SEARCH_HEADERS,
                cookies=cookies, timeout=15,
            )
        if resp.status_code == 200 and resp.text:
            return _parse_search_response(resp.text)
        logger.debug(
            "Morningstar search rejected (status=%s, refreshed=%s); retrying",
            resp.status_code, force_refresh,
        )

    return []


def _pick_best_search_result(
    query: str,
    results: list[dict],
    *,
    exchange: str | None = None,
    allowed_types: tuple[str, ...] | None = None,
) -> dict | None:
    query_upper = (query or "").strip().upper()
    exchange_upper = (exchange or "").strip().upper()
    best_item = None
    best_score = None
    for item in results:
        item_type = (item.get("type") or "").upper()
        if allowed_types and item_type not in allowed_types:
            continue
        item_exchange = (item.get("exchange") or "").upper()
        item_ticker = (item.get("ticker") or "").upper()
        item_name = (item.get("name") or "").upper()
        item_sec_id = (item.get("secId") or "").upper()
        score = 0
        if item_type == "STOCK":
            score += 200
        elif item_type == "ETF":
            score += 180
        elif item_type == "FUND":
            score += 170
        if item_sec_id == query_upper:
            score += 120
        if item_ticker == query_upper:
            score += 80
        if item_name == query_upper:
            score += 50
        if exchange_upper and item_exchange == exchange_upper:
            score += 40
        score -= _exchange_rank(item_exchange)
        if _looks_like_isin(query):
            score += 20
        if best_score is None or score > best_score:
            best_score = score
            best_item = item
    return best_item


def _is_fund_like_type(value: str) -> bool:
    return (value or "").strip().upper() in {"ETF", "FUND"}


def _page_exchange_candidates(exchange: str) -> list[str]:
    normalized = (exchange or "").strip().upper()
    candidates: list[str] = []
    if normalized:
        for alias in _PAGE_EXCHANGE_ALIASES.get(normalized, ()):
            candidates.append(alias)
        if normalized.startswith("X") and len(normalized) >= 4:
            candidates.append(normalized.lower())
        else:
            candidates.append(normalized.lower())

    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _security_page_path(item: dict) -> str:
    item_type = (item.get("type") or "").strip().upper()
    if item_type == "ETF":
        return "etfs"
    if item_type == "FUND":
        return "funds"
    return "stocks"


def _extract_html_attr(html: str, attr_name: str) -> str:
    if not html:
        return ""
    match = re.search(fr'{re.escape(attr_name)}="([^"]+)"', html, re.IGNORECASE)
    if not match:
        return ""
    return (match.group(1) or "").strip()


def _resolve_fund_security_id(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    sec_id = (item.get("secId") or "").strip()
    if sec_id.startswith("F"):
        return sec_id

    ticker = (item.get("ticker") or "").strip()
    if not ticker:
        return ""

    page_path = _security_page_path(item)
    for exchange_slug in _page_exchange_candidates(item.get("exchange", "")):
        url = (
            f"{_MS_WEBSITE_BASE}/{page_path}/{quote(exchange_slug)}/"
            f"{quote(ticker.lower())}/portfolio"
        )
        try:
            response = _http_get(
                url,
                headers={"User-Agent": _BROWSER_USER_AGENT},
                timeout=30,
            )
        except Exception as exc:
            logger.debug("ETF/Fund page fetch failed for %s: %s", url, exc)
            continue

        if response.status_code != 200:
            continue

        html = response.text or ""
        security_id = _extract_html_attr(html, "security-id")
        if security_id.startswith("F"):
            return security_id

        # Fallback for future markup changes.
        match = re.search(r'securityID:"([^"]+)"', html)
        if match:
            security_id = (match.group(1) or "").strip()
            if security_id.startswith("F"):
                return security_id

    return ""


class _DirectStockAPI:
    def __init__(
        self,
        sec_id: str,
        *,
        name: str = "",
        isin: str = "",
        exchange: str = "",
    ) -> None:
        self._sec_id = sec_id
        self._name = name or sec_id
        self._isin = isin
        self._exchange = exchange

    @property
    def name(self) -> str:
        return self._name

    @property
    def isin(self) -> str:
        return self._isin

    def _get_data(
        self,
        field: str,
        *,
        params: dict | None = None,
        url_suffix: str = "data",
        headers: dict | None = None,
    ) -> dict:
        url = f"{_MS_STOCK_API_BASE}/{field}/{self._sec_id}"
        if url_suffix:
            url += f"/{url_suffix}"
        resp = _http_get(
            url,
            params={**_MS_BASE_PARAMS, **(params or {})},
            headers={**_MS_HEADERS, **(headers or {})},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        return result if isinstance(result, dict) else {}

    def _get_realtime(self, path: str) -> dict:
        resp = _http_get(
            f"{_MS_REALTIME_BASE}/{path}",
            params={"securities": self._sec_id},
            headers={"User-Agent": _BROWSER_USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        return result if isinstance(result, dict) else {}

    def overview(self) -> dict:
        return self._get_data("equityOverview")

    def trading_info(self) -> dict:
        return self._get_realtime("quotes")

    def company_profile(self) -> dict:
        return self._get_data("companyProfile", url_suffix="")

    def key_metrics(self) -> dict:
        return self._get_data("keyMetrics/summary", params={"reportType": "A"}, url_suffix="")

    def valuation(self) -> dict:
        return self._get_data("valuation/v3", url_suffix="")

    def income_statement(self) -> dict:
        return self._get_data(
            "newfinancials",
            params={"reportType": "A", "dataType": "A"},
            url_suffix="incomeStatement/detail",
        )

    def balance_sheet(self) -> dict:
        return self._get_data(
            "newfinancials",
            params={"reportType": "A", "dataType": "A"},
            url_suffix="balanceSheet/detail",
        )

    def cash_flow(self) -> dict:
        return self._get_data(
            "newfinancials",
            params={"reportType": "A", "dataType": "A"},
            url_suffix="cashFlow/detail",
        )

    def dividends(self) -> dict:
        return self._get_data("dividends/v4")

    def profitability(self) -> dict:
        return self._get_data("keyMetrics/profitabilityAndEfficiency", url_suffix="")

    def operating_growth(self) -> dict:
        return self._get_data("keyStats/growthTable", url_suffix="")

    def financial_health(self) -> dict:
        return self._get_data("keyMetrics/financialHealth", url_suffix="")

    def free_cash_flow(self) -> dict:
        return self._get_data("keyMetrics/cashFlow", url_suffix="")

    def esg_risk(self) -> dict:
        return self._get_data("esgRisk")

    def sustainability(self) -> dict:
        return self._get_data("esgRisk/sustainability")

    def analysis_report(self) -> dict:
        return self._get_data("morningstarTake/v4", url_suffix="analysisReport")

    def board_of_directors(self) -> dict:
        return self._get_data("insiders/boardOfDirectors")

    def key_executives(self) -> dict:
        return self._get_data("insiders/keyExecutives")

    def institution_buyers(self) -> dict:
        return self._get_data("ownership/v1", url_suffix="Buyers/institution/20/data")

    def institution_sellers(self) -> dict:
        return self._get_data("ownership/v1", url_suffix="Sellers/institution/20/data")


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — StockAPI  (thin mstarpy wrapper)
# ══════════════════════════════════════════════════════════════════════════════


class StockAPI:
    """Thin wrapper around ``ms.Stock``.

    One instance per ISIN.  Every method calls the corresponding mstarpy
    endpoint, catches errors, and returns a raw ``dict`` (``{}`` on failure).
    """

    def __init__(
        self,
        term: str,
        *,
        exchange: str | None = None,
        language: str = "en-gb",
    ) -> None:
        filters = {"exchange": exchange} if exchange else None
        self._stock = None
        self._direct = None
        mstarpy_module = _load_mstarpy()
        if mstarpy_module is not None:
            try:
                self._stock = mstarpy_module.Stock(
                    term,
                    language=language,
                    filters=filters,
                )
            except Exception as exc:
                logger.warning(
                    "mstarpy init failed for %s: %s; using direct Morningstar HTTP fallback",
                    term,
                    exc,
                )
        if self._stock is None:
            candidates = _search_assets_http(term, limit=20)
            resolved = _pick_best_search_result(
                term,
                candidates,
                exchange=exchange,
                allowed_types=("STOCK",),
            )
            if not resolved:
                if not term:
                    raise ValueError("No term provided and no resolved security found.")
                resolved = {
                    "secId": term,
                    "name": term,
                    "ticker": "",
                    "exchange": exchange or "",
                    "type": "STOCK",
                }
            self._direct = _DirectStockAPI(
                resolved.get("secId", term),
                name=resolved.get("name", term),
                isin=term if _looks_like_isin(term) else "",
                exchange=resolved.get("exchange", exchange or ""),
            )

    @property
    def name(self) -> str:
        if self._direct is not None:
            return self._direct.name
        return self._stock.name

    @property
    def isin(self) -> str:
        if self._direct is not None:
            return self._direct.isin
        return getattr(self._stock, "isin", "")

    # -- endpoint delegates ----------------------------------------------------

    def _call(self, method: str) -> dict:
        try:
            if self._direct is not None:
                direct_method_map = {
                    "tradingInformation": "trading_info",
                    "companyProfile": "company_profile",
                    "keyMetricsSummary": "key_metrics",
                    "incomeStatement": "income_statement",
                    "balanceSheet": "balance_sheet",
                    "cashFlow": "cash_flow",
                    "operatingGrowth": "operating_growth",
                    "financialHealth": "financial_health",
                    "freeCashFlow": "free_cash_flow",
                    "esgRisk": "esg_risk",
                    "analysisReport": "analysis_report",
                    "boardOfDirectors": "board_of_directors",
                    "keyExecutives": "key_executives",
                    "institutionBuyers": "institution_buyers",
                    "institutionSellers": "institution_sellers",
                }
                target = self._direct
                call_name = direct_method_map.get(method, method)
            else:
                target = self._stock
                call_name = method
            result = getattr(target, call_name)()
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug("%s() failed: %s", method, exc)
            return {}

    def overview(self) -> dict:
        return self._call("overview")

    def trading_info(self) -> dict:
        return self._call("tradingInformation")

    def company_profile(self) -> dict:
        return self._call("companyProfile")

    def key_metrics(self) -> dict:
        return self._call("keyMetricsSummary")

    def valuation(self) -> dict:
        return self._call("valuation")

    def income_statement(self) -> dict:
        return self._call("incomeStatement")

    def balance_sheet(self) -> dict:
        return self._call("balanceSheet")

    def cash_flow(self) -> dict:
        return self._call("cashFlow")

    def dividends(self) -> dict:
        return self._call("dividends")

    def profitability(self) -> dict:
        return self._call("profitability")

    def operating_growth(self) -> dict:
        return self._call("operatingGrowth")

    def financial_health(self) -> dict:
        return self._call("financialHealth")

    def free_cash_flow(self) -> dict:
        return self._call("freeCashFlow")

    def esg_risk(self) -> dict:
        return self._call("esgRisk")

    def sustainability(self) -> dict:
        return self._call("sustainability")

    def analysis_report(self) -> dict:
        return self._call("analysisReport")

    def board_of_directors(self) -> dict:
        return self._call("boardOfDirectors")

    def key_executives(self) -> dict:
        return self._call("keyExecutives")

    def institution_buyers(self) -> dict:
        return self._call("institutionBuyers")

    def institution_sellers(self) -> dict:
        return self._call("institutionSellers")


class _DirectFundAPI:
    def __init__(
        self,
        sec_id: str,
        *,
        name: str = "",
        isin: str = "",
        exchange: str = "",
        performance_id: str = "",
        fund_type: str = "ETF",
    ) -> None:
        self._sec_id = sec_id
        self._name = name or sec_id
        self._isin = isin
        self._exchange = exchange
        self._performance_id = performance_id
        self._fund_type = (fund_type or "").strip().upper()
        self._metadata_cache: dict | None = None

    @property
    def name(self) -> str:
        metadata = self.security_metadata()
        return metadata.get("name") or self._name

    @property
    def isin(self) -> str:
        metadata = self.security_metadata()
        return metadata.get("isin") or self._isin

    def _base_paths(self) -> tuple[str, ...]:
        if self._fund_type == "ETF":
            return ("fund", "etf")
        return ("fund",)

    def _get_data(
        self,
        field: str,
        *,
        params: dict | None = None,
        url_suffix: str = "data",
        headers: dict | None = None,
    ) -> dict | list:
        if not self._sec_id:
            return {}

        last_result: dict | list = {}
        for base_path in self._base_paths():
            url = f"{_MS_FUND_API_BASE}/{base_path}/{field}/{self._sec_id}"
            if url_suffix:
                url += f"/{url_suffix}"
            try:
                resp = _http_get(
                    url,
                    params={**_MS_BASE_PARAMS, **(params or {})},
                    headers={**_MS_HEADERS, **(headers or {})},
                    timeout=30,
                )
            except Exception as exc:
                logger.debug("%s via %s failed: %s", field, base_path, exc)
                continue

            if resp.status_code != 200:
                continue
            try:
                result = resp.json()
            except Exception:
                continue

            if isinstance(result, dict):
                if field == "securityMetaData" and not any(
                    result.get(key) for key in ("secId", "performanceId", "name", "isin")
                ):
                    continue
                return result
            if isinstance(result, list):
                return result
            last_result = result

        return last_result

    def security_metadata(self) -> dict:
        if self._metadata_cache is None:
            result = self._get_data("securityMetaData", url_suffix="")
            self._metadata_cache = result if isinstance(result, dict) else {}
        return self._metadata_cache

    def quote(self) -> dict:
        result = self._get_data("quote/v7")
        return result if isinstance(result, dict) else {}

    def investment_strategy(self) -> dict:
        result = self._get_data("morningstarTake/investmentStrategy")
        return result if isinstance(result, dict) else {}

    def performance_table(self) -> dict:
        result = self._get_data("performance/table", url_suffix="")
        return result if isinstance(result, dict) else {}

    def risk_return_summary(self) -> dict:
        result = self._get_data("performance/riskReturnSummary")
        return result if isinstance(result, dict) else {}

    def risk_volatility(self) -> dict:
        result = self._get_data("performance/riskVolatility")
        return result if isinstance(result, dict) else {}

    def risk_score(self) -> dict:
        result = self._get_data("performance/riskScore")
        return result if isinstance(result, dict) else {}

    def sector(self) -> dict:
        result = self._get_data("portfolio/v2/sector")
        return result if isinstance(result, dict) else {}

    def holdings(self) -> dict:
        result = self._get_data("portfolio/holding/v2")
        return result if isinstance(result, dict) else {}

    def esg_risk(self) -> dict:
        result = self._get_data("esgRisk")
        return result if isinstance(result, dict) else {}

    def fee_level(self) -> dict:
        result = self._get_data("price/feeLevel/v1")
        return result if isinstance(result, dict) else {}

    def analyst_rating(self) -> list[dict]:
        result = self._get_data("parent/analystRating")
        return result if isinstance(result, list) else []


class FundAPI:
    """Thin wrapper around ``ms.Funds`` with a direct Morningstar HTTP fallback."""

    def __init__(
        self,
        term: str,
        *,
        exchange: str | None = None,
        language: str = "en-gb",
    ) -> None:
        filters = {"exchange": exchange} if exchange else None
        self._fund = None
        self._direct = None
        mstarpy_module = _load_mstarpy()
        if mstarpy_module is not None:
            try:
                self._fund = mstarpy_module.Funds(
                    term,
                    language=language,
                    filters=filters,
                )
            except Exception as exc:
                logger.warning(
                    "mstarpy fund init failed for %s: %s; using direct Morningstar HTTP fallback",
                    term,
                    exc,
                )

        if self._fund is None:
            candidates = _search_assets_http(term, limit=20)
            resolved = _pick_best_search_result(
                term,
                candidates,
                exchange=exchange,
                allowed_types=("ETF", "FUND"),
            )
            if not resolved:
                inferred_type = "ETF" if not _looks_like_isin(term) else "FUND"
                resolved = {
                    "secId": term,
                    "performanceId": term,
                    "name": term,
                    "ticker": "",
                    "exchange": exchange or "",
                    "type": inferred_type,
                }
            security_id = _resolve_fund_security_id(resolved)
            if not security_id and str(term).startswith("F"):
                security_id = term
            self._direct = _DirectFundAPI(
                security_id or term,
                name=resolved.get("name", term),
                isin=term if _looks_like_isin(term) else "",
                exchange=resolved.get("exchange", exchange or ""),
                performance_id=resolved.get("performanceId") or resolved.get("secId") or term,
                fund_type=resolved.get("type", "ETF"),
            )

    @property
    def name(self) -> str:
        if self._direct is not None:
            return self._direct.name
        return self._fund.name

    @property
    def isin(self) -> str:
        if self._direct is not None:
            return self._direct.isin
        return getattr(self._fund, "isin", "")

    def _call(self, method: str, *args, **kwargs) -> dict | list:
        try:
            if self._direct is not None:
                return getattr(self._direct, method)(*args, **kwargs)
            result = getattr(self._fund, method)(*args, **kwargs)
            if isinstance(result, dict):
                return result
            if hasattr(result, "to_dict"):
                return result.to_dict(orient="records")
            if isinstance(result, list):
                return result
            return {}
        except Exception as exc:
            logger.debug("%s() failed: %s", method, exc)
            return {}

    def security_metadata(self) -> dict:
        return self._call("security_metadata") if self._direct is not None else self._call("metaData")

    def quote(self) -> dict:
        return self._call("quote")

    def investment_strategy(self) -> dict:
        return self._call("investment_strategy") if self._direct is not None else self._call("investmentStrategy")

    def performance_table(self) -> dict:
        return self._call("performance_table") if self._direct is not None else self._call("performanceTable")

    def risk_return_summary(self) -> dict:
        return self._call("risk_return_summary") if self._direct is not None else self._call("riskReturnSummary")

    def risk_volatility(self) -> dict:
        return self._call("risk_volatility") if self._direct is not None else self._call("riskVolatility")

    def risk_score(self) -> dict:
        return self._call("risk_score") if self._direct is not None else self._call("riskScore")

    def sector(self) -> dict:
        return self._call("sector", version=2) if self._direct is None else self._call("sector")

    def holdings(self) -> dict:
        return self._call("holdings") if self._direct is not None else self._call("position", version=2)

    def esg_risk(self) -> dict:
        return self._call("esg_risk") if self._direct is not None else self._call("esgRisk")

    def fee_level(self) -> dict:
        return self._call("fee_level") if self._direct is not None else self._call("feeLevel")

    def analyst_rating(self) -> list[dict]:
        result = self._call("analyst_rating") if self._direct is not None else self._call("analystRating")
        return result if isinstance(result, list) else []


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — Stateless extractors  (raw dict → clean dict)
# ══════════════════════════════════════════════════════════════════════════════


def extract_exchange(raw_trading: dict) -> str:
    """First 'exchange' value from ``tradingInformation()`` data."""
    for sec_data in raw_trading.values():
        if not isinstance(sec_data, dict):
            continue
        for field_data in sec_data.values():
            if not isinstance(field_data, dict):
                continue
            exch = field_data.get("exchange")
            if isinstance(exch, dict) and exch.get("value"):
                return exch["value"]
            props = field_data.get("properties", {})
            if isinstance(props, dict):
                exch = props.get("exchange")
                if isinstance(exch, dict) and exch.get("value"):
                    return exch["value"]
    return ""


def extract_overview(
    raw_overview: dict,
    raw_trading: dict,
    stock_name: str = "",
) -> dict | None:
    if not raw_overview:
        return None
    fundamentals = raw_overview.get("morningstarFundamentals", {})
    return {
        "securityName": raw_overview.get("securityName", stock_name),
        "ticker": raw_overview.get("ticker", ""),
        "exchange": extract_exchange(raw_trading),
        "starRating": raw_overview.get("starRating"),
        "lastClose": fundamentals.get("lastClose"),
        "lastCloseDate": (fundamentals.get("lastCloseDate", "") or "")[:10],
        "fairValue": fundamentals.get("fairValue"),
        "uncertainty": fundamentals.get("uncertainty"),
        "sector": raw_overview.get("sector"),
        "industry": raw_overview.get("industry"),
    }


def extract_company_profile(raw_profile: dict) -> dict | None:
    if not raw_profile:
        return None
    sections = raw_profile.get("sections") or {}
    contact = sections.get("contact") or {}
    return {
        "description": (sections.get("businessDescription") or {}).get("value", ""),
        "address": contact.get("address1", ""),
        "country": contact.get("country", ""),
        "phone": contact.get("phone", ""),
        "url": contact.get("url", ""),
        "sector": (sections.get("sector") or {}).get("value", ""),
        "industry": (sections.get("industry") or {}).get("value", ""),
        "employees": (sections.get("totalEmployees") or {}).get("value", ""),
        "employeesDate": ((sections.get("totalEmployees") or {}).get("date") or "")[:10],
        "fiscalYearEnd": ((sections.get("fiscalYearEnds") or {}).get("value") or "")[:10],
    }


def extract_valuation_multiples(
    raw_valuation: dict,
    raw_overview: dict,
) -> dict:
    """PE, EV/EBIT, EV/EBITDA with PE fallback chain.

    Returns ``{"pe": ..., "ev_to_ebit": ..., "ev_to_ebitda": ...}``
    (only keys that have values).
    """
    result: dict = {}
    # Primary: valuation() rows
    if raw_valuation:
        all_rows: list[dict] = []
        for section in ("Collapsed", "Expanded"):
            sd = raw_valuation.get(section)
            if isinstance(sd, dict):
                all_rows.extend(sd.get("rows") or [])
        # For each multiple, find the first non-None value from the end (most recent)
        for sal_id, key in _VALUATION_IDS.items():
            found = False
            for row in all_rows:
                if row.get("salDataId", "") != sal_id:
                    continue
                datum = row.get("datum", [])
                stock_datum = datum[:-2] if len(datum) > 2 else datum
                # Go from most recent to oldest, pick first non-None
                for v in reversed(stock_datum):
                    fv = safe_float(v)
                    if fv is not None:
                        result[key] = fv
                        found = True
                        break
                if found:
                    break

    # Fallback PE: overview
    if not result.get("pe"):
        pe = safe_float(raw_overview.get("priceEarnings"))
        if pe is not None:
            result["pe"] = pe

    return result


def extract_base_financials(raw_kms: dict) -> dict | None:
    """EBITDA, EBIT, EPS, shares, net debt, currency from keyMetricsSummary."""
    if not raw_kms:
        return None

    inc_data = (raw_kms.get("incomeStatementList") or {}).get("dataList") or []
    bs_data = (raw_kms.get("balanceSheetList") or {}).get("dataList") or []

    result: dict = {}
    if inc_data:
        result["ebitda"] = last_value(inc_data, "ebitda")
        result["ebit"] = last_value(inc_data, "ebit")
        result["eps"] = last_value(inc_data, "dilutedEPS") or last_value(
            inc_data, "basicEPS"
        )

    if bs_data:
        total_debt = last_value(bs_data, "totalDebt") or 0.0
        cash = (
            last_value(bs_data, "cashAndCashEquivalent", "cashAndCashEquivalents")
            or 0.0
        )
        result["net_debt"] = total_debt - cash

    # Shares from netIncome / EPS for unit consistency
    shares = None
    for entry in reversed(inc_data):
        ni = entry.get("netIncome")
        ep = entry.get("dilutedEPS") or entry.get("basicEPS")
        if ni and ep and ep != 0:
            shares = ni / ep
            break
    result["shares"] = shares

    if inc_data:
        result["currency"] = inc_data[-1].get("currencyId", "")

    if not any(result.get(k) for k in ("ebitda", "ebit", "eps")):
        return None
    return result


_FINANCIAL_RATIO_DEFINITIONS: list[dict[str, Any]] = [
    {
        "title": "Liquidity ratios",
        "items": [
            {
                "key": "current_ratio",
                "name": "Current ratio",
                "formula": "Current Assets / Current Liabilities",
                "interpretation": "Mesure la capacite a couvrir les passifs court terme avec les actifs court terme.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "quick_ratio",
                "name": "Quick ratio",
                "formula": "(Cash + Marketable Securities + Receivables) / Current Liabilities",
                "interpretation": "Version plus stricte du current ratio, en excluant les stocks.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "cash_ratio",
                "name": "Cash ratio",
                "formula": "(Cash + Marketable Securities) / Current Liabilities",
                "interpretation": "Mesure la capacite immediate a payer les dettes court terme uniquement avec le cash.",
                "format": "multiple",
            },
            {
                "key": "working_capital",
                "name": "Working capital",
                "formula": "Current Assets - Current Liabilities",
                "interpretation": "Montant net disponible pour financer le cycle d'exploitation.",
                "format": "amount",
                "featured": True,
            },
        ],
    },
    {
        "title": "Solvency / leverage ratios",
        "items": [
            {
                "key": "assets_to_liabilities",
                "name": "Total assets / Total liabilities",
                "formula": "Total Assets / Total Liabilities",
                "interpretation": "Mesure combien d'actifs soutiennent chaque euro de dette/passif.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "debt_to_equity",
                "name": "Debt-to-equity ratio",
                "formula": "Total Debt / Shareholders' Equity",
                "interpretation": "Mesure le levier financier par rapport aux fonds propres.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "net_debt_to_ebitda",
                "name": "Net debt / EBITDA",
                "formula": "(Total Debt - Cash) / EBITDA",
                "interpretation": "Mesure le nombre d'annees d'EBITDA necessaires pour rembourser la dette nette.",
                "format": "multiple",
            },
            {
                "key": "interest_coverage",
                "name": "Interest coverage",
                "formula": "EBIT / Interest Expense",
                "interpretation": "Mesure la capacite a payer les interets de la dette.",
                "format": "multiple",
            },
            {
                "key": "debt_ratio",
                "name": "Debt ratio",
                "formula": "Total Liabilities / Total Assets",
                "interpretation": "Mesure la part des actifs financee par les passifs.",
                "format": "percent",
            },
        ],
    },
    {
        "title": "Profitability ratios",
        "items": [
            {
                "key": "gross_margin",
                "name": "Gross margin",
                "formula": "Gross Profit / Revenue",
                "interpretation": "Mesure la marge apres les couts directs de production ou de service.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "operating_margin",
                "name": "Operating margin",
                "formula": "Operating Income / Revenue or EBIT / Revenue",
                "interpretation": "Mesure la rentabilite du coeur operationnel de l'entreprise.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "pretax_margin",
                "name": "Pretax margin",
                "formula": "Earnings Before Tax / Revenue",
                "interpretation": "Mesure la rentabilite avant impots.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "net_margin",
                "name": "Net margin",
                "formula": "Net Income / Revenue",
                "interpretation": "Mesure la part du chiffre d'affaires transformee en profit final.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "ebitda_margin",
                "name": "EBITDA margin",
                "formula": "EBITDA / Revenue",
                "interpretation": "Mesure la rentabilite operationnelle avant amortissements.",
                "format": "percent",
            },
        ],
    },
    {
        "title": "Cash flow ratios",
        "items": [
            {
                "key": "ocf_margin",
                "name": "Operating cash-flow margin",
                "formula": "Operating Cash Flow / Revenue",
                "interpretation": "Mesure la part du chiffre d'affaires convertie en cash operationnel.",
                "format": "percent",
            },
            {
                "key": "fcf_margin",
                "name": "Free cash-flow margin",
                "formula": "Free Cash Flow / Revenue",
                "interpretation": "Mesure la part du chiffre d'affaires transformee en cash disponible.",
                "format": "percent",
            },
            {
                "key": "cash_flow_to_capex",
                "name": "Cash flow to CAPEX",
                "formula": "Operating Cash Flow / CAPEX",
                "interpretation": "Mesure combien de fois le cash operationnel couvre les investissements.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "fcf_conversion",
                "name": "FCF conversion",
                "formula": "Free Cash Flow / Net Income",
                "interpretation": "Mesure la conversion du resultat net en cash libre.",
                "format": "percent",
            },
            {
                "key": "capex_to_revenue",
                "name": "CAPEX / Revenue",
                "formula": "CAPEX / Revenue",
                "interpretation": "Mesure l'intensite capitalistique du business.",
                "format": "percent",
            },
        ],
    },
    {
        "title": "Return ratios",
        "items": [
            {
                "key": "roe",
                "name": "ROE",
                "formula": "Net Income / Shareholders' Equity",
                "interpretation": "Mesure le rendement des capitaux propres.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "roae",
                "name": "ROAE",
                "formula": "Net Income / Average Shareholders' Equity",
                "interpretation": "Version plus propre du ROE, utilisant les capitaux propres moyens.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "roa",
                "name": "ROA",
                "formula": "Net Income / Total Assets",
                "interpretation": "Mesure le rendement des actifs totaux.",
                "format": "percent",
            },
            {
                "key": "roc",
                "name": "ROC",
                "formula": "EBIT / Capital Employed",
                "interpretation": "Mesure le rendement du capital engage dans l'activite.",
                "format": "percent",
                "featured": True,
            },
            {
                "key": "roic",
                "name": "ROIC",
                "formula": "NOPAT / Invested Capital",
                "interpretation": "Mesure le rendement du capital reellement investi dans le business.",
                "format": "percent",
            },
        ],
    },
    {
        "title": "Efficiency / activity ratios",
        "items": [
            {
                "key": "inventory_turnover",
                "name": "Inventory turnover",
                "formula": "Cost of Goods Sold / Average Inventory",
                "interpretation": "Mesure combien de fois les stocks sont vendus et renouveles sur une periode.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "asset_turnover",
                "name": "Asset turnover",
                "formula": "Revenue / Total Assets",
                "interpretation": "Mesure combien de chiffre d'affaires est genere par euro d'actif.",
                "format": "multiple",
            },
            {
                "key": "receivables_turnover",
                "name": "Receivables turnover",
                "formula": "Revenue / Average Accounts Receivable",
                "interpretation": "Mesure la vitesse d'encaissement des creances clients.",
                "format": "multiple",
            },
            {
                "key": "payables_turnover",
                "name": "Payables turnover",
                "formula": "Cost of Goods Sold / Average Accounts Payable",
                "interpretation": "Mesure la vitesse a laquelle l'entreprise paie ses fournisseurs.",
                "format": "multiple",
            },
            {
                "key": "cash_conversion_cycle",
                "name": "Cash conversion cycle",
                "formula": "DIO + DSO - DPO",
                "interpretation": "Mesure le nombre de jours pendant lesquels le cash est immobilise dans le cycle d'exploitation.",
                "format": "days",
            },
        ],
    },
    {
        "title": "Growth ratios",
        "items": [
            {
                "key": "revenue_growth",
                "name": "Revenue growth",
                "formula": "(Revenue_t - Revenue_t-1) / Revenue_t-1",
                "interpretation": "Mesure la croissance du chiffre d'affaires.",
                "format": "percent",
            },
            {
                "key": "ebitda_growth",
                "name": "EBITDA growth",
                "formula": "(EBITDA_t - EBITDA_t-1) / EBITDA_t-1",
                "interpretation": "Mesure la croissance du profit operationnel avant amortissements.",
                "format": "percent",
            },
            {
                "key": "ebit_growth",
                "name": "EBIT growth",
                "formula": "(EBIT_t - EBIT_t-1) / EBIT_t-1",
                "interpretation": "Mesure la croissance du resultat operationnel.",
                "format": "percent",
            },
            {
                "key": "eps_growth",
                "name": "EPS growth",
                "formula": "(EPS_t - EPS_t-1) / EPS_t-1",
                "interpretation": "Mesure la croissance du benefice par action.",
                "format": "percent",
            },
            {
                "key": "fcf_growth",
                "name": "FCF growth",
                "formula": "(FCF_t - FCF_t-1) / FCF_t-1",
                "interpretation": "Mesure la croissance du cash-flow libre.",
                "format": "percent",
            },
        ],
    },
    {
        "title": "Valuation ratios",
        "items": [
            {
                "key": "pe",
                "name": "P/E",
                "formula": "Market Capitalization / Net Income or Share Price / EPS",
                "interpretation": "Mesure combien le marche paie pour 1 euro de benefice.",
                "format": "multiple",
                "featured": True,
            },
            {
                "key": "ev_to_ebitda",
                "name": "EV / EBITDA",
                "formula": "Enterprise Value / EBITDA",
                "interpretation": "Mesure la valeur de l'entreprise par rapport a son EBITDA.",
                "format": "multiple",
            },
            {
                "key": "ev_to_ebit",
                "name": "EV / EBIT",
                "formula": "Enterprise Value / EBIT",
                "interpretation": "Plus strict qu'EV/EBITDA car il tient compte des amortissements.",
                "format": "multiple",
            },
            {
                "key": "price_to_sales",
                "name": "Price-to-sales",
                "formula": "Market Capitalization / Revenue",
                "interpretation": "Utile pour les entreprises peu ou pas rentables.",
                "format": "multiple",
            },
            {
                "key": "price_to_book",
                "name": "Price-to-book",
                "formula": "Market Capitalization / Book Value of Equity",
                "interpretation": "Compare la valeur de marche aux fonds propres comptables.",
                "format": "multiple",
            },
            {
                "key": "fcf_yield",
                "name": "FCF yield",
                "formula": "Free Cash Flow / Market Capitalization",
                "interpretation": "Mesure le rendement en cash-flow libre pour l'actionnaire.",
                "format": "percent",
            },
            {
                "key": "earnings_yield",
                "name": "Earnings yield",
                "formula": "Net Income / Market Capitalization",
                "interpretation": "Inverse du P/E, utile pour comparer a des rendements obligataires.",
                "format": "percent",
            },
        ],
    },
]


def extract_financial_ratios(
    *,
    raw_overview: dict | None = None,
    raw_key_metrics: dict | None = None,
    raw_valuation: dict | None = None,
    raw_income_statement: dict | None = None,
    raw_balance_sheet: dict | None = None,
    raw_cash_flow: dict | None = None,
    raw_profitability: dict | None = None,
    raw_operating_growth: dict | None = None,
    raw_financial_health: dict | None = None,
    raw_free_cash_flow: dict | None = None,
) -> dict:
    """Build the Financial Ratios block from Morningstar raw payloads.

    Values are calculated from raw statement lines where possible, with
    Morningstar key-metric ratios as fallbacks when the underlying components
    are not exposed for a security.
    """

    raw_overview = raw_overview or {}
    raw_key_metrics = raw_key_metrics or {}
    raw_valuation = raw_valuation or {}
    raw_income_statement = raw_income_statement or {}
    raw_balance_sheet = raw_balance_sheet or {}
    raw_cash_flow = raw_cash_flow or {}
    raw_profitability = raw_profitability or {}
    raw_operating_growth = raw_operating_growth or {}
    raw_financial_health = raw_financial_health or {}
    raw_free_cash_flow = raw_free_cash_flow or {}

    inc_data = (raw_key_metrics.get("incomeStatementList") or {}).get("dataList") or []
    bs_data = (raw_key_metrics.get("balanceSheetList") or {}).get("dataList") or []
    cf_data = (raw_key_metrics.get("cashFlowList") or {}).get("dataList") or []
    profitability_data = raw_profitability.get("dataList") or []
    growth_data = raw_operating_growth.get("dataList") or []
    health_data = raw_financial_health.get("dataList") or []
    fcf_data = raw_free_cash_flow.get("dataList") or []

    revenue_series = _series_from_data_or_table(
        inc_data,
        raw_income_statement,
        ("revenue", "totalRevenue", "totalRevenueAsReported"),
        ("Total Revenue", "Total Revenue as Reported, Supplemental", "Revenue"),
    )
    ebit_series = _series_from_data_or_table(
        inc_data,
        raw_income_statement,
        ("ebit", "operatingIncome", "operatingIncomeLoss"),
        (
            "Operating Income",
            "Reported Total Operating Profit/Loss",
            "Reported Normalized Operating Profit",
            "Operating Profit",
        ),
    )
    ebitda_series = _series_from_data_or_table(
        inc_data,
        raw_income_statement,
        ("ebitda",),
        ("EBITDA",),
    )
    eps_series = _series_from_data_or_table(
        inc_data,
        raw_income_statement,
        ("dilutedEPS", "basicEPS", "eps"),
        ("Diluted EPS", "Basic EPS"),
    )
    equity_series = _series_from_data_or_table(
        bs_data,
        raw_balance_sheet,
        (
            "shareholdersEquity",
            "stockholdersEquity",
            "totalEquity",
            "equityAttributableToParentStockholders",
        ),
        (
            "Equity Attributable to Parent Stockholders",
            "Total Equity",
            "Total Stockholders' Equity",
            "Shareholders' Equity",
        ),
    )
    assets_series = _series_from_data_or_table(
        bs_data,
        raw_balance_sheet,
        ("totalAssets", "totalAsset"),
        ("Total Assets",),
    )
    receivables_series = _series_from_data_or_table(
        bs_data,
        raw_balance_sheet,
        ("accountsReceivable", "accountReceivable", "netReceivables", "receivables"),
        (
            "Trade and Other Receivables, Current",
            "Trade/Accounts Receivable, Current",
            "Accounts Receivable",
        ),
    )
    inventory_series = _series_from_data_or_table(
        bs_data,
        raw_balance_sheet,
        ("inventory", "inventories"),
        ("Inventories", "Inventory"),
    )
    payables_series = _series_from_data_or_table(
        bs_data,
        raw_balance_sheet,
        ("accountsPayable", "accountPayable", "payables"),
        (
            "Trade and Other Payables, Current",
            "Trade/Accounts Payable, Current",
            "Accounts Payable",
        ),
    )
    fcf_series = _series_from_data_or_table(
        cf_data,
        raw_cash_flow,
        ("freeCashFlow", "freeCf"),
        ("Free Cash Flow",),
    )

    revenue = _latest_numeric(revenue_series)
    gross_profit = _first_not_none(
        _latest_data_value(inc_data, "grossProfit"),
        _latest_table_value(raw_income_statement, "Gross Profit"),
    )
    operating_income = _latest_numeric(ebit_series)
    ebit = operating_income
    depreciation_amortization = _first_not_none(
        _latest_data_value(inc_data, "depreciationAndAmortization", "depreciationAmortization"),
        _latest_table_value(
            raw_income_statement,
            "Depreciation and Amortization, Supplemental",
            "Depreciation, Amortization and Depletion, Supplemental",
        ),
    )
    ebitda = _first_not_none(
        _latest_numeric(ebitda_series),
        _sum_if_all(ebit, abs(depreciation_amortization) if depreciation_amortization is not None else None),
    )
    pretax_income = _first_not_none(
        _latest_data_value(inc_data, "pretaxIncome", "incomeBeforeTax"),
        _latest_table_value(raw_income_statement, "Pretax Income", "Income Before Tax"),
    )
    net_income = _first_not_none(
        _latest_data_value(inc_data, "netIncome", "netIncomeCommonStockholders"),
        _latest_table_value(
            raw_income_statement,
            "Net Income Available to Common Stockholders",
            "Diluted Net Income Available to Common Stockholders",
            "Net Income after Non-Controlling/Minority Interests",
            "Net Income",
        ),
    )
    cogs = abs(
        _first_not_none(
            _latest_data_value(inc_data, "costOfRevenue", "costOfGoodsSold"),
            _latest_table_value(raw_income_statement, "Cost of Revenue", "Cost of Goods Sold"),
        )
        or 0
    ) or None
    interest_expense = _first_not_none(
        _latest_data_value(inc_data, "interestExpense"),
        _latest_table_value(
            raw_income_statement,
            "Interest Expense Net of Capitalized Interest",
            "Interest Expense",
        ),
    )

    current_assets = _first_not_none(
        _latest_data_value(bs_data, "totalCurrentAssets", "currentAssets"),
        _latest_table_value(raw_balance_sheet, "Total Current Assets", "Current Assets"),
    )
    current_liabilities = _first_not_none(
        _latest_data_value(bs_data, "totalCurrentLiabilities", "currentLiabilities"),
        _latest_table_value(raw_balance_sheet, "Total Current Liabilities", "Current Liabilities"),
    )
    cash = _first_not_none(
        _latest_data_value(bs_data, "cashAndCashEquivalent", "cashAndCashEquivalents", "cash"),
        _latest_table_value(raw_balance_sheet, "Cash and Cash Equivalents", "Cash"),
    )
    cash_and_marketable = _first_not_none(
        _latest_data_value(
            bs_data,
            "cashAndShortTermInvestments",
            "cashCashEquivalentsAndShortTermInvestments",
        ),
        _latest_table_value(
            raw_balance_sheet,
            "Cash, Cash Equivalents and Short Term Investments",
        ),
        _sum_if_any(
            cash,
            _first_not_none(
                _latest_data_value(bs_data, "shortTermInvestments", "marketableSecurities"),
                _latest_table_value(raw_balance_sheet, "Short Term Investments", "Marketable Securities"),
            ),
        ),
    )
    receivables = _latest_numeric(receivables_series)
    inventory = _latest_numeric(inventory_series)
    payables = _latest_numeric(payables_series)
    total_assets = _latest_numeric(assets_series)
    total_liabilities = _first_not_none(
        _latest_data_value(bs_data, "totalLiabilities", "totalLiability"),
        _latest_table_value(raw_balance_sheet, "Total Liabilities"),
    )
    shareholders_equity = _latest_numeric(equity_series)
    total_debt = _first_not_none(
        _latest_data_value(bs_data, "totalDebt", "debtTotal"),
        _latest_table_value(raw_balance_sheet, "Total Debt"),
        _sum_if_any(
            _first_not_none(
                _latest_data_value(bs_data, "shortTermDebt", "currentDebt"),
                _latest_table_value(
                    raw_balance_sheet,
                    "Current Portion of Long Term Debt and Capital Lease",
                    "Current Portion of Long Term Debt",
                    "Short Term Debt",
                ),
            ),
            _first_not_none(
                _latest_data_value(bs_data, "longTermDebt"),
                _latest_table_value(
                    raw_balance_sheet,
                    "Long Term Debt and Capital Lease Obligation",
                    "Long Term Debt",
                ),
            ),
        ),
    )

    operating_cash_flow = _first_not_none(
        _latest_data_value(
            cf_data,
            "operatingCashFlow",
            "cashFlowFromOperations",
            "netCashProvidedByOperatingActivities",
        ),
        _latest_table_value(
            raw_cash_flow,
            "Cash Flow from Operating Activities",
            "Net Cash Provided by Operating Activities",
            "Net Cash from Operating Activities",
            "Operating Cash Flow",
        ),
    )
    capex = _first_not_none(
        _latest_data_value(cf_data, "capitalExpenditure", "capitalExpenditures", "capex"),
        _latest_table_value(raw_cash_flow, "Capital Expenditure", "Capital Expenditures", "CAPEX"),
    )
    capex_abs = abs(capex) if capex is not None else None
    free_cash_flow = _first_not_none(
        _latest_numeric(fcf_series),
        _sum_if_all(operating_cash_flow, -capex_abs if capex_abs is not None else None),
    )

    avg_equity = _average_latest_two(equity_series)
    avg_inventory = _average_latest_two(inventory_series)
    avg_receivables = _average_latest_two(receivables_series)
    avg_payables = _average_latest_two(payables_series)
    capital_employed = _first_not_none(
        _difference(total_assets, current_liabilities),
        _sum_if_any(shareholders_equity, total_debt),
    )
    invested_capital = _sum_if_any(shareholders_equity, total_debt, -cash if cash is not None else None)
    tax_expense = abs(
        _first_not_none(
            _latest_data_value(inc_data, "taxExpense", "incomeTaxExpense"),
            _latest_table_value(raw_income_statement, "Tax Provision", "Income Tax Expense"),
        )
        or 0
    ) or None
    tax_rate = _ratio(tax_expense, pretax_income)
    nopat = ebit * (1 - tax_rate) if ebit is not None and tax_rate is not None else None

    valuation_multiples = extract_valuation_multiples(raw_valuation, raw_overview)
    pe = _first_not_none(
        valuation_multiples.get("pe"),
        _valuation_row_value(raw_valuation, "Price/Earnings", "P/E"),
        safe_float(raw_overview.get("priceEarnings")),
    )
    ev_to_ebitda = _first_not_none(
        valuation_multiples.get("ev_to_ebitda"),
        _valuation_row_value(
            raw_valuation,
            "Enterprise Value/EBITDA",
            "EV/EBITDA",
            "Enterprise Value To EBITDA",
        ),
    )
    ev_to_ebit = _first_not_none(
        valuation_multiples.get("ev_to_ebit"),
        _valuation_row_value(
            raw_valuation,
            "Enterprise Value/EBIT",
            "EV/EBIT",
            "Enterprise Value To EBIT",
        ),
    )
    price_to_sales = _first_not_none(
        _valuation_row_value(raw_valuation, "Price/Sales", "Price To Sales"),
        safe_float(raw_overview.get("priceSales")),
        _market_statement_ratio(safe_float(raw_overview.get("marketCap")), revenue),
    )
    price_to_book = _first_not_none(
        _valuation_row_value(raw_valuation, "Price/Book", "Price To Book"),
        safe_float(raw_overview.get("priceBook")),
        _market_statement_ratio(safe_float(raw_overview.get("marketCap")), shareholders_equity),
    )
    price_to_fcf = _valuation_row_value(raw_valuation, "Price/Free Cash Flow", "Price/FCF")
    market_cap = safe_float(raw_overview.get("marketCap"))

    inventory_turnover = _first_not_none(
        _ratio(cogs, avg_inventory),
        _latest_data_value(profitability_data, "inventoryTurnover"),
    )
    receivables_turnover = _first_not_none(
        _ratio(revenue, avg_receivables),
        _latest_data_value(profitability_data, "receivableTurnover", "receivablesTurnover"),
    )
    payables_turnover = _ratio(cogs, avg_payables)
    cash_conversion_cycle = _first_not_none(
        _latest_data_value(profitability_data, "cashConversionCycle"),
        _cash_conversion_cycle(
            inventory_turnover,
            receivables_turnover,
            payables_turnover,
        ),
    )

    values = {
        "current_ratio": _first_not_none(
            _ratio(current_assets, current_liabilities),
            _latest_data_value(health_data, "currentRatio"),
        ),
        "quick_ratio": _first_not_none(
            _ratio(_sum_if_all(cash_and_marketable, receivables), current_liabilities),
            _latest_data_value(health_data, "quickRatio"),
        ),
        "cash_ratio": _ratio(cash_and_marketable, current_liabilities),
        "working_capital": _difference(current_assets, current_liabilities),
        "assets_to_liabilities": _ratio(total_assets, total_liabilities),
        "debt_to_equity": _first_not_none(
            _ratio(total_debt, shareholders_equity),
            _latest_data_value(health_data, "debtEquityRatio", "debtToEquityRatio"),
        ),
        "net_debt_to_ebitda": _ratio(_difference(total_debt, cash), ebitda),
        "interest_coverage": _first_not_none(
            _ratio(ebit, abs(interest_expense) if interest_expense is not None else None),
            _latest_data_value(health_data, "interestCoverage"),
        ),
        "debt_ratio": _ratio(total_liabilities, total_assets),
        "gross_margin": _ratio(gross_profit, revenue),
        "operating_margin": _ratio(operating_income, revenue),
        "pretax_margin": _ratio(pretax_income, revenue),
        "net_margin": _ratio(net_income, revenue),
        "ebitda_margin": _ratio(ebitda, revenue),
        "ocf_margin": _ratio(operating_cash_flow, revenue),
        "fcf_margin": _first_not_none(
            _ratio(free_cash_flow, revenue),
            _percent_metric(_latest_data_value(fcf_data, "freeCfPerSales", "freeCashFlowPerSales")),
        ),
        "cash_flow_to_capex": _ratio(operating_cash_flow, capex_abs),
        "fcf_conversion": _first_not_none(
            _ratio(free_cash_flow, net_income),
            _latest_data_value(fcf_data, "freeCashFlowPerNetIncome"),
        ),
        "capex_to_revenue": _first_not_none(
            _ratio(capex_abs, revenue),
            _percent_metric(_latest_data_value(health_data, "capexAsPerOfSales", "capexAsPercentOfSales")),
        ),
        "roe": _first_not_none(
            _ratio(net_income, shareholders_equity),
            _percent_metric(_latest_data_value(profitability_data, "roe")),
        ),
        "roae": _ratio(net_income, avg_equity),
        "roa": _first_not_none(
            _ratio(net_income, total_assets),
            _percent_metric(_latest_data_value(profitability_data, "roa")),
        ),
        "roc": _ratio(ebit, capital_employed),
        "roic": _first_not_none(
            _percent_metric(_latest_data_value(profitability_data, "roic")),
            _ratio(nopat, invested_capital),
        ),
        "inventory_turnover": inventory_turnover,
        "asset_turnover": _ratio(revenue, total_assets),
        "receivables_turnover": receivables_turnover,
        "payables_turnover": payables_turnover,
        "cash_conversion_cycle": cash_conversion_cycle,
        "revenue_growth": _first_not_none(
            _growth_metric(growth_data, ("revenuePer", "revenue"), ("yearOverYear", "yearoverYear")),
            _growth_from_series(revenue_series),
        ),
        "ebitda_growth": _growth_from_series(ebitda_series),
        "ebit_growth": _first_not_none(
            _growth_metric(growth_data, ("operatingIncome", "ebit"), ("yearOverYear", "yearoverYear")),
            _growth_from_series(ebit_series),
        ),
        "eps_growth": _first_not_none(
            _growth_metric(growth_data, ("epsPer", "eps"), ("yearOverYear", "yearoverYear")),
            _growth_from_series(eps_series),
        ),
        "fcf_growth": _first_not_none(
            _percent_metric(_latest_data_value(fcf_data, "freeCashFlowGrowthPer", "freeCfGrowthPer")),
            _growth_from_series(fcf_series),
        ),
        "pe": pe,
        "ev_to_ebitda": ev_to_ebitda,
        "ev_to_ebit": ev_to_ebit,
        "price_to_sales": price_to_sales,
        "price_to_book": price_to_book,
        "fcf_yield": _first_not_none(
            _ratio(1, price_to_fcf),
            _market_statement_yield(free_cash_flow, market_cap),
        ),
        "earnings_yield": _first_not_none(
            _ratio(1, pe),
            _market_statement_yield(net_income, market_cap),
        ),
    }

    categories: list[dict[str, Any]] = []
    has_values = False
    for category in _FINANCIAL_RATIO_DEFINITIONS:
        items = []
        for item in category["items"]:
            value = values.get(item["key"])
            if value is not None:
                has_values = True
            items.append(
                {
                    "name": item["name"],
                    "formula": item["formula"],
                    "interpretation": item["interpretation"],
                    "displayValue": _format_financial_ratio_value(
                        value,
                        item["format"],
                    ),
                    "isAvailable": value is not None,
                    "featured": bool(item.get("featured")),
                }
            )
        categories.append({"title": category["title"], "items": items})

    return {"categories": categories, "hasValues": has_values}


def _normalize_lookup_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    n = safe_float(numerator)
    d = safe_float(denominator)
    if n is None or d in (None, 0):
        return None
    try:
        return n / d
    except (TypeError, ZeroDivisionError):
        return None


def _difference(left: Any, right: Any) -> float | None:
    l = safe_float(left)
    r = safe_float(right)
    if l is None or r is None:
        return None
    return l - r


def _sum_if_any(*values: Any) -> float | None:
    total = 0.0
    found = False
    for value in values:
        number = safe_float(value)
        if number is None:
            continue
        total += number
        found = True
    return total if found else None


def _sum_if_all(*values: Any) -> float | None:
    total = 0.0
    for value in values:
        number = safe_float(value)
        if number is None:
            return None
        total += number
    return total


def _percent_metric(value: Any) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    return number / 100


def _latest_data_value(data_list: list[dict], *keys: str) -> float | None:
    key_norms = [_normalize_lookup_key(key) for key in keys]
    for entry in reversed(data_list or []):
        norm_entry = {
            _normalize_lookup_key(key): value
            for key, value in entry.items()
            if not isinstance(value, dict)
        }
        for key, norm_key in zip(keys, key_norms):
            value = safe_float(entry.get(key))
            if value is None:
                value = safe_float(norm_entry.get(norm_key))
            if value is not None:
                return value
    return None


def _growth_metric(
    data_list: list[dict],
    parent_keys: tuple[str, ...],
    child_keys: tuple[str, ...],
) -> float | None:
    parent_norms = [_normalize_lookup_key(key) for key in parent_keys]
    child_norms = [_normalize_lookup_key(key) for key in child_keys]
    for entry in reversed(data_list or []):
        norm_entry = {_normalize_lookup_key(key): value for key, value in entry.items()}
        parent = None
        for key, norm_key in zip(parent_keys, parent_norms):
            candidate = entry.get(key)
            if candidate is None:
                candidate = norm_entry.get(norm_key)
            if isinstance(candidate, dict):
                parent = candidate
                break
        if not isinstance(parent, dict):
            continue
        norm_parent = {_normalize_lookup_key(key): value for key, value in parent.items()}
        for key, norm_key in zip(child_keys, child_norms):
            value = safe_float(parent.get(key))
            if value is None:
                value = safe_float(norm_parent.get(norm_key))
            if value is not None:
                return value / 100
    return None


def _table_sections(raw_table: dict) -> list[dict]:
    sections: list[dict] = []
    if not isinstance(raw_table, dict):
        return sections
    for section_name in ("Collapsed", "Expanded"):
        section = raw_table.get(section_name)
        if isinstance(section, dict):
            sections.append(section)
    if isinstance(raw_table.get("rows"), list):
        sections.append(raw_table)
    return sections


def _clean_table_columns(section: dict) -> list[str]:
    result: list[str] = []
    for col in section.get("columnDefs") or section.get("columnDefs_labels") or []:
        text = str(col)
        if text.startswith("tabular.data.label"):
            continue
        if "headers.current" in text:
            result.append("Current")
        elif "headers.oneyearttm" in text or "headers.oneYearTTM" in text:
            result.append("1Y TTM")
        elif "headers.fiveyear" in text or "headers.fiveYear" in text:
            result.append("5Y Avg")
        else:
            result.append(text)
    return result


def _iter_table_rows(raw_table: dict):
    for section in _table_sections(raw_table):
        columns = _clean_table_columns(section)

        def walk(node: dict):
            if not isinstance(node, dict):
                return
            if "datum" in node:
                yield {
                    "label": node.get("label", ""),
                    "salDataId": node.get("salDataId", ""),
                    "datum": node.get("datum") or [],
                    "columns": columns,
                }
            for child in node.get("subLevel") or []:
                yield from walk(child)

        for row in section.get("rows") or []:
            yield from walk(row)


def _table_series(raw_table: dict, *labels: str) -> list[tuple[str, float | None]]:
    if not raw_table:
        return []
    label_norms = [_normalize_lookup_key(label) for label in labels]
    rows = list(_iter_table_rows(raw_table))
    for row in rows:
        row_norm = _normalize_lookup_key(row.get("label"))
        if row_norm not in label_norms:
            continue
        values = [safe_float(value) for value in row.get("datum") or []]
        columns = row.get("columns") or []
        if len(columns) != len(values):
            columns = [""] * len(values)
        pairs = list(zip(columns, values))
        if any(value is not None for _, value in pairs):
            return pairs
    return []


def _latest_from_series(
    series: list[tuple[str, float | None]],
    *,
    skip_summary: bool = False,
) -> float | None:
    for column, value in reversed(series or []):
        if value is None:
            continue
        if skip_summary and _is_summary_column(column):
            continue
        return value
    return None


def _latest_table_value(raw_table: dict, *labels: str) -> float | None:
    return _latest_from_series(_table_series(raw_table, *labels), skip_summary=True)


def _valuation_row_value(raw_valuation: dict, *labels: str) -> float | None:
    return _latest_from_series(_table_series(raw_valuation, *labels), skip_summary=True)


def _is_summary_column(column: str) -> bool:
    normalized = _normalize_lookup_key(column)
    return any(marker in normalized for marker in ("avg", "average", "median", "5yr", "10yr"))


def _series_from_data_or_table(
    data_list: list[dict],
    raw_table: dict,
    data_keys: tuple[str, ...],
    table_labels: tuple[str, ...],
) -> list[tuple[str, float | None]]:
    data_series = _series_from_data_list(data_list, *data_keys)
    if any(value is not None for _, value in data_series):
        return data_series
    return _table_series(raw_table, *table_labels)


def _series_from_data_list(
    data_list: list[dict],
    *keys: str,
) -> list[tuple[str, float | None]]:
    result: list[tuple[str, float | None]] = []
    key_norms = [_normalize_lookup_key(key) for key in keys]
    for entry in data_list or []:
        norm_entry = {
            _normalize_lookup_key(key): value
            for key, value in entry.items()
            if not isinstance(value, dict)
        }
        value = None
        for key, norm_key in zip(keys, key_norms):
            value = safe_float(entry.get(key))
            if value is None:
                value = safe_float(norm_entry.get(norm_key))
            if value is not None:
                break
        column = (
            entry.get("fiscalPeriodYear")
            or (entry.get("fiscalPeriodDate", "") or entry.get("fiscalPeriodYearMonth", ""))[:4]
            or ""
        )
        result.append((str(column), value))
    return result


def _latest_numeric(series: list[tuple[str, float | None]]) -> float | None:
    return _latest_from_series(series, skip_summary=True)


def _average_latest_two(series: list[tuple[str, float | None]]) -> float | None:
    values: list[float] = []
    for column, value in reversed(series or []):
        if value is None or _is_summary_column(column):
            continue
        values.append(value)
        if len(values) == 2:
            break
    if len(values) < 2:
        return None
    return sum(values) / 2


def _growth_from_series(series: list[tuple[str, float | None]]) -> float | None:
    values: list[float] = []
    for column, value in reversed(series or []):
        normalized = _normalize_lookup_key(column)
        if value is None or _is_summary_column(column) or "ttm" in normalized:
            continue
        values.append(value)
        if len(values) == 2:
            break
    if len(values) < 2 or values[1] == 0:
        return None
    current, previous = values[0], values[1]
    return (current - previous) / previous


def _cash_conversion_cycle(
    inventory_turnover: float | None,
    receivables_turnover: float | None,
    payables_turnover: float | None,
) -> float | None:
    if not inventory_turnover or not receivables_turnover or not payables_turnover:
        return None
    return (365 / inventory_turnover) + (365 / receivables_turnover) - (365 / payables_turnover)


def _market_statement_ratio(
    market_value: float | None,
    statement_value: float | None,
) -> float | None:
    market = safe_float(market_value)
    statement = safe_float(statement_value)
    if market is None or statement in (None, 0):
        return None
    ratio_value = market / statement
    if abs(ratio_value) > 500 and abs(market) > abs(statement) * 1000:
        return market / (statement * 1_000_000)
    return ratio_value


def _market_statement_yield(
    statement_value: float | None,
    market_value: float | None,
) -> float | None:
    market = safe_float(market_value)
    statement = safe_float(statement_value)
    if market is None or market == 0 or statement is None:
        return None
    result = statement / market
    if abs(result) < 0.0001 and abs(market) > abs(statement) * 1000:
        return (statement * 1_000_000) / market
    return result


def _format_financial_ratio_value(value: Any, format_name: str) -> str:
    number = safe_float(value)
    if number is None:
        return "N/A"
    if format_name == "percent":
        return f"{number * 100:.2f}%"
    if format_name == "days":
        return f"{number:.1f} days"
    if format_name == "amount":
        return f"{number:,.2f}"
    return f"{number:.2f}x"


# -- Ownership / ESG / Analysis extractors ------------------------------------


def _latest_compensation_value(
    values: Any,
    periods: list[Any],
) -> tuple[float | None, str]:
    if not isinstance(values, list):
        return None, ""
    for index in range(len(values) - 1, -1, -1):
        value = safe_float(values[index])
        if value is None:
            continue
        period = str(periods[index]) if index < len(periods) else ""
        return value, period
    return None, ""


def _format_compensation(value: float | None, currency: str) -> str:
    if value is None:
        return ""
    amount = f"{value:,.0f}"
    return f"{amount} {currency}".strip()


def extract_people(raw: dict) -> list[dict] | None:
    """Board / executives from ``boardOfDirectors()`` or ``keyExecutives()``."""
    rows = raw.get("rows", []) if isinstance(raw, dict) else []
    periods = raw.get("datesDef", []) if isinstance(raw, dict) else []
    periods = periods if isinstance(periods, list) else []
    currency = str(raw.get("currency") or "") if isinstance(raw, dict) else ""
    result = []

    for person in rows:
        if person.get("type") != "person":
            continue

        total, total_period = _latest_compensation_value(
            person.get("totalCompensation"),
            periods,
        )
        salary = None
        salary_period = ""
        breakdown = []
        for component in person.get("compensation") or []:
            value, period = _latest_compensation_value(
                component.get("datum"),
                periods,
            )
            if value is None:
                continue
            item = {
                "name": component.get("name") or component.get("nameId") or "",
                "value": value,
                "display": _format_compensation(value, currency),
                "period": period,
            }
            breakdown.append(item)
            if str(component.get("nameId") or "").lower() == "salary":
                salary = value
                salary_period = period

        result.append(
            {
                "name": person.get("name", ""),
                "title": person.get("title", ""),
                "age": person.get("age", ""),
                "memberSince": person.get("memberSince", ""),
                "salary": salary,
                "salaryDisplay": _format_compensation(salary, currency),
                "salaryPeriod": salary_period,
                "totalCompensation": total,
                "totalCompensationDisplay": _format_compensation(total, currency),
                "compensationPeriod": total_period,
                "compensationCurrency": currency,
                "compensationBreakdown": breakdown,
            }
        )
    return result or None


def extract_institutions(raw: dict, fmt: DataFormatter) -> list[dict] | None:
    """Buyers / sellers from ``institutionBuyers()`` / ``institutionSellers()``."""
    rows = raw.get("rows", []) if isinstance(raw, dict) else []
    result = [
        {
            "secId": r.get("secId") or "",
            "name": r.get("name", ""),
            "securityType": r.get("securityType") or "",
            "ticker": r.get("ticker") or "",
            "totalSharesHeld": fmt.number(r.get("totalSharesHeld")),
            "totalAssets": fmt.number(r.get("totalAssets")),
            "currentShares": (
                f"{r.get('currentShares', 0):,}"
                if r.get("currentShares") is not None
                else ""
            ),
            "changeAmount": (
                f"{r.get('changeAmount', 0):,}"
                if r.get("changeAmount") is not None
                else ""
            ),
            "changePercentage": fmt.number(r.get("changePercentage")),
            "trend": "" if r.get("trend") == "_PO_" else (r.get("trend") or ""),
            "starRating": fmt.number(r.get("starRating")),
            "domicileCountryId": r.get("domicileCountryId") or "",
            "date": (r.get("date") or "")[:10],
        }
        for r in rows
        if r.get("name")
    ]
    return result or None


def extract_esg_risk(raw: dict) -> dict | None:
    if not raw:
        return None
    return {
        "score": raw.get("susEsgRiskScore"),
        "globes": raw.get("susEsgRiskGlobes"),
        "category": raw.get("susEsgRiskCategory"),
        "controversyLevel": raw.get("comHighestControversyLevel"),
        "controversyDescriptor": raw.get("comControversyLevelDescriptor"),
        "controversyTopics": raw.get("comHighestControversyTopics") or "",
        "notableIssues": [
            {
                "scope": raw.get(f"notableIssue{index}Name") or "",
                "issue": raw.get(f"notableIssue{index}") or "",
            }
            for index in range(1, 4)
            if raw.get(f"notableIssue{index}")
        ],
        "asOfDate": (raw.get("asOfDate") or "")[:10],
        "subIndustry": raw.get("subIndustry") or "",
        "controversyAsOfDate": (raw.get("controversyAsOfDate") or "")[:10],
    }


def extract_sustainability(raw: dict) -> dict | None:
    if not raw:
        return None
    return {
        "esgRiskScore": raw.get("esgRiskScore"),
        "esgRiskCategory": raw.get("esgRiskCategory"),
        "companyExposureScore": raw.get("companyExposureScore"),
        "companyExposureCategory": raw.get("companyExposureCategory"),
        "subindustryExposureScore": raw.get("subindustryExposureScore"),
        "subindustryExposureCategory": raw.get("subindustryExposureCategory"),
        "overallManagementScore": raw.get("overallManagementScore"),
        "overallManagementCategory": raw.get("overallManagementCategory"),
        "controllableRisk": raw.get("controllableRisk"),
        "controlledRisk": raw.get("controlledRisk"),
        "controlledRiskPer": raw.get("controlledRiskPer"),
        "neglectedRisk": raw.get("neglectedRisk"),
        "neglectedRiskPer": raw.get("neglectedRiskPer"),
        "uncontrollableRisk": raw.get("uncontrollableRisk"),
        "asOfDate": (raw.get("asOfDate") or "")[:10],
        "companyName": raw.get("companyName"),
        "peers": [
            {
                "name": p.get("companyName", ""),
                "esgRiskScore": p.get("esgRiskScore"),
                "esgRiskCategory": p.get("esgRiskCategory"),
                "companyExposureScore": p.get("companyExposureScore"),
                "companyExposureCategory": p.get("companyExposureCategory"),
                "controllableRisk": p.get("controllableRisk"),
                "controlledRisk": p.get("controlledRisk"),
                "controlledRiskPer": p.get("controlledRiskPer"),
                "neglectedRisk": p.get("neglectedRisk"),
                "neglectedRiskPer": p.get("neglectedRiskPer"),
                "uncontrollableRisk": p.get("uncontrollableRisk"),
                "overallManagementScore": p.get("overallManagementScore"),
                "overallManagementCategory": p.get("overallManagementCategory"),
                "subindustryExposureScore": p.get("subindustryExposureScore"),
                "subindustryExposureCategory": p.get(
                    "subindustryExposureCategory"
                ),
                "asOfDate": (str(p.get("asOfDate") or ""))[:10],
                "companyId": p.get("companyId") or "",
            }
            for p in (raw.get("peers") or [])
            if p.get("companyName")
        ],
    }


def extract_analysis_report(raw: dict) -> dict | None:
    if not raw:
        return None
    report = raw.get("analysisReport", {}) or {}
    if not isinstance(report, dict):
        report = {}
    smart = report.get("smartText", {}) or {}
    if not isinstance(smart, dict):
        smart = {}

    def clean_text(value: Any) -> str:
        if isinstance(value, list):
            return "\n\n".join(
                str(item).strip()
                for item in value
                if item not in (None, "", "_PO_") and str(item).strip()
            )
        if value in (None, "_PO_"):
            return ""
        return str(value).strip()

    def first_text(*values: Any) -> str:
        for value in values:
            cleaned = clean_text(value)
            if cleaned:
                return cleaned
        return ""

    author = report.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    profiles = author.get("profiles") or []
    primary_profile = next(
        (
            profile
            for profile in profiles
            if isinstance(profile, dict) and profile.get("isPrimaryProfile")
        ),
        profiles[0] if profiles and isinstance(profiles[0], dict) else {},
    )

    raw_note = raw.get("recentAnalystNote") or raw.get("analystNote") or {}
    if not isinstance(raw_note, dict):
        raw_note = {}
    analyst_note = {
        "title": clean_text(raw_note.get("title")),
        "date": clean_text(raw_note.get("date"))[:10],
        "text": first_text(
            raw_note.get("lede"),
            raw_note.get("note"),
            raw_note.get("institutionalNoteContent"),
        ),
        "author": clean_text(raw_note.get("author")),
    }
    if not any(analyst_note.values()):
        analyst_note = None

    parsed = {
        "isQuan": bool(report.get("isQuan") or raw.get("isQuan"))
        or raw.get("rpsCovered", False) is False,
        "headline": clean_text(report.get("headLine")),
        "author": first_text(
            primary_profile.get("byLine"),
            author.get("authorName"),
        ),
        "authorTitle": clean_text(primary_profile.get("jobTitle")),
        "publishDate": clean_text(
            report.get("publishDate") or smart.get("publishedDate")
        )[:10],
        "investmentThesis": first_text(
            report.get("investmentThesisText"),
            report.get("investmentThesis"),
        ),
        "economicMoat": first_text(
            report.get("economicMoatText"),
            report.get("economicMoat"),
            smart.get("economicMoatContent"),
            smart.get("economicMoatText"),
        ),
        "economicMoatHeader": first_text(
            report.get("economicMoatTitle"),
            smart.get("economicMoatHeader"),
        ),
        "valuation": first_text(
            report.get("valuationText"),
            report.get("valuation"),
            smart.get("valuationContent"),
            smart.get("valuationText"),
        ),
        "valuationHeader": first_text(
            report.get("valuationTitle"),
            smart.get("valuationHeader"),
        ),
        "risk": first_text(
            report.get("riskText"),
            report.get("risk"),
            smart.get("riskContent"),
        ),
        "riskHeader": first_text(
            report.get("riskTitle"),
            smart.get("riskHeader"),
        ),
        "management": first_text(
            report.get("managementText"),
            report.get("management"),
        ),
        "managementHeader": clean_text(report.get("managementTitle")),
        "bullsSay": first_text(report.get("bullsSay"), smart.get("bullText")),
        "bearsSay": first_text(report.get("bearsSay"), smart.get("bearText")),
        "economicMoatRating": first_text(
            report.get("economicMoatRating"),
            smart.get("economicMoatRating"),
        ),
        "valuationRating": clean_text(report.get("valuationRating")),
        "managementRating": clean_text(report.get("managementRating")),
        "riskRating": clean_text(report.get("riskRating")),
        "analystNote": analyst_note,
    }
    return parsed


def _format_short_number(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs_number >= 1_000:
        return f"{number / 1_000:.2f}K"
    return f"{number:.2f}"


def _format_percent(value: Any, *, scale: float = 1.0) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    return f"{number * scale:.2f}%"


def _format_decimal(value: Any, *, digits: int = 2) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def _date_only(value: Any) -> str:
    return (str(value or "") or "")[:10]


def _humanize_label(key: str) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", str(key or ""))
    text = text.replace("_", " ").replace("/", " / ")
    return " ".join(part.capitalize() for part in text.split())


def extract_fund_overview(
    raw_quote: dict,
    raw_metadata: dict,
    raw_performance: dict,
    search_item: dict | None = None,
    fund_name: str = "",
) -> dict | None:
    if not any((raw_quote, raw_metadata, raw_performance, search_item)):
        return None

    latest_price = safe_float(raw_quote.get("latestPrice"))
    latest_nav = safe_float(raw_quote.get("latestNav"))
    return {
        "securityName": raw_metadata.get("name") or fund_name,
        "ticker": raw_metadata.get("tradingSymbol")
        or (search_item or {}).get("ticker", ""),
        "exchange": (search_item or {}).get("exchange", ""),
        "starRating": safe_float((search_item or {}).get("star_rating")),
        "lastClose": _format_decimal(latest_price if latest_price is not None else latest_nav),
        "lastCloseDate": _date_only(
            raw_quote.get("latestPriceDate") or raw_quote.get("latestNavDate")
        ),
        "lastCloseLabel": "Latest Price" if latest_price is not None else "Latest NAV",
        "category": raw_performance.get("categoryName") or "",
        "benchmark": raw_quote.get("index") or raw_performance.get("indexName") or "",
        "fundSize": _format_short_number(raw_quote.get("fundSize")),
        "fundSizeDate": _date_only(raw_quote.get("fundSizeDate")),
        "yield12Month": _format_percent(raw_quote.get("yield12Month")),
        "ongoingCharge": _format_percent(raw_quote.get("onGoingCharge"), scale=100),
    }


def extract_fund_profile(
    raw_quote: dict,
    raw_metadata: dict,
    raw_strategy: dict,
    raw_holdings: dict,
    raw_fee: dict,
    raw_performance: dict,
) -> dict | None:
    if not any((raw_quote, raw_metadata, raw_strategy, raw_holdings, raw_fee)):
        return None

    summary = raw_holdings.get("holdingSummary", {}) or {}
    active_share = raw_holdings.get("holdingActiveShare", {}) or {}
    domicile_code = (raw_metadata.get("domicileCountryId") or "").strip().upper()
    return {
        "category": raw_performance.get("categoryName") or "",
        "benchmark": raw_quote.get("index")
        or active_share.get("primaryProspectusBenchmark")
        or raw_performance.get("indexName")
        or "",
        "domicile": _COUNTRY_NAME_BY_ISO3.get(domicile_code, domicile_code),
        "baseCurrency": raw_metadata.get("baseCurrencyId")
        or raw_quote.get("currency")
        or "",
        "quoteTemplate": raw_metadata.get("quoteTemplateForEtf")
        or raw_metadata.get("quoteTemplate")
        or "",
        "fundSize": _format_short_number(raw_quote.get("fundSize")),
        "shareSize": _format_short_number(raw_quote.get("shareSize")),
        "yield12Month": _format_percent(raw_quote.get("yield12Month")),
        "ongoingCharge": _format_percent(raw_quote.get("onGoingCharge"), scale=100),
        "expenseRatio": _format_percent(raw_fee.get("prospectusExpenseRatio")),
        "feeLevel": str(raw_fee.get("morningstarFeeLevel") or ""),
        "feeLevelPercentile": _format_percent(
            raw_fee.get("morningstarFeeLevelPercentileRank")
        ),
        "numberOfHoldings": DataFormatter.safe(
            summary.get("numberOfHolding") or raw_holdings.get("numberOfHolding")
        ),
        "topHoldingWeighting": _format_percent(summary.get("topHoldingWeighting")),
        "lastTurnover": _format_percent(summary.get("lastTurnover")),
        "turnoverDate": _date_only(summary.get("LastTurnoverDate")),
        "investmentStrategy": raw_strategy.get("investmentStrategy")
        or raw_quote.get("kiidObjective")
        or "",
    }


def extract_fund_performance(raw_performance: dict) -> dict | None:
    table = raw_performance.get("table", {}) if isinstance(raw_performance, dict) else {}
    columns = table.get("columnDefs") or []
    rows = table.get("growth10KReturnData") or []
    if not columns or not rows:
        return None

    labels = [
        ("Fund", ("fund", "fundNav"), True),
        ("Category", ("category",), True),
        ("Index", ("index",), True),
        ("Percentile Rank", ("percentileRank",), False),
        ("Fund Count", ("fundNumber",), False),
    ]
    parsed_rows: list[dict] = []
    for display_label, keys, percent_values in labels:
        source = None
        for key in keys:
            candidate = next((row for row in rows if row.get("label") == key), None)
            if candidate and any(value not in (None, "", "_PO_") for value in candidate.get("datum", [])):
                source = candidate
                break
        if source is None:
            continue

        cells: list[str] = []
        for value in source.get("datum", []):
            if percent_values:
                cells.append(_format_percent(value) if safe_float(value) is not None else "")
            else:
                cells.append(DataFormatter.safe(value))
        parsed_rows.append({"label": display_label, "cells": cells})

    if not parsed_rows:
        return None
    return {
        "asOfDate": _date_only(raw_performance.get("asOfDate")),
        "categoryName": raw_performance.get("categoryName") or "",
        "indexName": raw_performance.get("indexName") or "",
        "columns": columns,
        "rows": parsed_rows,
    }


def extract_fund_risk(
    raw_summary: dict,
    raw_volatility: dict,
    raw_score: dict,
) -> dict | None:
    if not any((raw_summary, raw_volatility, raw_score)):
        return None

    summary_cards: list[dict] = []
    for period_key, label in (("for3Year", "3Y"), ("for5Year", "5Y")):
        period = raw_summary.get(period_key) or {}
        if period.get("riskVsCategory") is not None:
            summary_cards.append(
                {
                    "label": f"{label} Risk vs Category",
                    "value": f"{period['riskVsCategory']} / 5",
                }
            )
        if period.get("returnVsCategory") is not None:
            summary_cards.append(
                {
                    "label": f"{label} Return vs Category",
                    "value": f"{period['returnVsCategory']} / 5",
                }
            )

    if raw_score.get("riskLevel") is not None:
        summary_cards.append(
            {
                "label": "Risk Level",
                "value": DataFormatter.safe(raw_score.get("riskLevel")),
            }
        )
    if raw_volatility.get("calculationBenchmark"):
        summary_cards.append(
            {
                "label": "Benchmark",
                "value": raw_volatility.get("calculationBenchmark"),
            }
        )

    fund_vol = raw_volatility.get("fundRiskVolatility", {}) or {}
    periods = [("for1Year", "1Y"), ("for3Year", "3Y"), ("for5Year", "5Y")]
    metric_specs = (
        ("Alpha", "alpha", False),
        ("Beta", "beta", False),
        ("R-Squared", "rSquared", False),
        ("Std Deviation", "standardDeviation", True),
        ("Sharpe Ratio", "sharpeRatio", False),
    )
    metric_rows: list[dict] = []
    for label, key, percent_value in metric_specs:
        cells: list[str] = []
        has_value = False
        for period_key, _period_label in periods:
            value = (fund_vol.get(period_key) or {}).get(key)
            if safe_float(value) is None:
                cells.append("")
                continue
            has_value = True
            cells.append(
                _format_percent(value) if percent_value else _format_decimal(value)
            )
        if has_value:
            metric_rows.append({"label": label, "cells": cells})

    if not summary_cards and not metric_rows:
        return None
    return {
        "asOfDate": _date_only(raw_summary.get("endDate") or fund_vol.get("endDate")),
        "periods": [label for _, label in periods],
        "summaryCards": summary_cards,
        "metricRows": metric_rows,
    }


def extract_fund_holdings(raw_holdings: dict, *, limit: int = 10) -> list[dict] | None:
    if not raw_holdings:
        return None
    holding_pages = (
        raw_holdings.get("equityHoldingPage"),
        raw_holdings.get("boldHoldingPage"),
        raw_holdings.get("otherHoldingPage"),
    )
    holding_list: list[dict] = []
    for page in holding_pages:
        if isinstance(page, dict) and page.get("holdingList"):
            holding_list = page.get("holdingList") or []
            break
    if not holding_list:
        return None

    result = []
    for holding in holding_list[:limit]:
        result.append(
            {
                "name": holding.get("securityName", ""),
                "ticker": holding.get("ticker", ""),
                "weighting": _format_percent(holding.get("weighting")),
                "country": holding.get("country", ""),
                "sector": holding.get("sector", ""),
                "assessment": holding.get("assessment", ""),
                "stockRating": holding.get("stockRating", ""),
                "economicMoat": holding.get("economicMoat", ""),
            }
        )
    return result or None


def extract_fund_sector_allocation(raw_sector: dict) -> dict | None:
    if not raw_sector:
        return None
    block = raw_sector.get("EQUITY") or raw_sector.get(raw_sector.get("assetType", ""))
    if not isinstance(block, dict):
        return None

    fund_portfolio = block.get("fundPortfolio", {}) or {}
    category_portfolio = block.get("categoryPortfolio", {}) or {}
    index_portfolio = block.get("indexPortfolio", {}) or {}
    rows: list[dict] = []
    for key, fund_value in fund_portfolio.items():
        if key == "portfolioDate":
            continue
        category_value = category_portfolio.get(key)
        index_value = index_portfolio.get(key)
        if all(safe_float(v) is None for v in (fund_value, category_value, index_value)):
            continue
        rows.append(
            {
                "label": _humanize_label(key),
                "fund": _format_percent(fund_value),
                "category": _format_percent(category_value),
                "index": _format_percent(index_value),
            }
        )

    if not rows:
        return None
    return {
        "portfolioDate": _date_only(fund_portfolio.get("portfolioDate")),
        "categoryName": block.get("categoryName") or "",
        "indexName": block.get("indexName") or "",
        "rows": rows,
    }


def extract_fund_sustainability(raw_esg: dict) -> dict | None:
    if not raw_esg:
        return None
    carbon = raw_esg.get("carbon", {}) or {}
    return {
        "score": safe_float(raw_esg.get("fundSustainabilityScore")),
        "quintile": raw_esg.get("sustainabilityFundQuintile"),
        "coverage": safe_float(raw_esg.get("percentAUMCoveredESG")),
        "categoryAverageScore": safe_float(
            raw_esg.get("historicalSustainabilityScoreGlobalCategoryAverage")
        ),
        "categoryName": raw_esg.get("globalCategoryName") or "",
        "portfolioDate": _date_only(raw_esg.get("portfolioDate")),
        "rankDate": _date_only(raw_esg.get("categoryRankDate")),
        "fossilFuelInvolvement": safe_float(carbon.get("fossilFuelInvolvementPct")),
        "carbonRiskScore": safe_float(carbon.get("carbonRiskScore")),
        "carbonCategoryAverage": safe_float(carbon.get("carbonRiskScoreCategoryAverage")),
    }


# -- Search response parser ---------------------------------------------------


def _parse_search_response(text: str) -> list[dict]:
    """Parse the legacy-search/securities JSON response."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    results: list[dict] = []
    for item in payload.get("results", []):
        meta = item.get("meta") or {}
        fields = item.get("fields") or {}
        sec_id = meta.get("securityID", "")
        name = (fields.get("name") or {}).get("value", "")
        if not sec_id or not name:
            continue
        universe = meta.get("universe", "")
        results.append(
            {
                "name": name,
                "ticker": (fields.get("ticker") or {}).get("value", "") or meta.get("ticker", ""),
                "exchange": (fields.get("exchange") or {}).get("value", "") or meta.get("exchange", ""),
                "secId": sec_id,
                "performanceId": meta.get("performanceID", sec_id),
                "type": _SEARCH_UNIVERSE_TYPE.get(universe, "STOCK"),
                "star_rating": "",
            }
        )
    return results


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — MorningstarClient  (orchestrator)
# ══════════════════════════════════════════════════════════════════════════════


class MorningstarClient:
    """High-level Morningstar data client.

    Composes ``StockAPI`` (layer 1) with ``extract_*`` functions (layer 2)
    to expose the same public API consumed by ``views.py`` and
    ``valuation.py``.
    """

    def __init__(
        self,
        formatter: DataFormatter | None = None,
        parser: TableParser | None = None,
    ) -> None:
        self._fmt = formatter or DataFormatter()
        self._parser = parser or TableParser(self._fmt)

    # ══════════════════════════════════════════════════════════════════════
    # PUBLIC METHODS
    # ══════════════════════════════════════════════════════════════════════

    def search_assets(self, query: str, limit: int = 12) -> list[dict]:
        """Return matching assets from the Morningstar autocomplete API."""
        try:
            return _search_assets_http(query, limit=limit)
        except Exception as exc:
            logger.warning("Morningstar autocomplete error: %s", exc)
            return []

    # -- ISIN lookup (lightweight) ---------------------------------------------

    def get_isin(self, sec_id: str) -> str:
        """Return the ISIN for *sec_id* without fetching full asset data.

        Returns an empty string when the ISIN cannot be resolved.
        """
        try:
            candidates = _search_assets_http(sec_id, limit=12)
            resolved = _pick_best_search_result(sec_id, candidates)
            if resolved and _is_fund_like_type(resolved.get("type", "")):
                return FundAPI(sec_id).isin or ""
            return StockAPI(sec_id).isin or ""
        except Exception:
            return ""

    # -- Full asset data (detail page) -----------------------------------------

    def fetch_asset_data(
        self,
        term: str,
        *,
        include_valuation: bool = True,
    ) -> dict[str, Any]:
        """Fetch comprehensive fundamental data for a single stock/ETF."""
        search_item = _pick_best_search_result(
            term,
            self.search_assets(term, limit=12),
        )
        if search_item and _is_fund_like_type(search_item.get("type", "")):
            api = FundAPI(term, exchange=search_item.get("exchange") or None)
            raw_metadata = api.security_metadata()
            raw_quote = api.quote()
            raw_strategy = api.investment_strategy()
            raw_performance = api.performance_table()
            raw_risk_summary = api.risk_return_summary()
            raw_risk_volatility = api.risk_volatility()
            raw_risk_score = api.risk_score()
            raw_sector = api.sector()
            raw_holdings = api.holdings()
            raw_esg = api.esg_risk()
            raw_fee = api.fee_level()

            result: dict[str, Any] = {
                "assetType": (search_item.get("type") or "ETF").strip().upper(),
                "name": api.name,
                "isin": api.isin or term,
                "overview": extract_fund_overview(
                    raw_quote,
                    raw_metadata,
                    raw_performance,
                    search_item=search_item,
                    fund_name=api.name,
                ),
                "fundProfile": extract_fund_profile(
                    raw_quote,
                    raw_metadata,
                    raw_strategy,
                    raw_holdings,
                    raw_fee,
                    raw_performance,
                ),
                "etfPerformance": extract_fund_performance(raw_performance),
                "etfRisk": extract_fund_risk(
                    raw_risk_summary,
                    raw_risk_volatility,
                    raw_risk_score,
                ),
                "holdings": extract_fund_holdings(raw_holdings),
                "sectorExposure": extract_fund_sector_allocation(raw_sector),
                "sustainability": extract_fund_sustainability(raw_esg),
                "valuation": None,
                "companyProfile": None,
                "analysisReport": None,
            }
            return result

        api = StockAPI(term)
        result: dict[str, Any] = {
            "assetType": "STOCK",
            "name": api.name,
            "isin": api.isin or term,
        }

        # Overview + company profile
        try:
            raw_ov = api.overview()
            result["overview"] = extract_overview(raw_ov, api.trading_info(), api.name)
        except Exception as exc:
            logger.warning("overview error: %s", exc)
            raw_ov = {}
            result["overview"] = None

        try:
            result["companyProfile"] = extract_company_profile(api.company_profile())
        except Exception as exc:
            logger.warning("companyProfile error: %s", exc)
            result["companyProfile"] = None

        # Financial tables (parser-based)
        raw_financials: dict[str, dict] = {}
        financials: list[tuple[str, Any, Any]] = [
            ("keyMetrics", api.key_metrics, self._parser.from_key_metrics),
            ("incomeStatement", api.income_statement, self._parser.from_table),
            ("balanceSheet", api.balance_sheet, self._parser.from_table),
            ("cashFlow", api.cash_flow, self._parser.from_table),
            ("dividends", api.dividends, self._parser.from_table),
            ("profitability", api.profitability, self._parser.from_datalist),
            ("operatingGrowth", api.operating_growth, self._parser.from_datalist),
            ("financialHealth", api.financial_health, self._parser.from_datalist),
            ("freeCashFlow", api.free_cash_flow, self._parser.from_datalist),
        ]
        if include_valuation:
            financials.insert(1, ("valuation", api.valuation, self._parser.from_table))
        else:
            result["valuation"] = None

        for key, fetcher, parser in financials:
            try:
                raw = fetcher()
                raw_financials[key] = raw if isinstance(raw, dict) else {}
                result[key] = parser(raw)
            except Exception as exc:
                logger.warning("%s error: %s", key, exc)
                raw_financials[key] = {}
                result[key] = None

        if "valuation" not in raw_financials:
            try:
                raw_financials["valuation"] = api.valuation()
            except Exception as exc:
                logger.warning("valuation ratios raw fetch error: %s", exc)
                raw_financials["valuation"] = {}

        try:
            result["financialRatios"] = extract_financial_ratios(
                raw_overview=raw_ov,
                raw_key_metrics=raw_financials.get("keyMetrics"),
                raw_valuation=raw_financials.get("valuation"),
                raw_income_statement=raw_financials.get("incomeStatement"),
                raw_balance_sheet=raw_financials.get("balanceSheet"),
                raw_cash_flow=raw_financials.get("cashFlow"),
                raw_profitability=raw_financials.get("profitability"),
                raw_operating_growth=raw_financials.get("operatingGrowth"),
                raw_financial_health=raw_financials.get("financialHealth"),
                raw_free_cash_flow=raw_financials.get("freeCashFlow"),
            )
        except Exception as exc:
            logger.warning("financialRatios error: %s", exc)
            result["financialRatios"] = None

        # Ownership
        for attr, fetcher, extractor, extra in [
            ("boardOfDirectors", api.board_of_directors, extract_people, None),
            ("keyExecutives", api.key_executives, extract_people, None),
            ("institutionBuyers", api.institution_buyers, extract_institutions, self._fmt),
            ("institutionSellers", api.institution_sellers, extract_institutions, self._fmt),
        ]:
            try:
                raw = fetcher()
                result[attr] = extractor(raw, extra) if extra is not None else extractor(raw)
            except Exception as exc:
                logger.warning("%s error: %s", attr, exc)
                result[attr] = None

        # ESG
        result["esgRisk"] = extract_esg_risk(api.esg_risk())
        result["sustainability"] = extract_sustainability(api.sustainability())

        # Analyst report
        result["analysisReport"] = extract_analysis_report(api.analysis_report())

        return result

    # -- Comparable companies --------------------------------------------------

    def _fetch_base_company_data(self, base_isin: str) -> dict | None:
        """Fetch sector, financials and country for *base_isin*.

        Returns a dict with keys: api, name, sector, industry, market_cap,
        price_earnings, price_book, domicile_code.
        Returns ``None`` when the stock cannot be resolved or has no sector.
        """
        try:
            api = StockAPI(base_isin)
        except Exception as exc:
            logger.warning(
                "find_comparables: cannot create Stock for %s: %s", base_isin, exc
            )
            return None

        raw_overview = api.overview()
        sector = raw_overview.get("sector", "")
        if not sector:
            return None

        sections = api.company_profile().get("sections", {})
        country_name = (sections.get("contact", {}).get("country", "") or "").strip()

        return {
            "api": api,
            "raw_overview": raw_overview,
            "name": api.name,
            "sector": sector,
            "industry": raw_overview.get("industry", ""),
            "market_cap": safe_float(raw_overview.get("marketCap")),
            "price_earnings": safe_float(raw_overview.get("priceEarnings")),
            "price_book": safe_float(raw_overview.get("priceBook")),
            "domicile_code": _COUNTRY_ISO3.get(country_name.lower(), ""),
        }

    def _run_screener(
        self,
        sector: str,
        industry: str,
        base_name: str,
    ) -> list[dict]:
        """Run the Morningstar screener and exclude the base company by name."""
        filters: dict[str, Any] = {"investmentType": "EQ", "sector": sector}
        if industry:
            filters["industry"] = industry

        mstarpy_module = _load_mstarpy()
        if mstarpy_module is None:
            logger.warning("Morningstar screener is unavailable outside the main thread.")
            return []
        session = mstarpy_module.MorningstarSession()
        print("Running screener with filters:", filters)
        raw = session.screener_universe(
            "a",
            field=[
                "name",
                "isin",
                "ticker",
                "exchange",
                "sector",
                "industry",
                "priceToEarnings",
                "marketCap",
                "priceToBook",
                "domicile",
            ],
            filters=filters,
            pageSize=200,
        )
        print(f"Screener returned {len(raw)} results before filtering")
        print("\n base_name: ", base_name)
        print("\n raw: ", raw)
        return [
            x
            for x in raw
            if x.get("fields", {}).get("name", {}).get("value") != base_name
        ]

    def _filter_screener_results(
        self,
        raw: list[dict],
        base_isin: str,
        base_domicile_code: str,
        mc_lo: float | None,
        mc_hi: float | None,
        pe_lo: float | None,
        pe_hi: float | None,
        pb_lo: float | None,
        pb_hi: float | None,
        base_company_id: str | None = None,
    ) -> dict[str, dict]:
        """Deduplicate screener results by ISIN, applying numeric bounds.

        Keeps the listing with the best exchange rank when the same ISIN
        appears on multiple exchanges. Filters to companies in the same
        economy as the base company.
        Returns a dict keyed by ISIN.
        """
        base_economy = _ISO3_TO_ECONOMY.get(base_domicile_code)
        by_isin: dict[str, dict] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            isin = _sf(item, "isin") or ""
            if not isin or isin == base_isin:
                continue
            if base_company_id and item.get("meta", {}).get("companyID") == base_company_id:
                continue

            item_domicile = _sf(item, "domicile") or ""
            if base_economy and _ISO3_TO_ECONOMY.get(item_domicile) != base_economy:
                continue

            item_mc = safe_float(_sf(item, "marketCap"))
            item_pe = safe_float(_sf(item, "priceToEarnings"))
            item_pb = safe_float(_sf(item, "priceToBook"))
            if mc_lo and item_mc and item_mc < mc_lo:
                continue
            if mc_hi and item_mc and item_mc > mc_hi:
                continue
            if pe_lo and item_pe and item_pe < pe_lo:
                continue
            if pe_hi and item_pe and item_pe > pe_hi:
                continue
            if pb_lo and item_pb and item_pb < pb_lo:
                continue
            if pb_hi and item_pb and item_pb > pb_hi:
                continue

            exch = _sf(item, "exchange") or ""
            row = {
                "name": _sf(item, "name") or "",
                "isin": isin,
                "ticker": _sf(item, "ticker") or "",
                "exchange": exch,
                "sector": _sf(item, "sector") or "",
                "industry": _sf(item, "industry") or "",
                "screener_pe": item_pe,
            }
            existing = by_isin.get(isin)
            if existing is None or _exchange_rank(exch) < _exchange_rank(
                existing["exchange"]
            ):
                by_isin[isin] = row

        print(f"{len(by_isin)} unique ISINs after filtering")

        return by_isin

    def _deduplicate_by_name(
        self,
        by_isin: dict[str, dict],
        base_exchange: str,
        max_results: int,
    ) -> list[dict]:
        """Keep one listing per company name, preferring the base exchange."""
        peers_by_name: dict[str, dict] = {}
        for peer in by_isin.values():
            name = peer["name"]
            if name not in peers_by_name:
                peers_by_name[name] = peer
            elif peer["exchange"] == base_exchange:
                # Prefer the listing on the same exchange as the base company
                peers_by_name[name] = peer
        return list(peers_by_name.values())[:max_results]

    def _enrich_with_multiples(
        self,
        peers: list[dict],
        base_isin: str,
    ) -> tuple[list[dict], dict]:
        """Fetch valuation multiples for base + peers in parallel.

        Returns (filtered_peers, base_data) where filtered_peers contains only
        companies with all three multiples present (PE, EV/EBIT, EV/EBITDA).
        """
        all_isins = [base_isin] + [p["isin"] for p in peers]
        multiples_map: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(len(all_isins), 8)) as pool:
            futures = {
                pool.submit(self.get_valuation_multiples, isin_): isin_
                for isin_ in all_isins
            }
            for fut in as_completed(futures):
                data = fut.result()
                if data:
                    multiples_map[data["isin"]] = data

        filtered_peers = []
        for peer in peers:
            m = multiples_map.get(peer["isin"], {})
            peer["pe"] = peer.pop("screener_pe", None) or m.get("pe")
            peer["ev_to_ebitda"] = m.get("ev_to_ebitda")
            peer["ev_to_ebit"] = m.get("ev_to_ebit")
            # Keep only peers where all three multiples are available
            if (
                peer["pe"] is not None
                and peer["ev_to_ebitda"] is not None
                and peer["ev_to_ebit"] is not None
            ):
                filtered_peers.append(peer)

        base_data = multiples_map.get(base_isin, {"isin": base_isin})
        return filtered_peers, base_data

    def find_comparables(
        self,
        base_isin: str,
        max_results: int = 30,
        filter_overrides: dict | None = None,
    ) -> dict:
        """Find comparable companies via ``screener_universe()`` and fetch
        their valuation multiples.

        Returns ``{"results": [...], "base": {...}, "filters_used": {...}}``.
        """
        if not base_isin:
            return {"results": [], "base": None, "filters_used": {}}

        f = {**_COMP_FILTER_DEFAULTS, **(filter_overrides or {})}

        base = self._fetch_base_company_data(base_isin)
        if base is None:
            return {"results": [], "base": None, "filters_used": {}}

        mc_lo = pct_bound(base["market_cap"], f.get("mc_min_pct"))
        mc_hi = pct_bound(base["market_cap"], f.get("mc_max_pct"))
        pe_lo = pct_bound(base["price_earnings"], f.get("pe_min_pct"))
        pe_hi = pct_bound(base["price_earnings"], f.get("pe_max_pct"))
        pb_lo = pct_bound(base["price_book"], f.get("pb_min_pct"))
        pb_hi = pct_bound(base["price_book"], f.get("pb_max_pct"))

        filters_used = {
            "sector": base["sector"],
            "domicile": base["domicile_code"],
            "price_earnings": base["price_earnings"],
            "price_book": base["price_book"],
            "industry": base["industry"],
            **{k: f.get(k) for k in _COMP_FILTER_DEFAULTS},
        }

        try:
            raw = self._run_screener(
                base["sector"], base["industry"], base["name"]
            )
            print(f"Found {len(raw)} raw screener results")
        except Exception as exc:
            logger.warning("screener error: %s", exc)
            return {"results": [], "base": None, "filters_used": filters_used}

        if not raw:
            return {"results": [], "base": None, "filters_used": filters_used}

        base_company_id = base["api"].balance_sheet().get("_meta", {}).get("companyId")
        by_isin = self._filter_screener_results(
            raw, base_isin, base["domicile_code"], mc_lo, mc_hi, pe_lo, pe_hi, pb_lo, pb_hi,
            base_company_id=base_company_id,
        )
        base_exchange = _sf(base["raw_overview"], "exchange") or ""
        peers = self._deduplicate_by_name(by_isin, base_exchange, max_results)
        filtered_peers, base_data = self._enrich_with_multiples(peers, base_isin)

        return {
            "results": filtered_peers,
            "base": base_data,
            "filters_used": filters_used,
        }

    # -- Data methods (valuation / DCF) ----------------------------------------

    def get_valuation_multiples(
        self,
        isin: str,
        exchange: str | None = None,
    ) -> dict | None:
        """PE, EV/EBIT, EV/EBITDA with PE fallback chain.

        Returns ``{"isin": ..., "pe": ..., ...}`` or ``None`` on failure.
        """
        try:
            api = StockAPI(isin, exchange=exchange)
            raw_val = api.valuation()
            raw_ov = api.overview()
            multiples = extract_valuation_multiples(raw_val, raw_ov)
            if not multiples:
                return None
            return {"isin": isin, **multiples}
        except Exception as exc:
            logger.debug("get_valuation_multiples failed for %s: %s", isin, exc)
            return None

    def get_base_financials(
        self,
        isin: str,
        exchange: str | None = None,
    ) -> dict | None:
        """EBITDA, EBIT, EPS, shares, net debt, currency.

        Returns ``{"isin": ..., "ebitda": ..., ...}`` or ``None``.
        """
        try:
            api = StockAPI(isin, exchange=exchange)
            result = extract_base_financials(api.key_metrics())
            if result is None:
                return None
            return {"isin": isin, **result}
        except Exception as exc:
            logger.debug("get_base_financials failed for %s: %s", isin, exc)
            return None
