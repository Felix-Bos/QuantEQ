import unittest
from datetime import date, timedelta
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fetch_data.get_mrnstar import (
    DataFormatter,
    extract_analysis_report,
    extract_esg_risk,
    extract_institutions,
    extract_people,
    extract_sustainability,
)
from analysis.services.quantitative import build_quantitative_analysis


class MorningstarOwnershipTests(unittest.TestCase):
    def test_people_use_latest_available_compensation_period(self):
        people = extract_people(
            {
                "datesDef": ["2023", "2024", "2025"],
                "currency": "USD",
                "rows": [
                    {
                        "type": "person",
                        "name": "Jane Doe",
                        "title": "Chief Executive Officer",
                        "age": "51",
                        "memberSince": "2020",
                        "totalCompensation": ["100", "120", None],
                        "compensation": [
                            {
                                "nameId": "salary",
                                "name": "Salary",
                                "datum": ["40", "50", None],
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(people[0]["salaryDisplay"], "50 USD")
        self.assertEqual(people[0]["salaryPeriod"], "2024")
        self.assertEqual(people[0]["totalCompensationDisplay"], "120 USD")
        self.assertEqual(people[0]["compensationPeriod"], "2024")

    def test_institution_zero_change_is_not_hidden(self):
        institutions = extract_institutions(
            {
                "rows": [
                    {
                        "name": "Example Capital",
                        "totalSharesHeld": 1.25,
                        "currentShares": 1000,
                        "changeAmount": 0,
                        "totalAssets": 2.5,
                        "ticker": "EXAMPLE",
                        "trend": "_PO_",
                        "date": "2026-05-31T00:00:00.000",
                    }
                ]
            },
            DataFormatter(),
        )

        self.assertEqual(institutions[0]["changeAmount"], "0")
        self.assertEqual(institutions[0]["totalAssets"], "2.50")
        self.assertEqual(institutions[0]["ticker"], "EXAMPLE")
        self.assertEqual(institutions[0]["trend"], "")
        self.assertEqual(institutions[0]["date"], "2026-05-31")


class MorningstarEsgTests(unittest.TestCase):
    def test_esg_risk_includes_controversies_and_material_issues(self):
        esg = extract_esg_risk(
            {
                "susEsgRiskScore": 13.55,
                "susEsgRiskCategory": "Low",
                "comHighestControversyLevel": 2,
                "comControversyLevelDescriptor": "Moderate",
                "comHighestControversyTopics": "Business Ethics, Employee",
                "notableIssue1Name": "Overall",
                "notableIssue1": "Human Capital",
                "subIndustry": "Luxury Apparel",
                "asOfDate": "2026-06-06T05:00:00.000",
            }
        )

        self.assertEqual(esg["score"], 13.55)
        self.assertEqual(esg["controversyTopics"], "Business Ethics, Employee")
        self.assertEqual(
            esg["notableIssues"],
            [{"scope": "Overall", "issue": "Human Capital"}],
        )
        self.assertEqual(esg["subIndustry"], "Luxury Apparel")

    def test_sustainability_includes_complete_peer_comparison(self):
        sustainability = extract_sustainability(
            {
                "companyName": "Example Inc",
                "esgRiskScore": 15.0,
                "subindustryExposureScore": 36.0,
                "subindustryExposureCategory": "Medium",
                "peers": [
                    {
                        "companyName": "Peer Inc",
                        "companyId": "0C000PEER",
                        "esgRiskScore": 19.0,
                        "companyExposureScore": 42.0,
                        "overallManagementScore": 57.0,
                        "neglectedRisk": 16.0,
                        "neglectedRiskPer": 42.0,
                        "subindustryExposureScore": 39.0,
                        "subindustryExposureCategory": "Medium",
                    }
                ],
            }
        )

        peer = sustainability["peers"][0]
        self.assertEqual(peer["name"], "Peer Inc")
        self.assertEqual(peer["companyExposureScore"], 42.0)
        self.assertEqual(peer["overallManagementScore"], 57.0)
        self.assertEqual(peer["companyId"], "0C000PEER")


class MorningstarAnalysisTests(unittest.TestCase):
    def test_report_prefers_complete_text_and_exposes_author(self):
        report = extract_analysis_report(
            {
                "rpsCovered": True,
                "isQuan": False,
                "analysisReport": {
                    "headLine": "A complete Morningstar report",
                    "investmentThesis": "Short excerpt",
                    "investmentThesisText": ["First paragraph.", "Second paragraph."],
                    "publishDate": "2026-05-01T01:27:00Z",
                    "author": {
                        "authorName": "Jane Analyst",
                        "profiles": [
                            {
                                "byLine": "Jane Analyst, CFA",
                                "jobTitle": "Senior Equity Analyst",
                                "isPrimaryProfile": True,
                            }
                        ],
                    },
                },
            }
        )

        self.assertEqual(
            report["investmentThesis"],
            "First paragraph.\n\nSecond paragraph.",
        )
        self.assertEqual(report["author"], "Jane Analyst, CFA")
        self.assertEqual(report["authorTitle"], "Senior Equity Analyst")
        self.assertEqual(report["publishDate"], "2026-05-01")


class QuantitativeAnalysisTests(unittest.TestCase):
    def test_quantitative_payload_contains_risk_and_relative_metrics(self):
        start = date(2025, 1, 2)
        history = []
        benchmark = []
        for index in range(300):
            current_date = start + timedelta(days=index)
            price = 100 + index * 0.15 + (index % 11 - 5) * 0.2
            benchmark_price = 100 + index * 0.08
            history.append(
                {
                    "date": current_date.isoformat(),
                    "open": price - 0.4,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price,
                    "volume": 1_000_000 + index * 100,
                }
            )
            benchmark.append(
                {
                    "date": current_date.isoformat(),
                    "open": benchmark_price,
                    "high": benchmark_price,
                    "low": benchmark_price,
                    "close": benchmark_price,
                    "volume": 0,
                }
            )

        result = build_quantitative_analysis(
            history,
            ticker="TEST",
            currency="USD",
            benchmark_ticker="^GSPC",
            benchmark_history=benchmark,
        )

        self.assertEqual(result["observations"], 300)
        self.assertEqual(result["benchmarkTicker"], "^GSPC")
        self.assertEqual(len(result["series"]), 300)
        self.assertIsNotNone(result["performance"]["oneYear"])
        self.assertIsNotNone(result["risk"]["annualizedVolatility"])
        self.assertIsNotNone(result["technical"]["rsi14"])
        self.assertIsNotNone(result["relative"]["beta"])
        self.assertIsNotNone(result["series"][-1]["ma200"])


if __name__ == "__main__":
    unittest.main()
