"""
Euro Stoxx 600 - Tracenable ESG Data Scraper & Excel Builder
============================================================
Scrapes all 600 companies from eurostoxx600.txt on Tracenable (tracenable.com).
Extracts data preview tables from 5 tabs:
1. GHG Emissions (Scope 1, Scope 2, Scope 3 categories, Intensities)
2. Climate Targets (Targets, Scopes, Units, Reductions, Target Years)
3. EU Taxonomy (Turnover, Opex, Capex - Aligned & Eligible %)
4. Energy Management (Total, Renewable, Non-renewable, Production)
5. Waste Management (Generated, Recovered, Hazardous, Disposed)

Outputs:
- eurostoxx600_tracenable_esg.xlsx (Multi-sheet Excel workbook)
- Consolidated CSVs for each tab
"""

import os
import sys
import re
import json
import time
import html
import csv
import unicodedata
import urllib.request
import urllib.error
import concurrent.futures
from html.parser import HTMLParser
from typing import List, Dict, Any, Optional, Tuple, Set
import zipfile
import xml.sax.saxutils as saxutils

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
}

TABS = [
    ('ghg_emissions', 'ghg-emissions'),
    ('climate_targets', 'climate-targets'),
    ('eu_taxonomy', 'eu-taxonomy'),
    ('energy_management', 'energy-management'),
    ('waste_management', 'waste-management'),
]

