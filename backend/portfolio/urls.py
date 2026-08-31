from django.urls import path
from portfolio.views import workspace_view

urlpatterns = [
    path('', workspace_view, name='portfolio'),
]
