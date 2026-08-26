from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from catalog.models import Branch, Customer, Product
from reports.views import sales_report_data
from sales.models import Sale, SaleItem


class SalesReportTonsTests(TestCase):
    """Tons must be computed from aggregated qty, not rounded per receipt line."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "admin", "admin@example.com", "pass"
        )
        customer = Customer.objects.create(name="Test Customer")
        self.branch = Branch.objects.create(customer=customer, name="Main")
        self.product = Product.objects.create(
            code="BRO-25",
            name="Broiler Finisher 25kg",
            category="Poultry Broilers",
            barcode="1001",
        )
        self.factory = RequestFactory()

    def test_many_small_sales_do_not_inflate_tons(self):
        # 100 × 1 bag of 25kg = 2.50 t true weight.
        # Per-line rounding (0.025 → 0.03) would wrongly yield 3.00 t.
        now = timezone.now()
        for i in range(100):
            sale = Sale.objects.create(
                branch=self.branch,
                external_sale_id=f"sale-{i}",
                sold_at=now,
                total_amount=Decimal("10.00"),
            )
            SaleItem.objects.create(
                sale=sale,
                product=self.product,
                quantity=1,
                unit_price=Decimal("10.00"),
            )

        request = self.factory.get("/reports/sales/?period=30d")
        request.user = self.user
        _bounds, raw_rows, by_category, by_sku, by_branch, totals = sales_report_data(
            request
        )

        expected = Decimal("2.500")
        self.assertEqual(totals["tons"], expected)
        self.assertEqual(by_category[0]["volume"], expected)
        self.assertEqual(by_sku[0]["volume"], expected)
        self.assertEqual(by_branch[0]["volume"], expected)
        self.assertEqual(raw_rows[0]["tons"], expected)
        self.assertEqual(raw_rows[0]["quantity"], 100)
