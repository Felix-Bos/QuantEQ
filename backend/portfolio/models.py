from django.db import models
from django.conf import settings


class Portfolio(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    tickers = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
