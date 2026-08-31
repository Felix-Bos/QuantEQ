from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from analysis.services.climate_data import fetch_climate_profile
from analysis.services.quantitative import build_quantitative_analysis


class MarketDataError(Exception):
    """Raised when the configured providers cannot satisfy a request."""


logger = logging.getLogger(__name__)

_KNOWN_MORNINGSTAR_SECURITIES = {
    "MC.PA": {
        "sec_id": "0P0001QHYU",
        "isin": "FR0000121014",
    },
    "FR0000121014": {
        "sec_id": "0P0001QHYU",
        "isin": "FR0000121014",
    },
    "LVMH": {
        "sec_id": "0P0001QHYU",
        "isin": "FR0000121014",
    },
}


def _add_project_root_to_path() -> None:
    project_root = Path(__file__).resolve().parents[3]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)


def get_morningstar_client():
    _add_project_root_to_path()
    try:
        from fetch_data.get_mrnstar import MorningstarClient
    except ImportError as exc:
        raise MarketDataError(
            "Morningstar dependencies are unavailable. Install project requirements."
        ) from exc
    return MorningstarClient()


def search_assets(query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    normalized_query = (query or "").strip()
    if len(normalized_query) < 2:
        return []

    normalized_limit = max(1, min(limit, 25))
    try:
        morningstar_results = get_morningstar_client().search_assets(
            normalized_query,
            limit=normalized_limit,
        )
    except Exception as exc:
        raise MarketDataError("Morningstar search is unavailable.") from exc

    return _deduplicate_search_results(
        _normalize_morningstar_results(morningstar_results)
    )[:normalized_limit]


def fetch_company_data(
    reference: str,
    *,
    period: str = "5y",
) -> dict[str, Any]:
    normalized_reference = (reference or "").strip()
    if not normalized_reference:
        raise ValueError("A security identifier is required.")

    sec_id = _resolve_morningstar_reference(normalized_reference)
    morningstar_client = get_morningstar_client()
    morningstar_data = morningstar_client.fetch_asset_data(sec_id)
    morningstar_data["provider"] = "MORNINGSTAR"
    _normalize_asset_financial_tables(morningstar_data)
    _attach_provider_data(
        morningstar_data,
        sec_id=sec_id,
        requested_reference=normalized_reference,
        period=period,
    )

    overview = morningstar_data.get("overview") or {}
    return {
        "security": {
            "sec_id": sec_id,
            "isin": (morningstar_data.get("isin") or "").strip().upper(),
            "ticker": (overview.get("ticker") or "").strip().upper(),
            "exchange": (overview.get("exchange") or "").strip().upper(),
            "asset_type": (morningstar_data.get("assetType") or "").strip().upper(),
            "provider": "MORNINGSTAR",
        },
        "morningstar": morningstar_data,
        "climate": morningstar_data.get("climateData"),
        "quantitative": morningstar_data.get("quantitative"),
    }


def fetch_asset_detail(
    reference: str,
    *,
    period: str = "5y",
) -> dict[str, Any]:
    """Return one frontend-ready asset payload without persistent caching."""
    normalized_reference = (reference or "").strip()
    if not normalized_reference:
        raise ValueError("A security identifier is required.")

    sec_id = _resolve_morningstar_reference(normalized_reference)
    asset = get_morningstar_client().fetch_asset_data(
        sec_id,
        include_valuation=True,
    )
    asset["provider"] = "MORNINGSTAR"
    _normalize_asset_financial_tables(asset)
    _attach_provider_data(
        asset,
        sec_id=sec_id,
        requested_reference=normalized_reference,
        period=period,
    )
    return asset


def _resolve_morningstar_reference(reference: str) -> str:
    known_security = _KNOWN_MORNINGSTAR_SECURITIES.get(reference.upper())
    if known_security:
        return known_security["sec_id"]
    if _looks_like_morningstar_reference(reference):
        return reference

    results = search_assets(reference, limit=8)
    stocks = [
        result
        for result in results
        if (result.get("type") or "").strip().upper() == "STOCK"
    ]
    if not stocks:
        raise ValueError("Unknown company in Morningstar.")
    return stocks[0]["secId"]


def _attach_provider_data(
    asset: dict[str, Any],
    *,
    sec_id: str,
    requested_reference: str,
    period: str,
) -> None:
    overview = asset.get("overview") or {}
    name_candidates = [
        overview.get("securityName"),
        asset.get("name"),
        requested_reference,
        overview.get("ticker"),
    ]
    primary_name = next(
        (str(name).strip() for name in name_candidates if str(name or "").strip()),
        requested_reference,
    )
    asset["climateData"] = fetch_climate_profile(
        primary_name,
        aliases=[
            str(name).strip()
            for name in name_candidates
            if str(name or "").strip() and str(name).strip() != primary_name
        ],
    )

    quantitative = _fetch_morningstar_quantitative(
        sec_id=sec_id,
        isin=(asset.get("isin") or "").strip(),
        ticker=(overview.get("ticker") or "").strip(),
        currency=(overview.get("currency") or "").strip(),
        period=period,
    )
    asset["quantitative"] = quantitative
    if quantitative:
        series = quantitative.get("series") or []
        latest = series[-1] if series else {}
        overview = asset.setdefault("overview", {})
        if latest.get("close") is not None and not overview.get("lastClose"):
            overview["lastClose"] = latest["close"]
        if latest.get("date") and not overview.get("lastCloseDate"):
            overview["lastCloseDate"] = latest["date"]


def _fetch_morningstar_quantitative(
    *,
    sec_id: str,
    isin: str,
    ticker: str,
    currency: str,
    period: str,
) -> dict[str, Any] | None:
    years = _period_to_years(period)
    lookup_terms = [term for term in (isin, sec_id) if term]
    for term in lookup_terms:
        history = _fetch_morningstar_history(term, years=years)
        if not history:
            continue
        return build_quantitative_analysis(
            history,
            ticker=ticker or term,
            currency=currency,
            source="Morningstar",
        )
    return None


def _fetch_morningstar_history(term: str, *, years: int) -> list[dict[str, Any]]:
    try:
        import mstarpy
    except Exception as exc:
        logger.info("Morningstar price history requires mstarpy: %s", exc)
        return []

    end = date.today()
    start = end - timedelta(days=int((years + 0.25) * 365.25))
    try:
        stock = mstarpy.Stock(term)
        rows = stock.TimeSeries("close", start, end, frequency="daily")
    except Exception as exc:
        logger.warning("Morningstar TimeSeries failed for %s: %s", term, exc)
        return []

    history = []
    for row in rows or []:
        close = _safe_float(row.get("close"))
        row_date = row.get("date")
        if close is None or not row_date:
            continue
        if hasattr(row_date, "date"):
            row_date = row_date.date().isoformat()
        else:
            row_date = str(row_date)[:10]
        history.append(
            {
                "date": row_date,
                "open": _safe_float(row.get("open")) or close,
                "high": _safe_float(row.get("high")) or close,
                "low": _safe_float(row.get("low")) or close,
                "close": close,
                "volume": int(_safe_float(row.get("volume")) or 0),
            }
        )
    return history


def _period_to_years(period: str) -> int:
    return {
        "1mo": 1,
        "3mo": 1,
        "6mo": 1,
        "1y": 1,
        "3y": 3,
        "5y": 5,
        "10y": 10,
        "max": 15,
    }.get((period or "").strip().lower(), 5)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _looks_like_real_isin(value: str) -> bool:
    normalized = (value or "").strip().upper()
    return (
        len(normalized) == 12
        and normalized[:2].isalpha()
        and not normalized.startswith(("ZZ", "0P"))
    )


def _looks_like_morningstar_reference(value: str) -> bool:
    normalized = (value or "").strip().upper()
    return (
        normalized.startswith("0P")
        or normalized.startswith("F0")
        or normalized.startswith("FO")
        or _looks_like_real_isin(normalized)
    )


def _normalize_morningstar_results(
    results: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized_results = []
    for result in results or []:
        sec_id = str(result.get("secId") or "").strip()
        ticker = str(result.get("ticker") or "").strip().upper()
        asset_type = str(result.get("type") or "").strip().upper()
        if not sec_id or asset_type not in {"STOCK", "ETF", "FUND"}:
            continue
        normalized_results.append(
            {
                **result,
                "secId": sec_id,
                "ticker": ticker,
                "exchange": str(result.get("exchange") or "").strip().upper(),
                "type": "ETF" if asset_type == "FUND" else asset_type,
                "provider": "MORNINGSTAR",
            }
        )
    return normalized_results


def _deduplicate_search_results(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduplicated = []
    seen = set()
    for result in results:
        key = (
            str(result.get("ticker") or "").strip().upper(),
            str(result.get("exchange") or "").strip().upper(),
        )
        if not key[0]:
            key = (str(result.get("secId") or "").strip().upper(), key[1])
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(result)
    return deduplicated


_FINANCIAL_TABLE_KEYS = (
    "keyMetrics",
    "incomeStatement",
    "balanceSheet",
    "cashFlow",
    "valuation",
    "dividends",
    "profitability",
    "operatingGrowth",
    "financialHealth",
    "freeCashFlow",
)


def _normalize_asset_financial_tables(asset: dict[str, Any]) -> None:
    for key in _FINANCIAL_TABLE_KEYS:
        table = asset.get(key)
        if table:
            asset[key] = _normalize_financial_table(table)


def _normalize_financial_table(table: dict[str, Any]) -> dict[str, Any]:
    """Put annual periods newest-first while preserving each row's alignment."""
    columns = table.get("columns") or []
    rows = table.get("flat_rows") or []
    if len(columns) < 2:
        return table

    special_periods = {
        "CURRENT": 0,
        "TTM": 1,
        "LAST QUARTER": 2,
        "1Y TTM": 3,
        "5Y AVG": 4,
        "5-YR": 5,
        "VALUE": 6,
    }

    def sort_key(index: int) -> tuple[int, int, int]:
        label = str(columns[index]).strip()
        year_match = __import__("re").search(r"(?:19|20)\d{2}", label)
        if year_match:
            return (0, -int(year_match.group()), index)
        return (1, special_periods.get(label.upper(), 100), index)

    order = sorted(range(len(columns)), key=sort_key)
    if order == list(range(len(columns))):
        return table

    return {
        **table,
        "columns": [columns[index] for index in order],
        "flat_rows": [
            {
                **row,
                "cells": [
                    row.get("cells", [])[index]
                    if index < len(row.get("cells", []))
                    else ""
                    for index in order
                ],
            }
            for row in rows
        ],
    }
