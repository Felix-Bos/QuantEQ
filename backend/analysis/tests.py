from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from analysis.services.market_data import (
    _normalize_financial_table,
    fetch_asset_detail,
    fetch_company_data,
    search_assets,
)
from users.models import User


class MarketDataServiceTests(TestCase):
    def test_financial_table_is_newest_first_without_mixing_values(self):
        table = {
            'columns': ['2023', 'TTM', '2025', '2024'],
            'flat_rows': [
                {
                    'label': 'Revenue',
                    'depth': 1,
                    'cells': ['30', '55', '50', '40'],
                },
            ],
        }

        normalized = _normalize_financial_table(table)

        self.assertEqual(
            normalized['columns'],
            ['2025', '2024', '2023', 'TTM'],
        )
        self.assertEqual(
            normalized['flat_rows'][0]['cells'],
            ['50', '40', '30', '55'],
        )

    @patch('analysis.services.market_data.get_morningstar_client')
    @patch('analysis.services.market_data._fetch_morningstar_quantitative')
    @patch('analysis.services.market_data.fetch_climate_profile')
    def test_fetch_company_data_combines_morningstar_and_tracenable(
        self,
        fetch_climate_profile,
        fetch_quantitative,
        get_morningstar_client,
    ):
        get_morningstar_client.return_value.fetch_asset_data.return_value = {
            'assetType': 'STOCK',
            'name': 'LVMH',
            'isin': 'FR0000121014',
            'overview': {'ticker': 'MC', 'exchange': 'XPAR'},
        }
        fetch_climate_profile.return_value = {
            'provider': 'TRACENABLE',
            'status': 'FOUND',
            'slug': 'lvmh',
        }
        fetch_quantitative.return_value = {
            'source': 'Morningstar',
            'series': [],
        }

        payload = fetch_company_data('0P0000ABC', period='1y')

        self.assertEqual(payload['security']['provider'], 'MORNINGSTAR')
        self.assertEqual(payload['morningstar']['name'], 'LVMH')
        self.assertEqual(payload['climate']['provider'], 'TRACENABLE')
        self.assertEqual(payload['quantitative']['source'], 'Morningstar')

    @patch('analysis.services.market_data.get_morningstar_client')
    def test_search_uses_morningstar_only(self, get_morningstar_client):
        get_morningstar_client.return_value.search_assets.return_value = [
            {
                'ticker': 'MC',
                'name': 'LVMH',
                'exchange': 'XPAR',
                'secId': '0P0001QHYU',
                'type': 'STOCK',
            },
        ]

        results = search_assets('LVMH', limit=12)

        self.assertEqual(results[0]['secId'], '0P0001QHYU')
        self.assertEqual(results[0]['type'], 'STOCK')
        self.assertEqual(results[0]['provider'], 'MORNINGSTAR')

    @patch('analysis.services.market_data.get_morningstar_client')
    def test_unknown_plain_ticker_must_resolve_on_morningstar(
        self,
        get_morningstar_client,
    ):
        get_morningstar_client.return_value.search_assets.return_value = []

        with self.assertRaises(ValueError):
            fetch_asset_detail('UNKNOWN')

    @patch('analysis.services.market_data.get_morningstar_client')
    @patch('analysis.services.market_data._fetch_morningstar_quantitative')
    @patch('analysis.services.market_data.fetch_climate_profile')
    def test_lvmh_ticker_uses_known_morningstar_security(
        self,
        fetch_climate_profile,
        fetch_quantitative,
        get_morningstar_client,
    ):
        get_morningstar_client.return_value.fetch_asset_data.return_value = {
            'assetType': 'STOCK',
            'name': 'LVMH',
            'overview': {'ticker': 'MCp', 'exchange': 'XPAR'},
        }
        fetch_climate_profile.return_value = {
            'provider': 'TRACENABLE',
            'status': 'FOUND',
            'slug': 'lvmh',
        }
        fetch_quantitative.return_value = {'ticker': 'MCp', 'series': []}

        payload = fetch_asset_detail('MC.PA')

        get_morningstar_client.return_value.fetch_asset_data.assert_called_once_with(
            '0P0001QHYU',
            include_valuation=True,
        )
        self.assertEqual(payload['provider'], 'MORNINGSTAR')
        self.assertEqual(payload['climateData']['slug'], 'lvmh')
        self.assertEqual(payload['quantitative']['ticker'], 'MCp')

    @patch('analysis.services.market_data.get_morningstar_client')
    @patch('analysis.services.market_data._fetch_morningstar_quantitative')
    @patch('analysis.services.market_data.fetch_climate_profile')
    def test_missing_quantitative_keeps_morningstar_sections(
        self,
        fetch_climate_profile,
        fetch_quantitative,
        get_morningstar_client,
    ):
        get_morningstar_client.return_value.fetch_asset_data.return_value = {
            'assetType': 'STOCK',
            'name': 'LVMH',
            'overview': {'ticker': 'MCp', 'exchange': 'XPAR'},
            'esgRisk': {'score': 13.55},
            'institutionBuyers': [{'name': 'Example Capital'}],
        }
        fetch_quantitative.return_value = None
        fetch_climate_profile.return_value = {
            'provider': 'TRACENABLE',
            'status': 'NO_DATA',
        }

        payload = fetch_asset_detail('0P0001QHYU')

        self.assertEqual(payload['esgRisk']['score'], 13.55)
        self.assertEqual(
            payload['institutionBuyers'][0]['name'],
            'Example Capital',
        )
        self.assertIsNone(payload['quantitative'])


class MarketDataViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='analyst',
            password='Test-password-123',
        )
        self.client.force_login(self.user)

    def test_short_search_query_does_not_call_provider(self):
        with patch('analysis.views.search_assets') as search_assets:
            response = self.client.get(
                reverse('search_companies'),
                {'q': 'A'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'results': []})
        search_assets.assert_not_called()

    def test_workspace_contains_company_search(self):
        response = self.client.get(reverse('analysis'))

        self.assertContains(response, 'id="searchInput"')
        self.assertContains(response, reverse('search_companies'))
        self.assertContains(response, '/static/js/search.js')

    def test_company_detail_contains_search_and_dynamic_detail_shell(self):
        response = self.client.get(
            reverse('company_detail', args=['0P000000GY']),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="searchInput"')
        self.assertContains(response, 'id="detailContent"')
        self.assertContains(response, 'data-tab="quantitative"')
        self.assertContains(response, 'data-tab="income"')
        self.assertContains(response, 'data-tab="management"')
        self.assertContains(response, 'data-tab="institutions"')
        self.assertContains(response, 'data-tab="analysts"')
        self.assertContains(response, 'data-tab="esg"')
        self.assertContains(response, 'data-tab="climate"')
        self.assertContains(response, '/analysis/api/0P000000GY/')
        self.assertContains(response, '/analysis/company/__SEC_ID__/')

    @patch('analysis.views.search_assets')
    def test_search_returns_morningstar_results(self, search_assets):
        search_assets.return_value = [
            {
                'name': 'Apple Inc',
                'ticker': 'AAPL',
                'exchange': 'XNAS',
                'secId': '0P000000GY',
                'type': 'STOCK',
            }
        ]

        response = self.client.get(
            reverse('search_companies'),
            {'q': 'Apple'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['ticker'], 'AAPL')
        search_assets.assert_called_once_with('Apple', limit=12)

    @patch('analysis.views.fetch_company_data')
    def test_company_endpoint_returns_combined_data(self, fetch_company_data):
        fetch_company_data.return_value = {
            'security': {'sec_id': '0P000000GY'},
            'morningstar': {'name': 'Apple Inc'},
            'climate': {'provider': 'TRACENABLE'},
            'quantitative': {'source': 'Morningstar'},
        }

        response = self.client.get(
            reverse('company_data', args=['0P000000GY']),
            {'period': '5y'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['morningstar']['name'], 'Apple Inc')
        fetch_company_data.assert_called_once_with('0P000000GY', period='5y')

    @patch('analysis.views.fetch_asset_detail')
    def test_asset_api_uses_generic_detail_service(self, fetch_asset_detail):
        fetch_asset_detail.return_value = {
            'provider': 'MORNINGSTAR',
            'assetType': 'STOCK',
            'name': 'LVMH',
            'overview': {'ticker': 'MC'},
        }

        response = self.client.get(
            reverse('asset_data_api', args=['QQQ']),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['provider'], 'MORNINGSTAR')
        fetch_asset_detail.assert_called_once_with('QQQ', period='5y')

    def test_company_endpoint_rejects_unknown_period(self):
        response = self.client.get(
            reverse('company_data', args=['0P000000GY']),
            {'period': '2y'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Unsupported period.'})
