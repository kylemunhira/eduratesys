import secrets

from django.db import models
from django.utils import timezone


class Customer(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=200, unique=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


class Branch(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="branches"
    )
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    gps_lat = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    gps_lng = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    manager = models.CharField(max_length=200, blank=True)
    api_key = models.CharField(max_length=64, unique=True, default=generate_api_key)
    api_key_valid_until = models.DateTimeField(null=True, blank=True)
    api_key_expired = models.BooleanField(default=False)
    machine_id = models.CharField(max_length=100, blank=True, default="")
    last_sync_at = models.DateTimeField(null=True, blank=True)
    is_online = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer__name", "name"]
        unique_together = [("customer", "name")]

    def __str__(self):
        return f"{self.customer.name} / {self.name}"

    @property
    def is_api_key_expired(self) -> bool:
        if self.api_key_valid_until is None:
            return False
        return timezone.now() >= self.api_key_valid_until

    def refresh_api_key_expiry(self):
        """Persist expired state when valid_until has passed."""
        if not self.is_api_key_expired:
            return False
        if self.api_key_expired and not self.is_online:
            return False
        self.api_key_expired = True
        self.is_online = False
        self.save(
            update_fields=["api_key_expired", "is_online", "updated_at"]
        )
        return True

    def mark_api_key_expired(self):
        """Called when sync is rejected because the API key has expired."""
        self.api_key_expired = True
        self.is_online = False
        self.save(
            update_fields=["api_key_expired", "is_online", "updated_at"]
        )

    def renew_api_key(self, valid_until):
        """Extend API key validity without changing the key value."""
        self.api_key_valid_until = valid_until
        self.api_key_expired = False
        self.save(
            update_fields=[
                "api_key_valid_until",
                "api_key_expired",
                "updated_at",
            ]
        )
        return self.api_key

    def regenerate_api_key(self):
        self.api_key = generate_api_key()
        self.api_key_expired = False
        self.save(
            update_fields=["api_key", "api_key_expired", "updated_at"]
        )
        return self.api_key

    def mark_synced(self):
        self.last_sync_at = timezone.now()
        self.is_online = True
        self.save(update_fields=["last_sync_at", "is_online", "updated_at"])


class Product(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    code = models.CharField(max_length=50, unique=True)
    barcode = models.CharField(max_length=64, blank=True, db_index=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=40, default="unit")
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    low_stock_threshold = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def effective_threshold(self, default_threshold: int) -> int:
        if self.low_stock_threshold is not None:
            return self.low_stock_threshold
        return default_threshold