# Manual alias mapping for Euro Stoxx 600 companies
ALIASES = {
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


class TracenableTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.current_table = None
        self.current_row = None
        self.current_cell = None
        self.in_sup = False
        self.in_svg = False
        self.is_blurred = False
        self.cell_clean_text = []
        self.cell_footnote = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_dict = dict(attrs)
        if tag == 'table':
            self.current_table = []
            self.tables.append(self.current_table)
        elif self.current_table is not None:
            if tag == 'tr':
                self.current_row = []
                self.current_table.append(self.current_row)
            elif tag in ('th', 'td') and self.current_row is not None:
                self.current_cell = {'tag': tag, 'attrs': attr_dict}
                self.cell_clean_text = []
                self.cell_footnote = []
                self.is_blurred = False
            elif tag == 'sup':
                self.in_sup = True
            elif tag == 'svg':
                self.in_svg = True
            
            if 'data-tooltip' in attr_dict and attr_dict['data-tooltip'] == 'Upgrade to download this value':
                self.is_blurred = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'table':
            self.current_table = None
        elif self.current_table is not None:
            if tag == 'tr':
                self.current_row = None
            elif tag in ('th', 'td') and self.current_row is not None:
                clean_val = "".join(self.cell_clean_text).strip()
                clean_val = re.sub(r'\s+', ' ', clean_val)
                if self.is_blurred and not clean_val:
                    val = "[Locked / Upgrade Required]"
                else:
                    val = clean_val
                self.current_row.append(html.unescape(val))
                self.current_cell = None
            elif tag == 'sup':
                self.in_sup = False
            elif tag == 'svg':
                self.in_svg = False

    def handle_data(self, data):
        if self.current_cell is not None:
            if self.in_sup:
                self.cell_footnote.append(data)
            elif not self.in_svg:
                self.cell_clean_text.append(data)


def fetch_url(url: str, max_retries: int = 2) -> Optional[str]:
    for _ in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return None


def clean_for_matching(text: str) -> Tuple[str, Set[str]]:
    text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8').lower()
    text = re.sub(r'[\'\"’\.\,\&\(\)\/\-\+]', ' ', text)
    legal_words = {
        'ag', 'sa', 'se', 'nv', 'plc', 'spa', 'ab', 'publ', 'as', 'asa', 'oyj',
        'kgaa', 'co', 'holding', 'holdings', 'group', 'groupe', 'corp', 'inc',
        'ltd', 'limited', 'societe', 'anonyme', 'polska', 'sgps', 'socimi', 'reit',
        'abp', 'de', 'et', 'the', 'cie', 'compagnie'
    }
    tokens = [w for w in text.split() if w not in legal_words]
    return " ".join(tokens), set(tokens)


def build_company_mapping(companies: List[str], all_slugs: List[str]) -> Dict[str, Tuple[Optional[str], float, str]]:
    slug_set = set(all_slugs)
    slug_token_map = {}
    for s in all_slugs:
        clean_s, tok_s = clean_for_matching(s.replace('-', ' '))
        slug_token_map[s] = (clean_s, tok_s)

    mapping = {}
    for comp in companies:
        if comp in ALIASES and ALIASES[comp] in slug_set:
            mapping[comp] = (ALIASES[comp], 1.0, 'manual_alias')
            continue
        
        direct_slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', comp).encode('ASCII', 'ignore').decode('utf-8').lower()).strip('-')
        if direct_slug in slug_set:
            mapping[comp] = (direct_slug, 1.0, 'direct_slug')
            continue
            
        clean_c, tokens_c = clean_for_matching(comp)
        clean_slug = "-".join(clean_c.split())
        if clean_slug in slug_set:
            mapping[comp] = (clean_slug, 1.0, 'clean_slug')
            continue

        cand = clean_c.replace(' and ', '-')
        cand_slug = "-".join(cand.split())
        if cand_slug in slug_set:
            mapping[comp] = (cand_slug, 1.0, 'cand_slug')
            continue
            
        best_s = None
        best_score = 0.0
        for s, (clean_s, tok_s) in slug_token_map.items():
            if not tokens_c or not tok_s:
                continue
            inter = tokens_c.intersection(tok_s)
            union = tokens_c.union(tok_s)
            jaccard = len(inter) / len(union) if union else 0
            
            if tokens_c.issubset(tok_s) or tok_s.issubset(tokens_c):
                score = max(jaccard, len(inter) / min(len(tokens_c), len(tok_s)) * 0.9)
            else:
                score = jaccard
                
            if score > best_score:
                best_score = score
                best_s = s
                
        if best_s and best_score >= 0.55:
            mapping[comp] = (best_s, best_score, 'fuzzy')
        else:
            mapping[comp] = (None, 0.0, 'not_found')
            
    return mapping


def scrape_single_company(comp_name: str, slug: Optional[str]) -> Dict[str, Any]:
    res = {
        'company': comp_name,
        'slug': slug or '',
        'ghg': None,
        'targets': None,
        'taxonomy': None,
        'energy': None,
        'waste': None,
        'status': 'NO_SLUG' if not slug else 'OK'
    }
    if not slug:
        return res

    for tab_id, tab_slug in TABS:
        url = f"https://tracenable.com/company/{slug}/{tab_slug}"
        content = fetch_url(url)
        if not content:
            continue
            
        parser = TracenableTableParser()
        parser.feed(content)
        
        if tab_id == 'ghg_emissions' and parser.tables:
            t = parser.tables[0]
            header = t[0] if t else []
            rows = [r for r in t[1:] if any(r)]
            res['ghg'] = {'header': header, 'rows': rows}
            
        elif tab_id == 'climate_targets' and parser.tables:
            t = parser.tables[0]
            header = t[0] if t else []
            rows = [r for r in t[1:] if any(r)]
            res['targets'] = {'header': header, 'rows': rows}
            
        elif tab_id == 'eu_taxonomy' and parser.tables:
            kpis = ['Turnover', 'Opex', 'Capex']
            tax_rows = []
            tax_header = None
            for idx, tbl in enumerate(parser.tables):
                if not tbl: continue
                if tax_header is None:
                    tax_header = ['KPI Category'] + tbl[0]
                kpi_name = kpis[idx] if idx < len(kpis) else f"Table {idx+1}"
                for r in tbl[1:]:
                    if any(r):
                        tax_rows.append([kpi_name] + r)
            res['taxonomy'] = {'header': tax_header, 'rows': tax_rows}
            
        elif tab_id == 'energy_management' and parser.tables:
            t = parser.tables[0]
            header = t[0] if t else []
            rows = [r for r in t[1:] if any(r)]
            res['energy'] = {'header': header, 'rows': rows}
            
        elif tab_id == 'waste_management' and parser.tables:
            t = parser.tables[0]
            header = t[0] if t else []
            rows = [r for r in t[1:] if any(r)]
            res['waste'] = {'header': header, 'rows': rows}

    return res


class SimpleXLSXWriter:
    """Generates multi-sheet Excel .xlsx files with formatting without third-party dependencies."""
    def __init__(self, filename: str):
        self.filename = filename
        self.sheets = []  # list of (name, rows)

    def add_sheet(self, name: str, rows: List[List[Any]]):
        clean_name = saxutils.escape(name[:31])
        self.sheets.append((clean_name, rows))

    def save(self):
        with zipfile.ZipFile(self.filename, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            # 1. [Content_Types].xml
            ct_parts = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                '<Default Extension="xml" ContentType="application/xml"/>',
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
            ]
            for i in range(len(self.sheets)):
                ct_parts.append(f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            ct_parts.append('</Types>')
            z.writestr('[Content_Types].xml', "".join(ct_parts))

            # 2. _rels/.rels
            rels = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>'
            )
            z.writestr('_rels/.rels', rels)

            # 3. xl/_rels/workbook.xml.rels
            wb_rels = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            ]
            for i in range(len(self.sheets)):
                wb_rels.append(f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>')
            wb_rels.append('</Relationships>')
            z.writestr('xl/_rels/workbook.xml.rels', "".join(wb_rels))

            # 4. xl/workbook.xml
            wb = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
                '<sheets>'
            ]
            for i, (name, _) in enumerate(self.sheets):
                wb.append(f'<sheet name="{name}" sheetId="{i+1}" r:id="rId{i+1}"/>')
            wb.append('</sheets></workbook>')
            z.writestr('xl/workbook.xml', "".join(wb))

            # 5. xl/styles.xml
            styles = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<fonts count="2">'
                '<font><sz val="10"/><name val="Segoe UI"/></font>'
                '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Segoe UI"/></font>'
                '</fonts>'
                '<fills count="3">'
                '<fill><patternFill patternType="none"/></fill>'
                '<fill><patternFill patternType="gray125"/></fill>'
                '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/></patternFill></fill>'
                '</fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="2">'
                '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
                '</cellXfs>'
                '</styleSheet>'
            )
            z.writestr('xl/styles.xml', styles)

            # 6. xl/worksheets/sheetN.xml
            for i, (_, rows) in enumerate(self.sheets):
                ws = [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                    '<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>',
                    '<sheetFormatPr defaultRowHeight="15"/>',
                    '<sheetData>'
                ]
                for r_idx, row in enumerate(rows, start=1):
                    s_attr = ' s="1"' if r_idx == 1 else ''
                    ws.append(f'<row r="{r_idx}">')
                    for c_idx, val in enumerate(row, start=1):
                        col_letter = ""
                        n = c_idx
                        while n > 0:
                            n, rem = divmod(n - 1, 26)
                            col_letter = chr(65 + rem) + col_letter
                        cell_ref = f"{col_letter}{r_idx}"
                        val_str = saxutils.escape(str(val) if val is not None else "")
                        ws.append(f'<c r="{cell_ref}" t="inlineStr"{s_attr}><is><t>{val_str}</t></is></c>')
                    ws.append('</row>')
                ws.append('</sheetData></worksheet>')
                z.writestr(f'xl/worksheets/sheet{i+1}.xml', "".join(ws))


