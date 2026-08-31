"""Tracenable climate/ESG data scraper.

Scrapes a company's public Tracenable page (tracenable.com) across 5 tabs:
1. GHG Emissions      — Scope 1/2/3 categories and intensities
2. Climate Targets    — targets, scopes, units, reductions, target years
3. EU Taxonomy        — turnover/opex/capex aligned & eligible %
4. Energy Management  — total/renewable/non-renewable, production
5. Waste Management   — generated/recovered/hazardous/disposed

Works for any public company, not a fixed universe — a name is normalized
into candidate URL slugs (with a manual alias table for names Tracenable
slugs unpredictably), each candidate is fetched, and the first one with
real data wins.

Public API
----------
scrape_single_company(company_name, slug) – scrape one company by slug
ALIASES                                    – name → slug overrides for
                                              companies whose Tracenable slug
                                              doesn't match a naive guess
"""

from __future__ import annotations

import html
import re
import time
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

_TABS = [
    ("ghg_emissions", "ghg-emissions"),
    ("climate_targets", "climate-targets"),
    ("eu_taxonomy", "eu-taxonomy"),
    ("energy_management", "energy-management"),
    ("waste_management", "waste-management"),
]

# Manual name -> slug overrides for companies whose Tracenable slug doesn't
# match a naive normalization of their legal name (abbreviations, merged
# entities, alternate spellings, etc.).
ALIASES: dict[str, str] = {
    "Auto Trader Group PLC": "autotrader-group",
    "FLSmidth & Co A/S": "flsmidth-and-co",
    "Sydbank A/S": "al-sydbank",
    "D'Ieteren Group SA": "dieteren-group",
    "Allegro.eu SA": "allegroeu",
    "L'Oreal SA": "loreal",
    "Chocoladefabriken Lindt & Spruengli AG": "lindt-and-sprungli",
    "P/F Bakkafrost": "pf-bakkafrost",
    "Muenchener Rueckversicherungs-Gesellschaft in Muenchen AG": "munich-re",
    "Banco Bilbao Vizcaya Argentaria SA": "bbva",
    "Powszechna Kasa Oszczednosci Bank Polski SA": "pko-bank-polski",
    "Santander Bank Polska SA": "banco-santander",
    "M&G PLC": "m-and-g",
    "St James's Place PLC": "st-jamess-place",
    "SEB SA": "seb",
    "LVMH Moet Hennessy Louis Vuitton SE": "lvmh",
    "Hermes International SCA": "hermes-international",
    "Industria de Diseno Textil SA": "inditex",
    "Bayerische Motoren Werke AG": "bmw",
    "Compagnie Generale des Etablissements Michelin SCA": "michelin",
    "H & M Hennes & Mauritz AB": "h-and-m",
    "AstraZeneca PLC": "astrazeneca",
    "EssilorLuxottica SA": "essilorluxottica",
    "GSK plc": "gsk",
    "Merck KGaA": "merck",
    "Siemens AG": "siemens",
    "Schneider Electric SE": "schneider-electric",
    "Airbus SE": "airbus",
    "Abb Ltd": "abb",
    "Safran SA": "safran",
    "Vinci SA": "vinci",
    "Compagnie de Saint Gobain SA": "saint-gobain",
    "BAE Systems PLC": "bae-systems",
    "Deutsche Post AG": "dhl-group",
    "AP Moeller - Maersk A/S": "maersk",
    "L'Air Liquide Societe Anonyme pour l'Etude et l'Exploitation des Procedes Georges Claude SA": "air-liquide",
    "Anheuser-Busch Inbev SA": "ab-inbev",
    "British American Tobacco plc": "british-american-tobacco",
    "Reckitt Benckiser Group PLC": "reckitt-benckiser-group",
    "Associated British Foods PLC": "associated-british-foods",
    "Davide Campari Milano NV": "campari-group",
    "J Sainsbury PLC": "sainsburys",
    "Marks and Spencer Group PLC": "marks-and-spencer",
    "TotalEnergies SE": "totalenergies",
    "London Stock Exchange Group PLC": "london-stock-exchange-group",
    "Intesa Sanpaolo SpA": "intesa-sanpaolo",
    "Banco Santander SA": "banco-santander",
    "BNP Paribas SA": "bnp-paribas",
    "UniCredit SpA": "unicredit",
    "ING Groep NV": "ing-groep",
    "Barclays PLC": "barclays",
    "Assicurazioni Generali SpA": "generali",
    "Credit Agricole SA": "credit-agricole",
    "Deutsche Bank AG": "deutsche-bank",
    "Societe Generale SA": "societe-generale",
    "Poste Italiane SpA": "poste-italiane",
    "Legal & General Group PLC": "legal-and-general-group",
    "Aviva PLC": "aviva",
    "Amundi SA": "amundi",
    "ASML Holding NV": "asml-holding",
    "Dassault Systemes SE": "dassault-systemes",
    "Capgemini SE": "capgemini",
    "Rio Tinto PLC": "rio-tinto",
    "BASF SE": "basf",
    "Holcim AG": "holcim",
    "Heidelberg Materials AG": "heidelberg-materials",
    "ArcelorMittal SA": "arcelormittal",
    "Iberdrola SA": "iberdrola",
    "Enel SpA": "enel",
    "Engie SA": "engie",
    "E.ON SE": "e-on",
    "RWE AG": "rwe",
    "Veolia Environnement SA": "veolia",
}


