from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from accounts.models import log_audit
from catalog.models import Branch, Product


class BranchStock(models.Model):
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="stock_rows"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stock_rows"
    )
    quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("branch", "product")]
        ordering = ["branch__name", "product__name"]

    def __str__(self):
        return f"{self.branch} — {self.product.code}: {self.quantity}"


def adjust_stock(branch: Branch, product: Product, delta: int) -> BranchStock:
    stock, _ = BranchStock.objects.select_for_update().get_or_create(
        branch=branch, product=product, defaults={"quantity": 0}
    )
    stock.quantity += delta
    stock.save(update_fields=["quantity", "updated_at"])
    return stock


class Dispatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"

    reference = models.CharField(max_length=40, unique=True, blank=True)
    customer = models.ForeignKey(
        "catalog.Customer", on_delete=models.PROTECT, related_name="dispatches"
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="dispatches"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatches_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatches_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference or f"Dispatch #{self.pk}"

    def save(self, *args, **kwargs):
        if self.branch_id and self.customer_id and self.branch.customer_id != self.customer_id:
            raise ValidationError("Branch must belong to the selected customer.")
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"DSP-{self.pk:05d}"
            super().save(update_fields=["reference"])

    @transaction.atomic
    def approve(self, user=None):
        if self.status == self.Status.APPROVED:
            raise ValidationError("Dispatch is already approved.")
        if self.status == self.Status.CANCELLED:
            raise ValidationError("Cancelled dispatch cannot be approved.")
        items = list(self.items.select_related("product"))
        if not items:
            raise ValidationError("Dispatch has no items.")
        for item in items:
            adjust_stock(self.branch, item.product, item.quantity)
        self.status = self.Status.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(
            update_fields=["status", "approved_by", "approved_at", "updated_at"]
        )
        log_audit(
            actor=user,
            action="approve",
            entity="Dispatch",
            entity_id=self.pk,
            details=f"Approved {self.reference} to {self.branch}",
        )
        return self


class DispatchItem(models.Model):
    dispatch = models.ForeignKey(
        Dispatch, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        unique_together = [("dispatch", "product")]

    def __str__(self):
        return f"{self.product.code} x {self.quantity}"


class StockLoss(models.Model):
    """Shrinkage or damage that reduces branch stock (not a sale)."""

    class Reason(models.TextChoices):
        SHRINKAGE = "shrinkage", "Shrinkage"
        DAMAGE = "damage", "Damage"

    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="stock_losses"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="stock_losses"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    quantity = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_losses_recorded",
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at", "-pk"]

    def __str__(self):
        return f"{self.get_reason_display()} {self.product.code} x {self.quantity}"

    @transaction.atomic
    def apply(self, user=None):
        if self.pk:
            raise ValidationError("Stock loss already recorded.")
        if self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")
        self.recorded_by = user or self.recorded_by
        self.save()
        adjust_stock(self.branch, self.product, -self.quantity)
        log_audit(
            actor=user,
            action="record",
            entity="StockLoss",
            entity_id=self.pk,
            details=(
                f"{self.get_reason_display()} {self.quantity} of "
                f"{self.product.code} at {self.branch}"
            ),
        )
        return self
