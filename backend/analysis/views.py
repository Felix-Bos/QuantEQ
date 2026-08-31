import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from analysis.services.market_data import (
    MarketDataError,
    fetch_asset_detail,
    fetch_company_data,
    search_assets,
)

logger = logging.getLogger(__name__)


class _NumpySafe(json.JSONEncoder):
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)


@login_required
def workspace_view(request):
    return render(request, 'analysis/workspace.html', {'active_module': 'analysis'})


@login_required
def company_detail_view(request, sec_id):
    """Renders a skeleton page immediately; data is fetched client-side via JS."""
    return render(request, 'analysis/company_detail.html', {
        'sec_id': sec_id,
        'active_module': 'analysis',
    })


@login_required
@require_GET
def asset_data_api(request, sec_id):
    """Returns complete provider data without persistent file caching."""
    from django.conf import settings
    try:
        asset = fetch_asset_detail(sec_id, period='5y')
        return JsonResponse(asset, encoder=_NumpySafe)
    except MarketDataError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception as exc:
        logger.exception('asset_data_api failed for %s', sec_id)
        msg = repr(exc) if settings.DEBUG else 'Company data is currently unavailable.'
        return JsonResponse({'error': msg}, status=500)


@login_required
@require_GET
def search_companies(request):
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})
    try:
        results = search_assets(query, limit=12)
    except MarketDataError as exc:
        return JsonResponse({'results': [], 'error': str(exc)}, status=503)
    except Exception:
        return JsonResponse(
            {'results': [], 'error': 'Company and ETF search is unavailable.'},
            status=502,
        )
    return JsonResponse({'results': results})


@login_required
@require_GET
def company_data(request, sec_id):
    from django.conf import settings
    period = request.GET.get('period', '5y').strip().lower()
    if period not in {'1mo', '3mo', '6mo', '1y', '3y', '5y', '10y', 'max'}:
        return JsonResponse({'error': 'Unsupported period.'}, status=400)
    try:
        payload = fetch_company_data(sec_id, period=period)
    except MarketDataError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception as exc:
        logger.exception('company_data failed for %s', sec_id)
        msg = repr(exc) if settings.DEBUG else 'Company data is unavailable.'
        return JsonResponse({'error': msg}, status=502)
    return JsonResponse(payload)