def consolidate_results(results: List[Dict[str, Any]], company_mapping: Dict[str, Any]):
    """Consolidates scraped results into structured tables for Excel & CSV."""
    
    # 1. Summary Sheet
    summary_rows = [["Company Name", "Tracenable Slug", "Match Method", "Match Score", "GHG Rows", "Climate Targets Rows", "Taxonomy Rows", "Energy Rows", "Waste Rows", "Status"]]
    
    # 2. GHG Emissions
    # Harmonize headers (e.g. 2025, 2024, 2023, 2022-2019)
    ghg_year_cols = []
    for r in results:
        if r['ghg'] and r['ghg'].get('header'):
            for h in r['ghg']['header'][1:]:
                if h not in ghg_year_cols and h:
                    ghg_year_cols.append(h)
    
    ghg_header = ["Company Name", "Tracenable Slug", "Metric (tCO2e)"] + ghg_year_cols
    ghg_rows = [ghg_header]
    
    # 3. Climate Targets
    targets_header = ["Company Name", "Tracenable Slug", "Target Type", "Scope of Target", "Unit", "Target", "Target Year"]
    targets_rows = [targets_header]
    
    # 4. EU Taxonomy
    tax_year_cols = []
    for r in results:
        if r['taxonomy'] and r['taxonomy'].get('header'):
            for h in r['taxonomy']['header'][2:]:
                if h not in tax_year_cols and h:
                    tax_year_cols.append(h)
                    
    tax_header = ["Company Name", "Tracenable Slug", "KPI Category", "Metric"] + tax_year_cols
    tax_rows = [tax_header]
    
    # 5. Energy Management
    energy_year_cols = []
    for r in results:
        if r['energy'] and r['energy'].get('header'):
            for h in r['energy']['header'][1:]:
                if h not in energy_year_cols and h:
                    energy_year_cols.append(h)
                    
    energy_header = ["Company Name", "Tracenable Slug", "Metric (GJ)"] + energy_year_cols
    energy_rows = [energy_header]
    
    # 6. Waste Management
    waste_year_cols = []
    for r in results:
        if r['waste'] and r['waste'].get('header'):
            for h in r['waste']['header'][1:]:
                if h not in waste_year_cols and h:
                    waste_year_cols.append(h)
                    
    waste_header = ["Company Name", "Tracenable Slug", "Metric (tonnes)"] + waste_year_cols
    waste_rows = [waste_header]

    # Populate rows
    for r in results:
        c_name = r['company']
        slug = r['slug']
        map_info = company_mapping.get(c_name, (None, 0.0, 'not_found'))
        
        ghg_cnt = len(r['ghg']['rows']) if r['ghg'] else 0
        tgt_cnt = len(r['targets']['rows']) if r['targets'] else 0
        tax_cnt = len(r['taxonomy']['rows']) if r['taxonomy'] else 0
        ene_cnt = len(r['energy']['rows']) if r['energy'] else 0
        wst_cnt = len(r['waste']['rows']) if r['waste'] else 0
        
        status = "Data Extracted" if (ghg_cnt + tgt_cnt + tax_cnt + ene_cnt + wst_cnt) > 0 else ("No Slug" if not slug else "No Data on Tracenable")
        summary_rows.append([c_name, slug, map_info[2], f"{map_info[1]:.2f}", ghg_cnt, tgt_cnt, tax_cnt, ene_cnt, wst_cnt, status])

        # GHG
        if r['ghg'] and r['ghg'].get('rows'):
            cur_h = r['ghg']['header'][1:]
            for row in r['ghg']['rows']:
                metric = row[0]
                vals = row[1:]
                row_dict = dict(zip(cur_h, vals))
                aligned_vals = [row_dict.get(y, "") for y in ghg_year_cols]
                ghg_rows.append([c_name, slug, metric] + aligned_vals)

        # Targets
        if r['targets'] and r['targets'].get('rows'):
            for row in r['targets']['rows']:
                # Pad to 5 columns
                padded = row + [""] * (5 - len(row))
                targets_rows.append([c_name, slug] + padded[:5])

        # Taxonomy
        if r['taxonomy'] and r['taxonomy'].get('rows'):
            cur_h = r['taxonomy']['header'][2:]
            for row in r['taxonomy']['rows']:
                kpi_cat = row[0]
                metric = row[1] if len(row) > 1 else ""
                vals = row[2:] if len(row) > 2 else []
                row_dict = dict(zip(cur_h, vals))
                aligned_vals = [row_dict.get(y, "") for y in tax_year_cols]
                tax_rows.append([c_name, slug, kpi_cat, metric] + aligned_vals)

        # Energy
        if r['energy'] and r['energy'].get('rows'):
            cur_h = r['energy']['header'][1:]
            for row in r['energy']['rows']:
                metric = row[0]
                vals = row[1:]
                row_dict = dict(zip(cur_h, vals))
                aligned_vals = [row_dict.get(y, "") for y in energy_year_cols]
                energy_rows.append([c_name, slug, metric] + aligned_vals)

        # Waste
        if r['waste'] and r['waste'].get('rows'):
            cur_h = r['waste']['header'][1:]
            for row in r['waste']['rows']:
                metric = row[0]
                vals = row[1:]
                row_dict = dict(zip(cur_h, vals))
                aligned_vals = [row_dict.get(y, "") for y in waste_year_cols]
                waste_rows.append([c_name, slug, metric] + aligned_vals)

    return {
        'Summary': summary_rows,
        'GHG_Emissions': ghg_rows,
        'Climate_Targets': targets_rows,
        'EU_Taxonomy': tax_rows,
        'Energy_Management': energy_rows,
        'Waste_Management': waste_rows,
    }


