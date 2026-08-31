from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('', RedirectView.as_view(url='/analysis/'), name='home'),
    path('admin/', admin.site.urls),
    path('auth/', include('users.urls')),
    path('analysis/', include('analysis.urls')),
    path('portfolio/', include('portfolio.urls')),
]
