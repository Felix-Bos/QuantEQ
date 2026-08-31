from django.urls import path
from analysis.views import asset_data_api, company_data, company_detail_view, search_companies, workspace_view

urlpatterns = [
    path('', workspace_view, name='analysis'),
    path('search/', search_companies, name='search_companies'),
    path('company/<str:sec_id>/', company_detail_view, name='company_detail'),
    path('api/<str:sec_id>/', asset_data_api, name='asset_data_api'),
    path('data/company/<str:sec_id>/', company_data, name='company_data'),
]
