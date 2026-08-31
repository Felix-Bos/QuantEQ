from __future__ import annotations

import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrape_eurostoxx600 import ALIASES, scrape_single_company  # noqa: E402


_LEGAL_SUFFIXES = {
    "ab",
    "ag",
    "as",
    "asa",
    "co",
    "corp",
    "gmbh",
    "group",
    "holding",
    "holdings",
    "inc",
    "kgaa",
    "limited",
    "ltd",
    "nv",
    "oyj",
    "plc",
    "publ",
    "sa",
    "se",
    "spa",
}

_TAB_MAP = {
    "ghg": "ghgEmissions",
    "targets": "climateTargets",
    "taxonomy": "euTaxonomy",
    "energy": "energyManagement",
    "waste": "wasteManagement",
}


def fetch_climate_profile(
    company_name: str,
    *,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Return Tracenable climate data for a company name.

    The resolver deliberately uses only Tracenable pages. It tries deterministic
    aliases and slug candidates, then keeps the first candidate with published
    climate rows.
    """
    names = [company_name, *(aliases or [])]
    candidates = _slug_candidates(names)

    best_result = None
    for slug in candidates:
        result = _scrape_cached(_primary_name(names), slug)
        if _row_count(result):
            best_result = result
            break
        if best_result is None:
            best_result = result

    if best_result is None:
        return {
            "provider": "TRACENABLE",
            "companyName": company_name,
            "slug": "",
            "status": "NO_SLUG",
            "tabs": {},
            "summary": {},
        }

    tabs = {
        frontend_key: _normalize_table(best_result.get(source_key))
        for source_key, frontend_key in _TAB_MAP.items()
    }
    tabs = {key: value for key, value in tabs.items() if value is not None}
    summary = {
        key: len(value["rows"])
        for key, value in tabs.items()
    }
    slug = best_result.get("slug") or ""
    status = "FOUND" if summary else ("NO_DATA" if slug else "NO_SLUG")
    return {
        "provider": "TRACENABLE",
        "companyName": company_name,
        "matchedName": best_result.get("company") or company_name,
        "slug": slug,
        "url": f"https://tracenable.com/company/{slug}" if slug else "",
        "status": status,
        "tabs": tabs,
        "summary": summary,
    }


def _primary_name(names: list[str]) -> str:
    for name in names:
        if (name or "").strip():
            return name.strip()
    return ""


@lru_cache(maxsize=512)
def _scrape_cached(company_name: str, slug: str) -> dict[str, Any]:
    return scrape_single_company(company_name, slug)


def _row_count(result: dict[str, Any]) -> int:
    total = 0
    for source_key in _TAB_MAP:
        block = result.get(source_key)
        if isinstance(block, dict):
            total += len(block.get("rows") or [])
    return total


def _normalize_table(block: Any) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    columns = [str(col or "").strip() for col in block.get("header") or []]
    rows = [
        [str(cell or "").strip() for cell in row]
        for row in block.get("rows") or []
        if any(str(cell or "").strip() for cell in row)
    ]
    if not columns or not rows:
        return None
    return {"columns": columns, "rows": rows}


def _slug_candidates(names: list[str]) -> list[str]:
    candidates: list[str] = []
    for raw_name in names:
        name = (raw_name or "").strip()
        if not name:
            continue
        if name in ALIASES:
            candidates.append(ALIASES[name])
        normalized_name = _normalize_text(name)
        for alias_name, slug in ALIASES.items():
            alias_norm = _normalize_text(alias_name)
            if normalized_name == alias_norm or normalized_name in alias_norm:
                candidates.append(slug)

        candidates.extend(_slug_variants(name))

    result = []
    seen = set()
    for candidate in candidates:
        slug = candidate.strip("-").lower()
        if slug and slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result


def _slug_variants(name: str) -> list[str]:
    normalized = _normalize_text(name)
    tokens = [token for token in normalized.split() if token not in _LEGAL_SUFFIXES]
    compact_name = " ".join(tokens) or normalized
    variants = {
        _to_slug(normalized),
        _to_slug(compact_name),
        _to_slug(compact_name.replace(" and ", " ")),
        _to_slug(compact_name.replace(" and ", " and ")),
        _to_slug(compact_name.replace(" and ", "-and-")),
    }
    if tokens:
        variants.add(_to_slug(tokens[0]))
        variants.add(_to_slug(" ".join(tokens[:2])))
    return [variant for variant in variants if variant]


def _normalize_text(value: str) -> str:
    value = (
        unicodedata.normalize("NFKD", value)
        .encode("ASCII", "ignore")
        .decode("utf-8")
        .lower()
    )
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _to_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-").lower()