# ══════════════════════════════════════════════════════════════════════════════
# HTML TABLE PARSER
# ══════════════════════════════════════════════════════════════════════════════


class _TracenableTableParser(HTMLParser):
    """Extracts every <table> on a Tracenable tab page as a list of rows.

    Cells hidden behind Tracenable's paywall (``data-tooltip="Upgrade to
    download this value"``) are rendered as a blurred placeholder with no
    text — those are reported as ``"[Locked / Upgrade Required]"`` so callers
    can distinguish "no data" from "data exists but requires a paid plan".
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._in_cell = False
        self._in_sup = False
        self._in_svg = False
        self._is_blurred = False
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_dict = dict(attrs)
        if tag == "table":
            self._current_table = []
            self.tables.append(self._current_table)
        elif self._current_table is not None:
            if tag == "tr":
                self._current_row = []
                self._current_table.append(self._current_row)
            elif tag in ("th", "td") and self._current_row is not None:
                self._in_cell = True
                self._cell_text = []
                self._is_blurred = False
            elif tag == "sup":
                self._in_sup = True
            elif tag == "svg":
                self._in_svg = True

            if attr_dict.get("data-tooltip") == "Upgrade to download this value":
                self._is_blurred = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            self._current_table = None
        elif self._current_table is not None:
            if tag == "tr":
                self._current_row = None
            elif tag in ("th", "td") and self._current_row is not None:
                clean_val = re.sub(r"\s+", " ", "".join(self._cell_text).strip())
                value = "[Locked / Upgrade Required]" if self._is_blurred and not clean_val else clean_val
                self._current_row.append(html.unescape(value))
                self._in_cell = False
            elif tag == "sup":
                self._in_sup = False
            elif tag == "svg":
                self._in_svg = False

    def handle_data(self, data: str) -> None:
        if self._in_cell and not self._in_sup and not self._in_svg:
            self._cell_text.append(data)


# ══════════════════════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════════════════════


def _fetch_url(url: str, *, max_retries: int = 2) -> str | None:
    for _ in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════


def scrape_single_company(company_name: str, slug: str | None) -> dict[str, Any]:
    """Scrape all 5 Tracenable tabs for one company by its URL slug.

    Returns a dict with a ``header``/``rows`` table per tab (``ghg``,
    ``targets``, ``taxonomy``, ``energy``, ``waste``), or ``status:
    "NO_SLUG"`` if *slug* is falsy — callers are expected to resolve the
    slug themselves (see ``analysis.services.climate_data`` for the name
    -> slug candidate resolution used by the web app).
    """
    result: dict[str, Any] = {
        "company": company_name,
        "slug": slug or "",
        "ghg": None,
        "targets": None,
        "taxonomy": None,
        "energy": None,
        "waste": None,
        "status": "NO_SLUG" if not slug else "OK",
    }
    if not slug:
        return result

    for tab_id, tab_slug in _TABS:
        content = _fetch_url(f"https://tracenable.com/company/{slug}/{tab_slug}")
        if not content:
            continue

        parser = _TracenableTableParser()
        parser.feed(content)
        if not parser.tables:
            continue

        if tab_id == "eu_taxonomy":
            result["taxonomy"] = _consolidate_taxonomy_tables(parser.tables)
        else:
            key = {
                "ghg_emissions": "ghg",
                "climate_targets": "targets",
                "energy_management": "energy",
                "waste_management": "waste",
            }[tab_id]
            table = parser.tables[0]
            header = table[0] if table else []
            rows = [row for row in table[1:] if any(row)]
            result[key] = {"header": header, "rows": rows}

    return result


def _consolidate_taxonomy_tables(tables: list[list[list[str]]]) -> dict[str, Any]:
    """EU Taxonomy renders as 3 separate tables (Turnover/Opex/Capex) — merge
    them into one, tagging each row with its KPI category."""
    kpi_names = ["Turnover", "Opex", "Capex"]
    header: list[str] | None = None
    rows: list[list[str]] = []
    for idx, table in enumerate(tables):
        if not table:
            continue
        if header is None:
            header = ["KPI Category"] + table[0]
        kpi_name = kpi_names[idx] if idx < len(kpi_names) else f"Table {idx + 1}"
        rows.extend([kpi_name] + row for row in table[1:] if any(row))
    return {"header": header, "rows": rows}