def save_csv(filename: str, rows: List[List[Any]]):
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerows(rows)


def main():
    print("==================================================================")
    print(" EURO STOXX 600 - TRACENABLE ESG DATA SCRAPER & EXCEL EXPORTER   ")
    print("==================================================================")
    
    # 1. Load companies
    with open('eurostoxx600.txt', 'r', encoding='utf-8') as f:
        line = f.read().strip()
        companies = [c.strip() for c in line.split('\t') if c.strip()]
    print(f"Loaded {len(companies)} companies from eurostoxx600.txt")

    # 2. Load slugs
    with open('tracenable_all_slugs.json', 'r', encoding='utf-8') as f:
        all_slugs = json.load(f)
    print(f"Loaded {len(all_slugs)} slugs from Tracenable sitemap")

    # 3. Match companies
    mapping = build_company_mapping(companies, all_slugs)
    mapped_count = sum(1 for v in mapping.values() if v[0] is not None)
    print(f"Mapped {mapped_count} / {len(companies)} companies ({mapped_count/len(companies)*100:.1f}%)")

    # 4. Scrape all companies with ThreadPoolExecutor
    print(f"\nStarting concurrent extraction (workers: 12)...")
    results = []
    completed = 0
    total = len(companies)
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        future_to_comp = {
            executor.submit(scrape_single_company, comp, mapping[comp][0]): comp
            for comp in companies
        }
        for future in concurrent.futures.as_completed(future_to_comp):
            res = future.result()
            results.append(res)
            completed += 1
            if completed % 25 == 0 or completed == total:
                elapsed = time.time() - start_time
                rate = completed / elapsed
                rem_time = (total - completed) / rate if rate > 0 else 0
                print(f"  [{completed:3d}/{total}] ({completed/total*100:5.1f}%) - Elapsed: {elapsed:.1f}s - Est. Remaining: {rem_time:.1f}s")

    # Sort results by original company order in eurostoxx600.txt
    comp_order = {c: idx for idx, c in enumerate(companies)}
    results.sort(key=lambda r: comp_order.get(r['company'], 9999))

    # 5. Consolidate tables
    print("\nConsolidating tables across all tabs...")
    sheets_data = consolidate_results(results, mapping)

    # 6. Save Excel file
    excel_path = "eurostoxx600_tracenable_esg.xlsx"
    print(f"Writing Excel workbook to {excel_path}...")
    xlsx_writer = SimpleXLSXWriter(excel_path)
    for sheet_name, rows in sheets_data.items():
        print(f"  - Sheet '{sheet_name}': {len(rows)} rows")
        xlsx_writer.add_sheet(sheet_name, rows)
    xlsx_writer.save()
    print(f"Successfully saved {excel_path}!")

    # 7. Save individual CSVs
    print("\nWriting individual CSV files...")
    csv_mappings = {
        'Summary': 'eurostoxx600_companies_summary.csv',
        'GHG_Emissions': 'eurostoxx600_ghg_emissions.csv',
        'Climate_Targets': 'eurostoxx600_climate_targets.csv',
        'EU_Taxonomy': 'eurostoxx600_eu_taxonomy.csv',
        'Energy_Management': 'eurostoxx600_energy_management.csv',
        'Waste_Management': 'eurostoxx600_waste_management.csv',
    }
    for sheet_name, csv_filename in csv_mappings.items():
        save_csv(csv_filename, sheets_data[sheet_name])
        print(f"  - Saved {csv_filename} ({len(sheets_data[sheet_name])} rows)")

    total_time = time.time() - start_time
    print(f"\n==================================================================")
    print(f" COMPLETE! Processed {len(companies)} companies in {total_time:.1f}s ({total_time/60:.2f} min)")
    print(f" Output Excel: {os.path.abspath(excel_path)}")
    print(f"==================================================================")


if __name__ == "__main__":
    main()
