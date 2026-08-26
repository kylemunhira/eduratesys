from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from catalog.models import Branch, Customer
from sales.authentication import BranchAPIKeyAuthentication


class BranchApiKeyExpiryTests(TestCase):
    def setUp(self):
        customer = Customer.objects.create(name="Test Customer")
        self.branch = Branch.objects.create(
            customer=customer,
            name="Test Branch",
            api_key="test-expiry-key",
            api_key_valid_until=timezone.now() - timedelta(hours=1),
        )

    def test_expired_key_rejected_and_persisted(self):
        auth = BranchAPIKeyAuthentication()
        request = type("Req", (), {"META": {"HTTP_X_API_KEY": self.branch.api_key}})()

        with self.assertRaisesMessage(Exception, "API key expired"):
            auth.authenticate(request)

        self.branch.refresh_from_db()
        self.assertTrue(self.branch.api_key_expired)
        self.assertFalse(self.branch.is_online)

    def test_renew_keeps_same_key(self):
        new_until = timezone.now() + timedelta(days=30)
        old_key = self.branch.api_key
        self.branch.renew_api_key(new_until)

        self.branch.refresh_from_db()
        self.assertEqual(self.branch.api_key, old_key)
        self.assertEqual(self.branch.api_key_valid_until, new_until)
        self.assertFalse(self.branch.api_key_expired)
