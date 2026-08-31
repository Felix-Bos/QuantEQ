from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any


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


def build_quantitative_analysis(
    history: list[dict],
    *,
    ticker: str,
    currency: str = "",
    benchmark_ticker: str = "",
    benchmark_history: list[dict] | None = None,
    risk_free_rate: float = 0.02,
    source: str = "Morningstar",
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
        (closes[-1] / closes[0])
        ** (
            365.25
            / max(
                1,
                (
                    datetime.fromisoformat(dates[-1])
                    - datetime.fromisoformat(dates[0])
                ).days,
            )
        )
        - 1
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
    cvar_95_values = [
        value for value in returns if var_95 is not None and value <= var_95
    ]
    cvar_99_values = [
        value for value in returns if var_99 is not None and value <= var_99
    ]
    mean_return = mean_daily or 0.0
    if std_daily:
        skewness = statistics.fmean(
            ((value - mean_return) / std_daily) ** 3 for value in returns
        )
        kurtosis = (
            statistics.fmean(
                ((value - mean_return) / std_daily) ** 4 for value in returns
            )
            - 3
        )
    else:
        skewness = kurtosis = None

    autocorrelation = None
    if len(returns) > 2:
        left = returns[:-1]
        right = returns[1:]
        left_mean = statistics.fmean(left)
        right_mean = statistics.fmean(right)
        numerator = sum(
            (a - left_mean) * (b - right_mean) for a, b in zip(left, right)
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
        for item_date, close in zip(dates, closes):
            benchmark_close = benchmark_close_by_date.get(item_date)
            if benchmark_close is None:
                continue
            if first_benchmark is None:
                first_benchmark = benchmark_close
            benchmark_base_by_date[item_date] = benchmark_close / first_benchmark * 100
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
                for asset_value, benchmark_value in zip(aligned_asset, aligned_benchmark)
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
                for asset_value, benchmark_value in zip(aligned_asset, aligned_benchmark)
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
                        annualized_excess / tracking_error if tracking_error else None
                    ),
                }
            )

            benchmark_closes = [
                benchmark_close_by_date[item_date]
                for item_date in dates[-252:]
                if item_date in benchmark_close_by_date
            ]
            if len(benchmark_closes) > 1:
                benchmark_return_1y = benchmark_closes[-1] / benchmark_closes[0] - 1
                relative["benchmarkReturn1Y"] = benchmark_return_1y
                if relative["assetReturn1Y"] is not None:
                    relative["outperformance1Y"] = (
                        relative["assetReturn1Y"] - benchmark_return_1y
                    )

    year = dates[-1][:4]
    ytd_index = next(
        (index for index, item_date in enumerate(dates) if item_date.startswith(year)),
        len(dates) - 1,
    )
    ytd_return = closes[-1] / closes[ytd_index] - 1 if closes[ytd_index] else None
    average_volume_20 = _safe_mean([float(value) for value in volumes[-20:]])
    relative_volume = volumes[-1] / average_volume_20 if average_volume_20 else None

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
        "source": source,
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
