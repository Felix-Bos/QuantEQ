"""Yahoo Finance client for market data."""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)


def _safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _safe_std(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else None


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _return_for_days(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or closes[-days - 1] == 0:
        return None
    return closes[-1] / closes[-days - 1] - 1


def _moving_average(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        result.append(running / window if index >= window - 1 else None)
    return result


def _rolling_volatility(
    returns: list[float | None],
    window: int = 30,
) -> list[float | None]:
    result: list[float | None] = [None]
    clean_returns = [value for value in returns[1:] if value is not None]
    for index in range(len(clean_returns)):
        if index < window - 1:
            result.append(None)
            continue
        sample = clean_returns[index - window + 1 : index + 1]
        deviation = _safe_std(sample)
        result.append(deviation * math.sqrt(252) if deviation is not None else None)
    return result


def _rsi(values: list[float], window: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= window:
        return result
    gains = []
    losses = []
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
        if index < window:
            continue
        avg_gain = statistics.fmean(gains[index - window : index])
        avg_loss = statistics.fmean(losses[index - window : index])
        if avg_loss == 0:
            result[index] = 100.0
        else:
            relative_strength = avg_gain / avg_loss
            result[index] = 100 - (100 / (1 + relative_strength))
    return result


def _atr(history: list[dict], window: int = 14) -> list[float | None]:
    true_ranges: list[float] = []
    result: list[float | None] = []
    previous_close = None
    for point in history:
        high = point.get("high")
        low = point.get("low")
        close = point.get("close")
        if high is None or low is None or close is None:
            true_range = 0.0
        elif previous_close is None:
            true_range = high - low
        else:
            true_range = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        true_ranges.append(true_range)
        result.append(
            statistics.fmean(true_ranges[-window:])
            if len(true_ranges) >= window
            else None
        )
        previous_close = close
    return result


def _build_quantitative_analysis(
    history: list[dict],
    *,
    ticker: str,
    currency: str = "",
    benchmark_ticker: str = "",
    benchmark_history: list[dict] | None = None,
    risk_free_rate: float = 0.02,
) -> dict | None:
    clean = [
        point
        for point in history
        if point.get("date") and point.get("close") not in (None, 0)
    ]
    if len(clean) < 3:
        return None

    dates = [point["date"] for point in clean]
    closes = [float(point["close"]) for point in clean]
    volumes = [int(point.get("volume") or 0) for point in clean]
    daily_returns: list[float | None] = [None]
    for index in range(1, len(closes)):
        daily_returns.append(closes[index] / closes[index - 1] - 1)
    returns = [value for value in daily_returns if value is not None]

    running_peak = closes[0]
    drawdowns: list[float] = []
    max_drawdown = 0.0
    peak_index = trough_index = 0
    current_peak_index = 0
    for index, close in enumerate(closes):
        if close > running_peak:
            running_peak = close
            current_peak_index = index
        drawdown = close / running_peak - 1
        drawdowns.append(drawdown)
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            peak_index = current_peak_index
            trough_index = index

    recovery_date = ""
    peak_value = closes[peak_index]
    for index in range(trough_index + 1, len(closes)):
        if closes[index] >= peak_value:
            recovery_date = dates[index]
            break

    mean_daily = _safe_mean(returns)
    std_daily = _safe_std(returns)
    annual_return = (
        (closes[-1] / closes[0]) ** (365.25 / max(
            1,
            (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days,
        )) - 1
        if closes[0]
        else None
    )
    annual_volatility = std_daily * math.sqrt(252) if std_daily is not None else None
    downside_returns = [value for value in returns if value < 0]
    downside_std = _safe_std(downside_returns)
    downside_volatility = (
        downside_std * math.sqrt(252) if downside_std is not None else None
    )
    sharpe = (
        (annual_return - risk_free_rate) / annual_volatility
        if annual_return is not None and annual_volatility
        else None
    )
    sortino = (
        (annual_return - risk_free_rate) / downside_volatility
        if annual_return is not None and downside_volatility
        else None
    )
    calmar = (
        annual_return / abs(max_drawdown)
        if annual_return is not None and max_drawdown
        else None
    )

    var_95 = _percentile(returns, 0.05)
    var_99 = _percentile(returns, 0.01)
    cvar_95_values = [value for value in returns if var_95 is not None and value <= var_95]
    cvar_99_values = [value for value in returns if var_99 is not None and value <= var_99]
    mean_return = mean_daily or 0.0
    if std_daily:
        skewness = statistics.fmean(
            ((value - mean_return) / std_daily) ** 3 for value in returns
        )
        kurtosis = statistics.fmean(
            ((value - mean_return) / std_daily) ** 4 for value in returns
        ) - 3
    else:
        skewness = kurtosis = None

    autocorrelation = None
    if len(returns) > 2:
        left = returns[:-1]
        right = returns[1:]
        left_mean = statistics.fmean(left)
        right_mean = statistics.fmean(right)
        numerator = sum(
            (a - left_mean) * (b - right_mean)
            for a, b in zip(left, right)
        )
        denominator = math.sqrt(
            sum((a - left_mean) ** 2 for a in left)
            * sum((b - right_mean) ** 2 for b in right)
        )
        autocorrelation = numerator / denominator if denominator else None

    ma20 = _moving_average(closes, 20)
    ma50 = _moving_average(closes, 50)
    ma200 = _moving_average(closes, 200)
    rolling_vol = _rolling_volatility(daily_returns, 30)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(clean, 14)
    high_52 = max(closes[-252:])
    low_52 = min(closes[-252:])
    current = closes[-1]

    benchmark_base_by_date: dict[str, float] = {}
    relative: dict[str, Any] = {
        "benchmarkTicker": benchmark_ticker,
        "beta": None,
        "alpha": None,
        "correlation": None,
        "trackingError": None,
        "informationRatio": None,
        "assetReturn1Y": _return_for_days(closes, 252),
        "benchmarkReturn1Y": None,
        "outperformance1Y": None,
    }
    if benchmark_history:
        benchmark_close_by_date = {
            point["date"]: float(point["close"])
            for point in benchmark_history
            if point.get("date") and point.get("close") not in (None, 0)
        }
        aligned_asset: list[float] = []
        aligned_benchmark: list[float] = []
        previous_asset = previous_benchmark = None
        first_benchmark = None
        for date, close in zip(dates, closes):
            benchmark_close = benchmark_close_by_date.get(date)
            if benchmark_close is None:
                continue
            if first_benchmark is None:
                first_benchmark = benchmark_close
            benchmark_base_by_date[date] = benchmark_close / first_benchmark * 100
            if previous_asset is not None and previous_benchmark is not None:
                aligned_asset.append(close / previous_asset - 1)
                aligned_benchmark.append(benchmark_close / previous_benchmark - 1)
            previous_asset = close
            previous_benchmark = benchmark_close

        if len(aligned_asset) > 2:
            asset_mean = statistics.fmean(aligned_asset)
            benchmark_mean = statistics.fmean(aligned_benchmark)
            covariance = sum(
                (asset_value - asset_mean) * (benchmark_value - benchmark_mean)
                for asset_value, benchmark_value in zip(
                    aligned_asset,
                    aligned_benchmark,
                )
            ) / (len(aligned_asset) - 1)
            benchmark_variance = statistics.variance(aligned_benchmark)
            beta = covariance / benchmark_variance if benchmark_variance else None
            asset_std = _safe_std(aligned_asset)
            benchmark_std = _safe_std(aligned_benchmark)
            correlation = (
                covariance / (asset_std * benchmark_std)
                if asset_std and benchmark_std
                else None
            )
            excess = [
                asset_value - benchmark_value
                for asset_value, benchmark_value in zip(
                    aligned_asset,
                    aligned_benchmark,
                )
            ]
            tracking_error_daily = _safe_std(excess)
            tracking_error = (
                tracking_error_daily * math.sqrt(252)
                if tracking_error_daily is not None
                else None
            )
            annualized_excess = statistics.fmean(excess) * 252
            relative.update(
                {
                    "beta": beta,
                    "alpha": (
                        (asset_mean - beta * benchmark_mean) * 252
                        if beta is not None
                        else None
                    ),
                    "correlation": correlation,
                    "trackingError": tracking_error,
                    "informationRatio": (
                        annualized_excess / tracking_error
                        if tracking_error
                        else None
                    ),
                }
            )

            benchmark_closes = [
                benchmark_close_by_date[date]
                for date in dates[-252:]
                if date in benchmark_close_by_date
            ]
            if len(benchmark_closes) > 1:
                benchmark_return_1y = (
                    benchmark_closes[-1] / benchmark_closes[0] - 1
                )
                relative["benchmarkReturn1Y"] = benchmark_return_1y
                if relative["assetReturn1Y"] is not None:
                    relative["outperformance1Y"] = (
                        relative["assetReturn1Y"] - benchmark_return_1y
                    )

    year = dates[-1][:4]
    ytd_index = next(
        (index for index, date in enumerate(dates) if date.startswith(year)),
        len(dates) - 1,
    )
    ytd_return = (
        closes[-1] / closes[ytd_index] - 1
        if closes[ytd_index]
        else None
    )
    average_volume_20 = _safe_mean([float(value) for value in volumes[-20:]])
    relative_volume = (
        volumes[-1] / average_volume_20
        if average_volume_20
        else None
    )

    series = []
    base_close = closes[0]
    for index, point in enumerate(clean):
        series.append(
            {
                "date": dates[index],
                "close": closes[index],
                "volume": volumes[index],
                "ma20": ma20[index],
                "ma50": ma50[index],
                "ma200": ma200[index],
                "drawdown": drawdowns[index],
                "rollingVol30": rolling_vol[index],
                "rsi14": rsi14[index],
                "atr14": atr14[index],
                "base100": closes[index] / base_close * 100,
                "benchmarkBase100": benchmark_base_by_date.get(dates[index]),
            }
        )

    return {
        "source": "Yahoo Finance",
        "ticker": ticker,
        "currency": currency,
        "benchmarkTicker": benchmark_ticker,
        "asOfDate": dates[-1],
        "startDate": dates[0],
        "observations": len(clean),
        "riskFreeRate": risk_free_rate,
        "performance": {
            "oneDay": _return_for_days(closes, 1),
            "oneMonth": _return_for_days(closes, 21),
            "threeMonths": _return_for_days(closes, 63),
            "sixMonths": _return_for_days(closes, 126),
            "ytd": ytd_return,
            "oneYear": _return_for_days(closes, 252),
            "threeYears": _return_for_days(closes, 756),
            "fiveYears": _return_for_days(closes, 1260),
            "cagr": annual_return,
        },
        "risk": {
            "annualizedReturn": annual_return,
            "annualizedVolatility": annual_volatility,
            "downsideVolatility": downside_volatility,
            "sharpeRatio": sharpe,
            "sortinoRatio": sortino,
            "calmarRatio": calmar,
            "maxDrawdown": max_drawdown,
            "maxDrawdownStart": dates[peak_index],
            "maxDrawdownTrough": dates[trough_index],
            "maxDrawdownRecovery": recovery_date,
            "currentDrawdown": drawdowns[-1],
            "var95": var_95,
            "cvar95": _safe_mean(cvar_95_values),
            "var99": var_99,
            "cvar99": _safe_mean(cvar_99_values),
            "bestDay": max(returns),
            "worstDay": min(returns),
            "positiveDays": sum(value > 0 for value in returns) / len(returns),
            "skewness": skewness,
            "excessKurtosis": kurtosis,
            "autocorrelation1D": autocorrelation,
        },
        "technical": {
            "lastPrice": current,
            "high52Week": high_52,
            "low52Week": low_52,
            "distanceToHigh52Week": current / high_52 - 1 if high_52 else None,
            "distanceToLow52Week": current / low_52 - 1 if low_52 else None,
            "ma20": ma20[-1],
            "ma50": ma50[-1],
            "ma200": ma200[-1],
            "distanceToMa20": current / ma20[-1] - 1 if ma20[-1] else None,
            "distanceToMa50": current / ma50[-1] - 1 if ma50[-1] else None,
            "distanceToMa200": current / ma200[-1] - 1 if ma200[-1] else None,
            "rsi14": rsi14[-1],
            "atr14": atr14[-1],
            "atr14Percent": atr14[-1] / current if atr14[-1] else None,
            "trend": (
                "Bullish"
                if ma50[-1] and ma200[-1] and current > ma50[-1] > ma200[-1]
                else "Bearish"
                if ma50[-1] and ma200[-1] and current < ma50[-1] < ma200[-1]
                else "Neutral"
            ),
        },
        "liquidity": {
            "latestVolume": volumes[-1],
            "averageVolume20": average_volume_20,
            "relativeVolume": relative_volume,
        },
        "relative": relative,
        "series": series,
    }


# ── Financial statement label maps ────────────────────────────────────────────
# Maps yfinance DataFrame row index names → (human label, category)

_BS_LABEL_MAP: dict[str, tuple[str, str]] = {
    # ── Assets ──────────────────────────────────────────
    "TotalAssets":                        ("Total Assets",                     "ASSETS"),
    "CurrentAssets":                      ("Total Current Assets",             "CURRENT ASSETS"),
    "CashAndCashEquivalents":             ("Cash & Cash Equivalents",          "CURRENT ASSETS"),
    "CashCashEquivalentsAndShortTermInvestments": ("Cash & Short-Term Investments", "CURRENT ASSETS"),
    "OtherShortTermInvestments":          ("Other Short-Term Investments",     "CURRENT ASSETS"),
    "Receivables":                        ("Receivables",                      "CURRENT ASSETS"),
    "AccountsReceivable":                 ("Accounts Receivable",              "CURRENT ASSETS"),
    "GrossAccountsReceivable":            ("Gross Accounts Receivable",        "CURRENT ASSETS"),
    "AllowanceForDoubtfulAccountsReceivable": ("Allowance for Doubtful Accts","CURRENT ASSETS"),
    "Inventory":                          ("Inventory",                        "CURRENT ASSETS"),
    "CurrentPrepaidAssets":               ("Prepaid Assets",                   "CURRENT ASSETS"),
    "OtherCurrentAssets":                 ("Other Current Assets",             "CURRENT ASSETS"),
    "CurrentDeferredAssets":              ("Deferred Tax Assets (current)",    "CURRENT ASSETS"),
    "CurrentDeferredTaxesAsset":          ("Deferred Tax Assets (current)",    "CURRENT ASSETS"),
    "HedgingAssetsCurrent":               ("Hedging Assets (current)",         "CURRENT ASSETS"),
    "AssetsHeldForSaleCurrent":           ("Assets Held For Sale",             "CURRENT ASSETS"),
    "NonCurrentAssets":                   ("Total Non-Current Assets",         "NON-CURRENT ASSETS"),
    "NetPPE":                             ("Net PP&E",                         "NON-CURRENT ASSETS"),
    "GoodwillAndOtherIntangibleAssets":   ("Goodwill & Other Intangibles",     "NON-CURRENT ASSETS"),
    "Goodwill":                           ("Goodwill",                         "NON-CURRENT ASSETS"),
    "OtherIntangibleAssets":              ("Other Intangible Assets",          "NON-CURRENT ASSETS"),
    "InvestmentsAndAdvances":             ("Investments & Advances",           "NON-CURRENT ASSETS"),
    "LongTermEquityInvestment":           ("Long-Term Equity Investments",     "NON-CURRENT ASSETS"),
    "NonCurrentDeferredAssets":           ("Deferred Tax Assets (non-current)","NON-CURRENT ASSETS"),
    "NonCurrentDeferredTaxesAsset":       ("Deferred Tax Assets (non-current)","NON-CURRENT ASSETS"),
    "FinancialAssets":                    ("Financial Assets",                 "NON-CURRENT ASSETS"),
    "OtherNonCurrentAssets":              ("Other Non-Current Assets",         "NON-CURRENT ASSETS"),
    "DefinedPensionBenefit":              ("Pension Asset",                    "NON-CURRENT ASSETS"),
    # ── Liabilities ──────────────────────────────────────
    "TotalLiabilitiesNetMinorityInterest":("Total Liabilities",                "LIABILITIES"),
    "CurrentLiabilities":                 ("Total Current Liabilities",        "CURRENT LIABILITIES"),
    "PayablesAndAccruedExpenses":         ("Payables & Accrued Expenses",      "CURRENT LIABILITIES"),
    "AccountsPayable":                    ("Accounts Payable",                 "CURRENT LIABILITIES"),
    "CurrentAccruedExpenses":             ("Accrued Expenses",                 "CURRENT LIABILITIES"),
    "OtherPayable":                       ("Other Payables",                   "CURRENT LIABILITIES"),
    "CurrentDebt":                        ("Current Portion of Debt",          "CURRENT LIABILITIES"),
    "CurrentDebtAndCapitalLeaseObligation": ("Current Debt & Leases",         "CURRENT LIABILITIES"),
    "CommercialPaper":                    ("Commercial Paper",                 "CURRENT LIABILITIES"),
    "CurrentDeferredLiabilities":         ("Deferred Revenue (current)",       "CURRENT LIABILITIES"),
    "CurrentDeferredRevenue":             ("Deferred Revenue (current)",       "CURRENT LIABILITIES"),
    "OtherCurrentLiabilities":            ("Other Current Liabilities",        "CURRENT LIABILITIES"),
    "TaxesPayable":                       ("Taxes Payable",                    "CURRENT LIABILITIES"),
    "NonCurrentLiabilities":              ("Total Non-Current Liabilities",    "NON-CURRENT LIABILITIES"),
    "LongTermDebt":                       ("Long-Term Debt",                   "NON-CURRENT LIABILITIES"),
    "LongTermDebtAndCapitalLeaseObligation": ("Long-Term Debt & Leases",      "NON-CURRENT LIABILITIES"),
    "LongTermCapitalLeaseObligation":     ("Capital Lease Obligations (LT)",   "NON-CURRENT LIABILITIES"),
    "NonCurrentDeferredLiabilities":      ("Deferred Revenue (non-current)",   "NON-CURRENT LIABILITIES"),
    "NonCurrentDeferredRevenue":          ("Deferred Revenue (non-current)",   "NON-CURRENT LIABILITIES"),
    "NonCurrentDeferredTaxesLiabilities": ("Deferred Tax Liabilities (LT)",   "NON-CURRENT LIABILITIES"),
    "OtherNonCurrentLiabilities":         ("Other Non-Current Liabilities",    "NON-CURRENT LIABILITIES"),
    "TradeAndOtherPayablesNonCurrent":    ("Non-Current Payables",             "NON-CURRENT LIABILITIES"),
    "EmployeeBenefits":                   ("Employee Benefits (LT)",           "NON-CURRENT LIABILITIES"),
    # ── Equity ───────────────────────────────────────────
    "StockholdersEquity":                 ("Total Shareholders' Equity",       "EQUITY"),
    "TotalEquityGrossMinorityInterest":   ("Total Equity (incl. Minority)",    "EQUITY"),
    "CommonStock":                        ("Common Stock",                     "EQUITY"),
    "AdditionalPaidInCapital":            ("Additional Paid-In Capital",       "EQUITY"),
    "RetainedEarnings":                   ("Retained Earnings",                "EQUITY"),
    "TreasuryStock":                      ("Treasury Stock",                   "EQUITY"),
    "AccumulatedOtherComprehensiveIncome":("Accumulated OCI",                  "EQUITY"),
    "OtherEquityInterest":                ("Other Equity",                     "EQUITY"),
    "MinorityInterest":                   ("Minority / Non-Controlling Interest","EQUITY"),
    "TotalCapitalization":                ("Total Capitalization",             "EQUITY"),
}

_IS_LABEL_MAP: dict[str, tuple[str, str]] = {
    "TotalRevenue":                       ("Total Revenue",                    "REVENUE"),
    "OperatingRevenue":                   ("Operating Revenue",                "REVENUE"),
    "CostOfRevenue":                      ("Cost of Revenue",                  "COST & GROSS PROFIT"),
    "GrossProfit":                        ("Gross Profit",                     "COST & GROSS PROFIT"),
    "OperatingExpense":                   ("Total Operating Expenses",         "OPERATING EXPENSES"),
    "SellingGeneralAndAdministration":    ("SG&A",                             "OPERATING EXPENSES"),
    "GeneralAndAdministrativeExpense":    ("G&A",                              "OPERATING EXPENSES"),
    "SellingAndMarketingExpense":         ("Sales & Marketing",                "OPERATING EXPENSES"),
    "ResearchAndDevelopment":             ("R&D",                              "OPERATING EXPENSES"),
    "OtherOperatingExpenses":             ("Other Operating Expenses",         "OPERATING EXPENSES"),
    "RestructuringAndMergeCosts":         ("Restructuring Costs",              "OPERATING EXPENSES"),
    "OtherIncomeExpense":                 ("Other Income / Expense",           "OPERATING EXPENSES"),
    "OperatingIncome":                    ("Operating Income (EBIT)",          "OPERATING INCOME"),
    "EBIT":                               ("EBIT",                             "OPERATING INCOME"),
    "EBITDA":                             ("EBITDA",                           "OPERATING INCOME"),
    "ReconciledDepreciation":             ("Depreciation & Amortization",      "OPERATING INCOME"),
    "DepreciationAndAmortization":        ("Depreciation & Amortization",      "OPERATING INCOME"),
    "Amortization":                       ("Amortization",                     "OPERATING INCOME"),
    "InterestExpense":                    ("Interest Expense",                 "NON-OPERATING"),
    "InterestExpenseNonOperating":        ("Interest Expense",                 "NON-OPERATING"),
    "InterestIncome":                     ("Interest Income",                  "NON-OPERATING"),
    "InterestIncomeNonOperating":         ("Interest Income",                  "NON-OPERATING"),
    "NetNonOperatingInterestIncomeExpense": ("Net Non-Op. Interest",           "NON-OPERATING"),
    "OtherNonOperatingIncomeExpenses":    ("Other Non-Operating",              "NON-OPERATING"),
    "TotalOtherFinanceCost":              ("Total Other Finance Cost",         "NON-OPERATING"),
    "PretaxIncome":                       ("Pretax Income",                    "NET INCOME"),
    "TaxProvision":                       ("Income Tax Provision",             "NET INCOME"),
    "IncomeTaxExpense":                   ("Income Tax Expense",               "NET INCOME"),
    "NetIncome":                          ("Net Income",                       "NET INCOME"),
    "NetIncomeCommonStockholders":        ("Net Income to Common Shareholders","NET INCOME"),
    "NetIncomeIncludingNoncontrollingInterests": ("Net Income (incl. NCI)",    "NET INCOME"),
    "TotalUnusualItems":                  ("Unusual Items",                    "NET INCOME"),
    "NormalizedIncome":                   ("Normalized Net Income",            "NET INCOME"),
    "BasicEPS":                           ("Basic EPS",                        "EARNINGS PER SHARE"),
    "DilutedEPS":                         ("Diluted EPS",                      "EARNINGS PER SHARE"),
    "BasicAverageShares":                 ("Basic Shares Outstanding",         "EARNINGS PER SHARE"),
    "DilutedAverageShares":               ("Diluted Shares Outstanding",       "EARNINGS PER SHARE"),
}

_CF_LABEL_MAP: dict[str, tuple[str, str]] = {
    "OperatingCashFlow":                  ("Operating Cash Flow",              "OPERATING ACTIVITIES"),
    "NetIncome":                          ("Net Income",                       "OPERATING ACTIVITIES"),
    "DepreciationAndAmortization":        ("Depreciation & Amortization",      "OPERATING ACTIVITIES"),
    "DeferredTax":                        ("Deferred Tax",                     "OPERATING ACTIVITIES"),
    "StockBasedCompensation":             ("Stock-Based Compensation",         "OPERATING ACTIVITIES"),
    "ChangeInWorkingCapital":             ("Change in Working Capital",        "OPERATING ACTIVITIES"),
    "ChangesInAccountReceivables":        ("Changes in Accounts Receivable",   "OPERATING ACTIVITIES"),
    "ChangeInInventory":                  ("Change in Inventory",              "OPERATING ACTIVITIES"),
    "ChangesInAccountPayables":           ("Changes in Accounts Payable",      "OPERATING ACTIVITIES"),
    "OtherNonCashItems":                  ("Other Non-Cash Items",             "OPERATING ACTIVITIES"),
    "OtherOperatingActivities":           ("Other Operating Activities",       "OPERATING ACTIVITIES"),
    "InvestingCashFlow":                  ("Investing Cash Flow",              "INVESTING ACTIVITIES"),
    "CapitalExpenditure":                 ("Capital Expenditure (Capex)",      "INVESTING ACTIVITIES"),
    "NetInvestmentPurchaseAndSale":       ("Net Investment Activity",          "INVESTING ACTIVITIES"),
    "PurchaseOfBusiness":                 ("Acquisitions",                     "INVESTING ACTIVITIES"),
    "SaleOfBusiness":                     ("Divestitures",                     "INVESTING ACTIVITIES"),
    "PurchaseOfInvestment":               ("Purchase of Investments",          "INVESTING ACTIVITIES"),
    "SaleOfInvestment":                   ("Sale of Investments",              "INVESTING ACTIVITIES"),
    "OtherInvestingActivities":           ("Other Investing Activities",       "INVESTING ACTIVITIES"),
    "FinancingCashFlow":                  ("Financing Cash Flow",              "FINANCING ACTIVITIES"),
    "IssuanceOfDebt":                     ("Debt Issuance",                    "FINANCING ACTIVITIES"),
    "RepaymentOfDebt":                    ("Debt Repayment",                   "FINANCING ACTIVITIES"),
    "IssuanceOfCapitalStock":             ("Stock Issuance",                   "FINANCING ACTIVITIES"),
    "RepurchaseOfCapitalStock":           ("Share Repurchases",                "FINANCING ACTIVITIES"),
    "CashDividendsPaid":                  ("Dividends Paid",                   "FINANCING ACTIVITIES"),
    "OtherFinancingActivities":           ("Other Financing Activities",       "FINANCING ACTIVITIES"),
    "FreeCashFlow":                       ("Free Cash Flow",                   "FREE CASH FLOW"),
    "EndCashPosition":                    ("Ending Cash Position",             "CASH POSITION"),
    "BeginningCashPosition":              ("Beginning Cash Position",          "CASH POSITION"),
    "ChangesInCash":                      ("Net Change in Cash",               "CASH POSITION"),
    "EffectOfExchangeRateChanges":        ("FX Effect on Cash",                "CASH POSITION"),
}


def _clean_yf_label(key: str) -> str:
    """Convert CamelCase yfinance key to readable label (fallback when not in map)."""
    import re
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return spaced.title()


def _extract_yf_esg(asset) -> dict | None:
    """Extract ESG scores from yfinance Ticker.sustainability."""
    try:
        sust = asset.sustainability
        if sust is None or getattr(sust, "empty", True):
            return None
        data: dict = {}
        for idx, row in sust.iterrows():
            val = row.iloc[0] if len(row) > 0 else None
            if val is not None and val == val:  # NaN guard
                data[str(idx)] = val
        if not data:
            return None
        total = data.get("totalEsg") or data.get("esgScores")
        env   = data.get("environmentScore")
        soc   = data.get("socialScore")
        gov   = data.get("governanceScore")
        if all(v is None for v in [total, env, soc, gov]):
            return None

        def _fmt(v: Any) -> str | None:
            if v is None:
                return None
            try:
                return f"{float(v):.2f}"
            except (TypeError, ValueError):
                return str(v)

        return {
            "score": _fmt(total),
            "environmentScore": _fmt(env),
            "socialScore": _fmt(soc),
            "governanceScore": _fmt(gov),
            "esgPerformance": data.get("esgPerformance"),
            "peerGroup": data.get("peerGroup"),
            "peerCount": int(data["peerCount"]) if data.get("peerCount") is not None else None,
            "highestControversy": _fmt(data.get("highestControversy")),
            "percentile": _fmt(data.get("percentile")),
            "source": "Yahoo Finance",
        }
    except Exception as exc:
        logger.debug("ESG extraction failed: %s", exc)
        return None


class YahooFinanceClient:
    """Client to interact with Yahoo Finance API."""

    RANGE_MAP = {
        "1D": "1d",
        "1M": "1mo",
        "3M": "3mo",
        "6M": "6mo",
        "1Y": "1y",
        "3Y": "3y",
        "5Y": "5y",
        "MAX": "max",
    }

    # ── private helpers ───────────────────────────────────────────────────────

    def _asset_type_from_quote_type(self, quote_type: str) -> str:
        normalized = (quote_type or "").upper()
        if normalized == "ETF":
            return "ETF"
        if normalized == "CRYPTOCURRENCY":
            return "CRYPTO"
        if normalized in {"EQUITY", "STOCK"}:
            return "STOCK"
        return "OTHER"

    def _determine_asset_type(self, info: Dict) -> str:
        return self._asset_type_from_quote_type(info.get("quoteType", ""))

    def _looks_like_real_isin(self, value: str) -> bool:
        normalized = (value or "").strip().upper()
        return (
            bool(normalized)
            and len(normalized) == 12
            and normalized[:2].isalpha()
            and not normalized.startswith("ZZ")
            and not normalized.startswith("0P")
        )

    def _parse_ohlcv(self, hist) -> list[dict]:
        """Convert a yfinance history DataFrame to plain-float OHLCV dicts.

        Skips rows where Close is NaN.
        """
        rows = []
        for dt, row in hist.iterrows():
            c = row.get("Close")
            if c is None or c != c:  # NaN guard
                continue
            rows.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open":   float(row["Open"])   if row["Open"]   == row["Open"]   else None,
                "high":   float(row["High"])   if row["High"]   == row["High"]   else None,
                "low":    float(row["Low"])    if row["Low"]    == row["Low"]    else None,
                "close":  float(c),
                "volume": int(row["Volume"])   if row["Volume"] == row["Volume"] else 0,
            })
        return rows

    def _format_table_value(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        if numeric != numeric:
            return ""
        absolute = abs(numeric)
        for divisor, suffix in (
            (1_000_000_000_000, "T"),
            (1_000_000_000, "B"),
            (1_000_000, "M"),
            (1_000, "K"),
        ):
            if absolute >= divisor:
                return f"{numeric / divisor:,.2f}{suffix}"
        return f"{numeric:,.2f}"

    def _statement_to_table(
        self,
        frame,
        *,
        max_periods: int = 6,
        label_map: dict[str, tuple[str, str]] | None = None,
    ) -> dict | None:
        if frame is None or getattr(frame, "empty", True):
            return None
        selected = frame.reindex(
            sorted(frame.columns, reverse=True),
            axis=1,
        ).iloc[:, :max_periods]
        columns = [
            column.strftime("%Y")
            if hasattr(column, "strftime")
            else str(column)
            for column in selected.columns
        ]
        n_cols = len(columns)
        rows: list[dict] = []
        current_category: str | None = None

        for raw_label, values in selected.iterrows():
            key = str(raw_label)
            if label_map and key in label_map:
                clean_label, category = label_map[key]
                if category != current_category:
                    current_category = category
                    rows.append({"label": category, "depth": 0, "cells": [""] * n_cols})
                rows.append({
                    "label": clean_label,
                    "depth": 1,
                    "cells": [self._format_table_value(v) for v in values],
                })
            else:
                fallback_label = _clean_yf_label(key)
                rows.append({
                    "label": fallback_label,
                    "depth": 1,
                    "cells": [self._format_table_value(v) for v in values],
                })
        return {"columns": columns, "flat_rows": rows}

    def _metrics_table(self, metrics: list[tuple[str, Any]]) -> dict | None:
        rows = [
            {
                "label": label,
                "depth": 1,
                "cells": [self._format_table_value(value)],
            }
            for label, value in metrics
            if value is not None and value != ""
        ]
        if not rows:
            return None
        return {"columns": ["VALUE"], "flat_rows": rows}

    def _benchmark_ticker(self, ticker: str, info: dict) -> str:
        normalized = ticker.upper()
        exchange = str(info.get("exchange") or "").upper()
        country = str(info.get("country") or "").upper()
        if normalized.endswith(".PA") or exchange == "PAR":
            return "^FCHI"
        if normalized.endswith(".DE") or exchange in {"GER", "FRA"}:
            return "^GDAXI"
        if normalized.endswith(".L") or exchange == "LSE":
            return "^FTSE"
        if normalized.endswith((".AS", ".BR", ".MI", ".MC", ".LS")):
            return "^STOXX50E"
        if normalized.endswith(".TO") or country == "CANADA":
            return "^GSPTSE"
        if normalized.endswith((".T", ".JP")) or country == "JAPAN":
            return "^N225"
        return "^GSPC"

    def _benchmark_history(self, ticker: str, period: str) -> list[dict]:
        try:
            history = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            return self._parse_ohlcv(history) if history is not None else []
        except Exception as exc:
            logger.debug("benchmark history %s: %s", ticker, exc)
            return []

    # ── public API ────────────────────────────────────────────────────────────

    def search_assets(
        self,
        query: str,
        *,
        asset_type: str | None = None,
        max_results: int = 8,
    ) -> list[Dict]:
        """Search Yahoo Finance and return normalized candidate matches."""
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        requested_type = (asset_type or "").upper() or None
        try:
            search = yf.Search(normalized_query, max_results=max_results)
            quotes = getattr(search, "quotes", None) or []
        except Exception as exc:
            logger.warning("search_assets %s: %s", normalized_query, exc)
            return []

        results: list[Dict] = []
        seen_symbols: set[str] = set()
        for quote in quotes:
            ticker = (quote.get("symbol") or "").strip().upper()
            if not ticker or ticker in seen_symbols:
                continue
            seen_symbols.add(ticker)

            resolved_asset_type = self._asset_type_from_quote_type(
                quote.get("quoteType") or quote.get("typeDisp") or ""
            )
            if resolved_asset_type not in {"STOCK", "ETF"}:
                continue
            if requested_type and resolved_asset_type != requested_type:
                continue

            results.append(
                {
                    "ticker": ticker,
                    "name": quote.get("longname") or quote.get("shortname") or ticker,
                    "exchange": (quote.get("exchange") or "").strip().upper(),
                    "exchange_display": (quote.get("exchDisp") or quote.get("exchange") or "").strip(),
                    "asset_type": resolved_asset_type,
                    "sector": (quote.get("sector") or "").strip(),
                    "industry": (quote.get("industry") or "").strip(),
                }
            )
            if len(results) >= max_results:
                break
        return results

    def validate_ticker(self, ticker: str) -> tuple[bool, Optional[Dict]]:
        """Validate if a ticker exists on Yahoo Finance."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            if not info or "symbol" not in info:
                return False, None
            isin = ""
            try:
                isin = getattr(stock, "isin", "") or info.get("isin", "") or ""
            except Exception:
                isin = info.get("isin", "") or ""
            return True, {
                "ticker": ticker.upper(),
                "name": info.get("longName") or info.get("shortName", ticker),
                "isin": isin,
                "currency": info.get("currency", "USD"),
                "exchange": info.get("exchange", ""),
                "asset_type": self._determine_asset_type(info),
                "sector": info.get("sector", ""),
                "country": info.get("country", ""),
            }
        except Exception as exc:
            logger.warning("validate_ticker %s: %s", ticker, exc)
            return False, None

    def get_full_asset_data(
        self,
        ticker: str,
        *,
        period: str = "5y",
    ) -> Optional[Dict]:
        """Return a frontend-ready company or ETF payload without file caching."""
        normalized_ticker = (ticker or "").strip().upper()
        if not normalized_ticker:
            return None

        try:
            asset = yf.Ticker(normalized_ticker)
            info = asset.info or {}
            if not info or not (info.get("symbol") or info.get("shortName")):
                return None

            history = asset.history(period=period, auto_adjust=True)
            ohlcv = self._parse_ohlcv(history) if history is not None else []
            latest = ohlcv[-1] if ohlcv else {}
            asset_type = self._determine_asset_type(info)
            benchmark_ticker = self._benchmark_ticker(normalized_ticker, info)
            benchmark_history = self._benchmark_history(
                benchmark_ticker,
                period,
            )
            quantitative = _build_quantitative_analysis(
                ohlcv,
                ticker=normalized_ticker,
                currency=info.get("currency", ""),
                benchmark_ticker=benchmark_ticker,
                benchmark_history=benchmark_history,
            )

            isin = ""
            try:
                isin = getattr(asset, "isin", "") or info.get("isin", "") or ""
            except Exception:
                isin = info.get("isin", "") or ""

            income_statement = self._statement_to_table(
                asset.income_stmt, label_map=_IS_LABEL_MAP
            )
            balance_sheet = self._statement_to_table(
                asset.balance_sheet, label_map=_BS_LABEL_MAP
            )
            cash_flow = self._statement_to_table(
                asset.cash_flow, label_map=_CF_LABEL_MAP
            )
            esg_data = _extract_yf_esg(asset)

            dividends = None
            try:
                dividend_series = asset.dividends
                if dividend_series is not None and not dividend_series.empty:
                    recent_dividends = dividend_series.tail(12)
                    dividends = {
                        "columns": [
                            index.strftime("%Y-%m-%d")
                            if hasattr(index, "strftime")
                            else str(index)
                            for index in recent_dividends.index
                        ],
                        "flat_rows": [
                            {
                                "label": "Dividend",
                                "depth": 1,
                                "cells": [
                                    self._format_table_value(value)
                                    for value in recent_dividends.values
                                ],
                            }
                        ],
                    }
            except Exception as exc:
                logger.debug("dividends %s: %s", normalized_ticker, exc)

            overview_metrics = self._metrics_table(
                [
                    ("Market Cap", info.get("marketCap")),
                    ("Enterprise Value", info.get("enterpriseValue")),
                    ("Revenue", info.get("totalRevenue")),
                    ("EBITDA", info.get("ebitda")),
                    ("Net Income", info.get("netIncomeToCommon")),
                    ("Free Cash Flow", info.get("freeCashflow")),
                    ("Total Cash", info.get("totalCash")),
                    ("Total Debt", info.get("totalDebt")),
                    ("Shares Outstanding", info.get("sharesOutstanding")),
                    ("Average Volume", info.get("averageVolume")),
                ]
            )
            valuation = self._metrics_table(
                [
                    ("Trailing P/E", info.get("trailingPE")),
                    ("Forward P/E", info.get("forwardPE")),
                    ("Price / Book", info.get("priceToBook")),
                    ("Price / Sales", info.get("priceToSalesTrailing12Months")),
                    ("EV / Revenue", info.get("enterpriseToRevenue")),
                    ("EV / EBITDA", info.get("enterpriseToEbitda")),
                    ("Dividend Yield", info.get("dividendYield")),
                    ("Beta", info.get("beta")),
                    ("52 Week Low", info.get("fiftyTwoWeekLow")),
                    ("52 Week High", info.get("fiftyTwoWeekHigh")),
                ]
            )

            return {
                "assetType": asset_type,
                "provider": "YAHOO",
                "name": info.get("longName")
                or info.get("shortName")
                or normalized_ticker,
                "isin": isin,
                "overview": {
                    "securityName": info.get("longName")
                    or info.get("shortName")
                    or normalized_ticker,
                    "ticker": normalized_ticker,
                    "exchange": info.get("exchange", ""),
                    "currency": info.get("currency", ""),
                    "sector": info.get("sector") or info.get("category") or "",
                    "industry": info.get("industry", ""),
                    "lastClose": info.get("currentPrice")
                    or info.get("regularMarketPrice")
                    or latest.get("close"),
                    "lastCloseDate": latest.get("date", ""),
                    "dayRangeHigh": info.get("dayHigh"),
                    "dayRangeLow": info.get("dayLow"),
                    "yearRangeHigh": info.get("fiftyTwoWeekHigh"),
                    "yearRangeLow": info.get("fiftyTwoWeekLow"),
                    "marketCap": info.get("marketCap"),
                    "volume": info.get("volume"),
                    "avgVolume": info.get("averageVolume"),
                    "priceEarnings": info.get("trailingPE"),
                    "priceBook": info.get("priceToBook"),
                    "dividendYield": info.get("dividendYield"),
                },
                "companyProfile": {
                    "description": info.get("longBusinessSummary", ""),
                    "address": ", ".join(
                        str(value)
                        for value in (
                            info.get("address1"),
                            info.get("city"),
                            info.get("zip"),
                        )
                        if value
                    ),
                    "country": info.get("country", ""),
                    "phone": info.get("phone", ""),
                    "url": info.get("website", ""),
                    "sector": info.get("sector") or info.get("category") or "",
                    "industry": info.get("industry", ""),
                    "employees": info.get("fullTimeEmployees"),
                    "employeesDate": "",
                    "fiscalYearEnd": info.get("lastFiscalYearEnd", ""),
                },
                "keyMetrics": overview_metrics,
                "incomeStatement": income_statement,
                "balanceSheet": balance_sheet,
                "cashFlow": cash_flow,
                "valuation": valuation,
                "dividends": dividends,
                "profitability": None,
                "operatingGrowth": None,
                "financialHealth": None,
                "freeCashFlow": None,
                "keyExecutives": [
                    {
                        "name": officer.get("name", ""),
                        "title": officer.get("title", ""),
                        "age": officer.get("age", ""),
                        "memberSince": "",
                        "salary": None,
                        "salaryDisplay": "",
                        "salaryPeriod": "",
                        "totalCompensation": officer.get("totalPay"),
                        "totalCompensationDisplay": (
                            f"{self._format_table_value(officer.get('totalPay'))} "
                            f"{info.get('currency', '')}"
                        ).strip()
                        if officer.get("totalPay") is not None
                        else "",
                        "compensationPeriod": str(
                            officer.get("fiscalYear") or ""
                        ),
                        "compensationCurrency": info.get("currency", ""),
                        "compensationBreakdown": [],
                    }
                    for officer in info.get("companyOfficers", [])
                    if officer.get("name")
                ],
                "boardOfDirectors": [],
                "institutionBuyers": [],
                "institutionSellers": [],
                "analysisReport": None,
                "esgRisk": esg_data,
                "sustainability": None,
                "marketHistory": ohlcv,
                "quantitative": quantitative,
            }
        except Exception as exc:
            logger.warning("get_full_asset_data %s: %s", normalized_ticker, exc)
            return None

    def get_quote(self, ticker: str) -> Optional[Dict]:
        """Get current quote for a ticker."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            if not info:
                return None
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            if not current_price:
                hist = stock.history(period="1d")
                if not hist.empty:
                    current_price = float(hist["Close"].iloc[-1])
                else:
                    return None
            previous_close = info.get("previousClose", current_price)
            change = current_price - previous_close if previous_close else 0
            change_percent = (change / previous_close * 100) if previous_close else 0
            result: Dict = {
                "ticker": ticker.upper(),
                "price": Decimal(str(current_price)),
                "previous_close": Decimal(str(previous_close)),
                "change": Decimal(str(change)),
                "change_percent": Decimal(str(change_percent)),
                "volume": info.get("volume", 0),
                "timestamp": datetime.now(timezone.utc),
                "market_state": info.get("marketState", "REGULAR"),
            }
            premarket_price = info.get("preMarketPrice")
            if premarket_price:
                result["premarket_price"] = Decimal(str(premarket_price))
                result["premarket_change"] = Decimal(str(info.get("preMarketChange", 0)))
                result["premarket_change_pct"] = Decimal(str(info.get("preMarketChangePercent", 0)))
            postmarket_price = info.get("postMarketPrice")
            if postmarket_price:
                result["postmarket_price"] = Decimal(str(postmarket_price))
                result["postmarket_change"] = Decimal(str(info.get("postMarketChange", 0)))
                result["postmarket_change_pct"] = Decimal(str(info.get("postMarketChangePercent", 0)))
            return result
        except Exception as exc:
            logger.warning("get_quote %s: %s", ticker, exc)
            return None

    def get_latest_price(self, ticker: str) -> Optional[Decimal]:
        """Return the latest close, checking info dict before fetching history."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            if current_price:
                return Decimal(str(current_price))
            hist = stock.history(period="1d")
            if not hist.empty:
                return Decimal(str(float(hist["Close"].iloc[-1])))
            return None
        except Exception as exc:
            logger.warning("get_latest_price %s: %s", ticker, exc)
            return None

    def get_history(
        self,
        ticker: str,
        range_key: str = "1Y",
        days: int | None = None,
    ) -> Optional[List[Dict]]:
        """Return OHLCV history as a list of dicts with Decimal prices.

        Pass *days* to request an exact number of trading days (up to 10 years).
        Pass *range_key* to use a named range (1D, 1M, 3M, 6M, 1Y, 3Y, 5Y, MAX).
        *days* takes precedence when both are supplied.
        """
        try:
            if days is not None:
                if days <= 0:
                    return None
                period = f"{min(days, 3650)}d" if days <= 3650 else "max"
            else:
                period = self.RANGE_MAP.get(range_key.upper(), "1y")
            hist = yf.Ticker(ticker).history(period=period)
            if hist.empty:
                return None
            history = [
                {
                    "date": date.date(),
                    "open": Decimal(str(row["Open"])),
                    "high": Decimal(str(row["High"])),
                    "low": Decimal(str(row["Low"])),
                    "close": Decimal(str(row["Close"])),
                    "volume": int(row["Volume"]),
                }
                for date, row in hist.iterrows()
            ]
            if days is not None and days < len(history):
                history = history[-days:]
            return history
        except Exception as exc:
            logger.warning("get_history %s: %s", ticker, exc)
            return None

    def get_intraday_history(
        self,
        ticker: str,
        *,
        period: str = "1d",
        interval: str = "5m",
    ) -> Optional[List[Dict]]:
        """Return intraday close history as a list of dicts with Decimal prices."""
        try:
            hist = yf.Ticker(ticker).history(
                period=period,
                interval=interval,
                auto_adjust=True,
                prepost=True,
            )
            if hist.empty:
                return None

            history = []
            for moment, row in hist.iterrows():
                close_value = row.get("Close")
                if close_value is None or close_value != close_value:
                    continue

                point = moment.to_pydatetime() if hasattr(moment, "to_pydatetime") else moment
                if getattr(point, "tzinfo", None) is not None:
                    point = point.astimezone(timezone.utc)

                history.append(
                    {
                        "date": point.isoformat(timespec="minutes"),
                        "close": Decimal(str(close_value)),
                    }
                )
            return history or None
        except Exception as exc:
            logger.warning("get_intraday_history %s: %s", ticker, exc)
            return None

    def get_metrics(self, ticker: str) -> Optional[Dict]:
        try:
            info = yf.Ticker(ticker).info
            if not info:
                return None
            return {
                "ticker": ticker.upper(),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
                "beta": info.get("beta"),
                "dividend_yield": info.get("dividendYield"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "average_volume": info.get("averageVolume"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
            }
        except Exception as exc:
            logger.warning("get_metrics %s: %s", ticker, exc)
            return None

    def get_analyst_targets(self, ticker: str) -> Optional[Dict]:
        """Return analyst consensus price targets for *ticker*."""
        try:
            info = yf.Ticker(ticker).info
            if not info:
                return None
            low = info.get("targetLowPrice")
            high = info.get("targetHighPrice")
            if low is None or high is None:
                return None
            mean = info.get("targetMeanPrice")
            median = info.get("targetMedianPrice")
            count = info.get("numberOfAnalystOpinions")
            return {
                "target_low": float(low),
                "target_mean": float(mean) if mean is not None else None,
                "target_median": float(median) if median is not None else None,
                "target_high": float(high),
                "analyst_count": int(count) if count is not None else None,
            }
        except Exception as exc:
            logger.warning("get_analyst_targets %s: %s", ticker, exc)
            return None

    def get_valuation_summary(
        self, ticker: str, isin: Optional[str] = None
    ) -> Optional[Dict]:
        """Return 52-week range, current price and analyst targets in ONE call.

        Args:
            ticker: Yahoo Finance ticker symbol (used as label and fallback key).
            isin:   ISIN from Morningstar.  When provided it is used as the
                    yfinance lookup key, avoiding exchange-suffix ambiguity.
        """
        lookups: list[str] = []
        normalized_isin = (isin or "").strip().upper()
        normalized_ticker = (ticker or "").strip()
        if self._looks_like_real_isin(normalized_isin):
            lookups.append(normalized_isin)
        if normalized_ticker and normalized_ticker not in lookups:
            lookups.append(normalized_ticker)

        for lookup in lookups:
            try:
                info = yf.Ticker(lookup).info
            except Exception as exc:
                logger.warning("get_valuation_summary %s via %s: %s", ticker, lookup, exc)
                continue

            if not info:
                continue

            low = info.get("fiftyTwoWeekLow")
            high = info.get("fiftyTwoWeekHigh")
            if low is None or high is None:
                continue

            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
            )
            tgt_low = info.get("targetLowPrice")
            tgt_high = info.get("targetHighPrice")
            analyst = None
            if tgt_low is not None and tgt_high is not None:
                tgt_mean = info.get("targetMeanPrice")
                tgt_median = info.get("targetMedianPrice")
                count = info.get("numberOfAnalystOpinions")
                analyst = {
                    "target_low": float(tgt_low),
                    "target_mean": float(tgt_mean) if tgt_mean is not None else None,
                    "target_median": float(tgt_median) if tgt_median is not None else None,
                    "target_high": float(tgt_high),
                    "analyst_count": int(count) if count is not None else None,
                }
            return {
                "ticker": (info.get("symbol") or normalized_ticker or lookup).upper(),
                "current_price": float(price) if price is not None else None,
                "week52_low": float(low),
                "week52_high": float(high),
                "currency": info.get("currency", ""),
                "analyst": analyst,
            }

        return None

    def get_multiples(self, ticker: str) -> Optional[Dict]:
        """Return key valuation multiples for *ticker* from a single ``yf.info`` call."""
        try:
            info = yf.Ticker(ticker).info
            if not info:
                return None

            def _f(key: str) -> Optional[float]:
                v = info.get(key)
                return float(v) if v is not None else None

            return {
                "ticker": ticker.upper(),
                "name": info.get("shortName") or info.get("longName") or ticker.upper(),
                "market_cap": info.get("marketCap"),
                "enterprise_value": info.get("enterpriseValue"),
                "trailing_pe": _f("trailingPE"),
                "forward_pe": _f("forwardPE"),
                "ev_to_revenue": _f("enterpriseToRevenue"),
                "ev_to_ebitda": _f("enterpriseToEbitda"),
                "price_to_book": _f("priceToBook"),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "currency": info.get("currency", ""),
                "current_price": (
                    _f("currentPrice") or _f("regularMarketPrice") or _f("previousClose")
                ),
            }
        except Exception as exc:
            logger.warning("get_multiples %s: %s", ticker, exc)
            return None

    def get_market_data(
        self,
        isin_or_ticker: str,
        fallback_ticker: str | None = None,
        period: str = "5y",
    ) -> dict | None:
        """Fetch full OHLCV history for quantitative analysis.

        Tries *isin_or_ticker* first (yfinance accepts ISINs directly).
        Falls back to *fallback_ticker* when the primary returns empty data.

        Returns ``{"ohlcv": [...], "currency": str, "name": str}`` or ``None``.
        All numeric values are plain floats.
        """
        def _fetch(key: str) -> tuple[list, str, str]:
            tk = yf.Ticker(key)
            hist = tk.history(period=period, auto_adjust=True)
            if hist is None or hist.empty:
                return [], "", ""
            rows = self._parse_ohlcv(hist)
            info = tk.info or {}
            return rows, info.get("currency", ""), info.get("longName") or info.get("shortName", key)

        try:
            rows, currency, name = _fetch(isin_or_ticker)
            if not rows and fallback_ticker and fallback_ticker != isin_or_ticker:
                rows, currency, name = _fetch(fallback_ticker)
            if not rows:
                return None
            return {"ohlcv": rows, "currency": currency, "name": name}
        except Exception as exc:
            logger.warning("get_market_data %s: %s", isin_or_ticker, exc)
            if fallback_ticker:
                try:
                    rows, currency, name = _fetch(fallback_ticker)
                    if rows:
                        return {"ohlcv": rows, "currency": currency, "name": name}
                except Exception:
                    pass
            return None

    def get_benchmark(self, benchmark_ticker: str, period: str = "5y") -> list | None:
        """Fetch close prices for a benchmark index (e.g. '^GSPC', '^STOXX50E').

        Returns ``[{"date": str, "close": float}]`` or ``None``.
        """
        try:
            tk = yf.Ticker(benchmark_ticker)
            hist = tk.history(period=period, auto_adjust=True)
            if hist is None or hist.empty:
                return None
            rows = [
                {"date": dt.strftime("%Y-%m-%d"), "close": float(row["Close"])}
                for dt, row in hist.iterrows()
                if row.get("Close") == row.get("Close")  # NaN guard
            ]
            return rows if rows else None
        except Exception as exc:
            logger.warning("get_benchmark %s: %s", benchmark_ticker, exc)
            return None

    def get_historical_price(self, ticker: str, target_date: datetime) -> Optional[Decimal]:
        try:
            if hasattr(target_date, "date"):
                target_date = target_date.date()
            stock = yf.Ticker(ticker)
            start_date = target_date - timedelta(days=7)
            end_date = target_date + timedelta(days=1)
            hist = stock.history(start=start_date.isoformat(), end=end_date.isoformat())
            if hist.empty:
                return None
            # Single pass: track the closest price at or before target_date
            closest: Optional[Decimal] = None
            for idx, row in hist.iterrows():
                if idx.date() <= target_date:
                    closest = Decimal(str(row["Close"]))
                    if idx.date() == target_date:
                        break
            return closest
        except Exception as exc:
            logger.warning("get_historical_price %s on %s: %s", ticker, target_date, exc)
            return None
