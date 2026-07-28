from django.urls import path

from .api import SaleIngestAPIView

urlpatterns = [
    path("sales/", SaleIngestAPIView.as_view(), name="api-sales-ingest"),
]
