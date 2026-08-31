"""Morningstar API response parsers.

- DataFormatter: raw API values → display strings
- TableParser:   API response dicts → flat, template-ready table structures
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np


class DataFormatter:
    """Converts raw API values into clean, human-readable strings."""

    @staticmethod
    def safe(val: Any) -> str:
        """Format any scalar value, masking nulls and Morningstar placeholders."""
        if val is None or val == "_PO_":
            return ""
        if isinstance(val, np.integer):
            return str(int(val))
        if isinstance(val, np.floating):
            return "" if np.isnan(val) else f"{float(val):,.2f}"
        if isinstance(val, float):
            return "" if np.isnan(val) else f"{val:,.2f}"
        if isinstance(val, int):
            return f"{val:,}"
        return str(val)

    @staticmethod
    def number(val: Any) -> str:
        """Format numeric values with comma separators."""
        if val is None:
            return ""
        if isinstance(val, float) and np.isnan(val):
            return ""
        if isinstance(val, float):
            return f"{val:,.2f}"
        if isinstance(val, int):
            return f"{val:,}"
        return str(val)


class TableParser:
    """Converts Morningstar API response dicts into flat, template-ready structures."""

    def __init__(self, formatter: DataFormatter) -> None:
        self._fmt = formatter

    # ── Public entry points ─────────────────────────────────────────────

    def from_table(self, data: dict) -> dict | None:
        """Parse a Collapsed-rows table (valuation, income, balance, cash, dividends)."""
        if not data:
            return None
        if "Collapsed" in data:
            raw_rows = data["Collapsed"].get("rows", [])
            columns = (
                data["Collapsed"].get("columnDefs")
                or data["Collapsed"].get("columnDefs_labels")
                or []
            )
        elif "rows" in data:
            raw_rows = data.get("rows", [])
            columns = data.get("columnDefs") or data.get("columnDefs_labels") or []
        else:
            return None
        if not raw_rows:
            return None
        clean_columns = self._clean_column_defs(columns)
        flat = self._walk_rows(raw_rows, clean_columns)
        section = self._strip_empty_columns(
            {"columns": clean_columns, "flat_rows": flat}
        )
        return self._sort_latest_first(section)

    def from_datalist(self, data: dict) -> dict | None:
        """Parse a dataList-style response (profitability, growth, health, FCF)."""
        if not data or "dataList" not in data:
            return None
        data_list = data["dataList"]
        if not data_list:
            return None
        columns = self._year_columns(data_list)
        flat = self._datalist_rows(data_list, columns, skip_keys=self._FISCAL_KEYS)
        section = self._strip_empty_columns({"columns": columns, "flat_rows": flat})
        return self._sort_latest_first(section)

    def from_key_metrics(self, data: dict) -> dict | None:
        """Parse the keyMetricsSummary grouped response."""
        if not data:
            return None
        sections = [
            ("Income", data.get("incomeStatementList")),
            ("Balance Sheet", data.get("balanceSheetList")),
            ("Cash Flow", data.get("cashFlowList")),
        ]
        sections = [(lbl, s) for lbl, s in sections if s and s.get("dataList")]
        if not sections:
            return None
        columns = self._year_columns(sections[0][1]["dataList"])
        n_cols = len(columns)
        flat: list[dict] = []
        for section_label, section in sections:
            flat.append({"label": section_label, "depth": 0, "cells": [""] * n_cols})
            for row in self._datalist_rows(
                section["dataList"], columns, skip_keys=self._FISCAL_KEYS, depth=1
            ):
                label = row["label"]
                for old, new in self._KEY_METRIC_REPLACEMENTS:
                    label = label.replace(old, new)
                label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label).title()
                flat.append({**row, "label": label})
        section = self._strip_empty_columns({"columns": columns, "flat_rows": flat})
        return self._sort_latest_first(section)

    # ── Private helpers ─────────────────────────────────────────────────

    _FISCAL_KEYS = frozenset(
        {
            "fiscalPeriodYear",
            "fiscalPeriodYearMonth",
            "fiscalPeriodDate",
            "morningstarEndingDate",
        }
    )

    _KEY_METRIC_REPLACEMENTS = [
        ("Per", " %"),
        ("revenueGrowth %", "Revenue Growth %"),
        ("grossProfitMargin %", "Gross Margin %"),
        ("operatingMargin %", "Operating Margin %"),
        ("ebitMargin %", "EBIT Margin %"),
        ("ebitdaMargin %", "EBITDA Margin %"),
        ("netIncomeMargin %", "Net Inc. Margin %"),
    ]

    @staticmethod
    def _clean_column_defs(columns: list) -> list[str]:
        result = []
        for col in columns:
            c = str(col)
            if c.startswith("tabular.data.label"):
                continue
            if "headers.current" in c:
                result.append("Current")
            elif "headers.oneyearttm" in c or "headers.oneYearTTM" in c:
                result.append("1Y TTM")
            elif "headers.fiveyear" in c or "headers.fiveYear" in c:
                result.append("5Y Avg")
            else:
                result.append(c)
        return result

    @staticmethod
    def _year_columns(data_list: list[dict]) -> list[str]:
        columns = []
        for item in data_list:
            period = str(item.get("fiscalPeriodYearMonth") or "").strip()
            if period.upper() in {"TTM", "CURRENT", "LAST QUARTER"}:
                columns.append(period)
                continue
            year = item.get("fiscalPeriodYear") or period[:4]
            if not year:
                year = str(item.get("fiscalPeriodDate") or "")[:4]
            columns.append(str(year))
        return columns

    def _walk_rows(self, raw_rows: list[dict], columns: list[str]) -> list[dict]:
        n_cols = len(columns)
        flat: list[dict] = []

        def walk(node: dict, depth: int = 0) -> None:
            label = node.get("label", "")
            if "datum" in node:
                cells = [self._fmt.safe(v) for v in node["datum"]]
                cells = (cells + [""] * n_cols)[:n_cols]
                flat.append({"label": label, "depth": depth, "cells": cells})
            children = node.get("subLevel") or []
            if children and isinstance(children, list):
                if "datum" not in node:
                    flat.append(
                        {"label": label, "depth": depth, "cells": [""] * n_cols}
                    )
                for child in children:
                    walk(child, depth + 1)

        for row in raw_rows:
            walk(row, 0)
        return flat

    def _datalist_rows(
        self,
        data_list: list[dict],
        columns: list[str],
        skip_keys: frozenset,
        depth: int = 0,
    ) -> list[dict]:
        n_cols = len(columns)
        flat: list[dict] = []
        sample = data_list[0]
        for key, sample_val in sample.items():
            if key in skip_keys:
                continue
            if isinstance(sample_val, dict):
                for sub_key in sample_val:
                    label = (
                        f"{key} \u2014 {sub_key}".replace("Per", " %")
                        .replace("_", " ")
                        .title()
                    )
                    cells = [
                        self._fmt.number(item.get(key, {}).get(sub_key))
                        for item in data_list
                    ]
                    flat.append(
                        {
                            "label": label,
                            "depth": depth,
                            "cells": (cells + [""] * n_cols)[:n_cols],
                        }
                    )
            elif isinstance(sample_val, (int, float)):
                label = key.replace("_", " ").title()
                cells = [self._fmt.number(item.get(key)) for item in data_list]
                flat.append(
                    {
                        "label": label,
                        "depth": depth,
                        "cells": (cells + [""] * n_cols)[:n_cols],
                    }
                )
        return flat

    @staticmethod
    def _strip_empty_columns(section: dict) -> dict:
        columns = section.get("columns", [])
        flat_rows = section.get("flat_rows", [])
        if not columns or not flat_rows:
            return section
        n = len(columns)
        keep = [
            ci
            for ci in range(n)
            if any(row["cells"][ci] for row in flat_rows if ci < len(row["cells"]))
        ]
        if len(keep) == n or not keep:
            return section
        return {
            "columns": [columns[i] for i in keep],
            "flat_rows": [
                {
                    "label": r["label"],
                    "depth": r["depth"],
                    "cells": [r["cells"][i] for i in keep if i < len(r["cells"])],
                }
                for r in flat_rows
            ],
        }

    @staticmethod
    def _sort_latest_first(section: dict) -> dict:
        columns = section.get("columns", [])
        flat_rows = section.get("flat_rows", [])
        if len(columns) < 2:
            return section

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
            year_match = re.search(r"(?:19|20)\d{2}", label)
            if year_match:
                return (0, -int(year_match.group()), index)
            return (1, special_periods.get(label.upper(), 100), index)

        order = sorted(range(len(columns)), key=sort_key)
        if order == list(range(len(columns))):
            return section

        return {
            **section,
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
                for row in flat_rows
            ],
        }
