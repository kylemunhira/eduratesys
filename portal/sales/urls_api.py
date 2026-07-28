from django.urls import path

from inventory.api import StockSnapshotAPIView

from .api import SaleIngestAPIView

urlpatterns = [
    path("sales/", SaleIngestAPIView.as_view(), name="api-sales-ingest"),
    path("stock/", StockSnapshotAPIView.as_view(), name="api-stock-snapshot"),
]
