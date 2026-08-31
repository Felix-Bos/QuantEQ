# QuantEQ

Open-source financial and climate data analysis for public companies.

## Overview

QuantEQ is a Django web application that brings together fundamental financial
analysis, climate/ESG data, and quantitative price analytics into a single
company research workspace. Search a company, and get its financial
statements, valuation multiples, ESG risk profile, analyst coverage, and
price-based analytics side by side.

The project also includes a portfolio module for grouping tickers, currently
in an early/stub state.

## Features

- Company search across stocks, ETFs, and funds
- Financial statements (income statement, balance sheet, cash flow) with
  drill-down detail
- Valuation multiples (P/E, EV/EBIT, EV/EBITDA) and a 12-year financial
  series ready for DCF modeling
- Financial ratios: liquidity, solvency, profitability, cash flow, returns,
  efficiency, growth, and valuation
- ESG risk and sustainability scoring
- Climate data per company (emissions, targets, SBTI alignment)
- Analyst ratings, bull/bear cases, board and executive information,
  institutional ownership
- Quantitative price analytics: volatility, beta, moving averages, RSI,
  relative performance vs. a benchmark
- A compact, card-based dashboard UI (light and dark theme, switchable and
  persisted per browser) with inline visual indicators — an ESG risk ring,
  ownership and RSI mini-bars — instead of raw numbers alone

## Data sources

- **Financial data — [Morningstar](https://www.morningstar.com)**, via the
  [`mstarpy`](https://pypi.org/project/mstarpy/) package: fundamentals,
  valuation multiples, financial statements, ESG risk, analyst reports,
  ownership. Requires a Morningstar API key (see [Setup](#setup)).
- **Climate data — Tracenable-style scraping** (`fetch_data/tracenable_scraper.py`):
  company-level emissions and climate-target data scraped from public
  disclosures, resolved for any company by name (not limited to a fixed
  index). For extending the climate dataset with Science-Based Targets data,
  see [sciencebasedtargets.org](https://sciencebasedtargets.org).
- **Market/quantitative data — [Yahoo Finance](https://finance.yahoo.com)**,
  via [`yfinance`](https://pypi.org/project/yfinance/): historical prices
  feeding the quantitative analytics engine (volatility, beta, technicals,
  relative performance).

## Tech stack

- **Backend**: Django 5 + Django REST Framework
- **Frontend**: Django templates with vanilla CSS/JS — no build step, no JS
  framework
- **Database**: SQLite (local/dev default)

## Project structure

```
backend/                  Django project
  quanteq/                 settings, root URLs
  users/                    authentication app
  analysis/                 core app: company search, financials, climate,
                             quantitative analytics
  portfolio/                portfolio grouping (early stage)
frontend/
  templates/                Django templates
  static/                   CSS and JS (no build tooling)
fetch_data/                Morningstar, Yahoo Finance, and Tracenable data clients
requirements.txt
.env.example
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# then edit .env and fill in SECRET_KEY and MSTARPY_API_KEY

python backend/manage.py migrate
python backend/manage.py runserver
```

A Morningstar API key (`MSTARPY_API_KEY`) is required for financial data to
load — this is tied to a Morningstar/`mstarpy`-compatible account, so this
dependency isn't fully "free" even though the code itself is open source.

Morningstar's company-search endpoint sits behind an AWS WAF bot challenge
that only a real browser can solve. `fetch_data/waf_session.py` handles this
automatically: the first search launches a headless Chromium (via Playwright)
to solve the challenge once, caches the resulting session to
`fetch_data/.waf_session_cache.json` (gitignored, valid for several days),
and reuses it for subsequent searches without relaunching a browser. This is
why `playwright install chromium` is required — plain HTTP requests alone
cannot pass this challenge.

## Running tests

```bash
python -m unittest fetch_data.tests
```

## License

[MIT](LICENSE)
