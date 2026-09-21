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


def set_stock(branch: Branch, product: Product, quantity: int) -> BranchStock:
    """Set absolute on-hand quantity (e.g. POS remaining qty sync)."""
    stock, _ = BranchStock.objects.select_for_update().get_or_create(
        branch=branch, product=product, defaults={"quantity": quantity}
    )
    if stock.quantity != quantity:
        stock.quantity = quantity
        stock.save(update_fields=["quantity", "updated_at"])
    return stock


@transaction.atomic
def ingest_stock_snapshot(*, branch: Branch, items: list[dict]) -> dict:
    """
    Apply POS remaining quantities to branch stock.
    Each item: {barcode, quantity}. Matched by product barcode only.
    """
    from sales.models import SyncLog

    updated = []
    skipped = []
    for line in items:
        barcode = (line.get("barcode") or "").strip()
        if not barcode:
            skipped.append({"reason": "missing_barcode", "line": line})
            continue
        try:
            qty = int(line.get("quantity"))
        except (TypeError, ValueError):
            skipped.append({"barcode": barcode, "reason": "invalid_quantity"})
            continue
        if qty < 0:
            skipped.append({"barcode": barcode, "reason": "negative_quantity"})
            continue
        product = Product.objects.filter(
            barcode=barcode, status=Product.Status.ACTIVE
        ).first()
        if not product:
            skipped.append({"barcode": barcode, "reason": "no_portal_match"})
            continue
        stock = set_stock(branch, product, qty)
        updated.append(
            {
                "barcode": barcode,
                "product_code": product.code,
                "quantity": stock.quantity,
            }
        )

    branch.mark_synced()
    SyncLog.objects.create(
        branch=branch,
        status=SyncLog.Status.SUCCESS,
        message=(
            f"Stock snapshot: {len(updated)} updated, {len(skipped)} skipped"
        ),
        external_sale_id="",
        payload_meta={
            "kind": "stock_snapshot",
            "updated_count": len(updated),
            "skipped_count": len(skipped),
            "updated": updated,
            "skipped": skipped,
        },
    )
    if updated:
        log_audit(
            action="sync",
            entity="BranchStock",
            entity_id=branch.pk,
            details=f"POS stock snapshot for {branch}: {len(updated)} products",
        )
    return {"updated": updated, "skipped": skipped}


class Dispatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GIT = "git", "GIT (Good in transit)"
        APPROVED = "approved", "Received"
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
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatches_sent",
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
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
    def send_to_git(self, user=None):
        """Sales Admin confirms details → Good In Transit (no stock change)."""
        if self.status == self.Status.GIT:
            raise ValidationError("Dispatch is already in transit (GIT).")
        if self.status == self.Status.APPROVED:
            raise ValidationError("Received dispatch cannot be sent to GIT.")
        if self.status == self.Status.CANCELLED:
            raise ValidationError("Cancelled dispatch cannot be sent to GIT.")
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only draft dispatches can be sent to GIT.")
        items = list(self.items.all())
        if not items:
            raise ValidationError("Dispatch has no items.")
        self.status = self.Status.GIT
        self.dispatched_by = user
        self.dispatched_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "dispatched_by",
                "dispatched_at",
                "updated_at",
            ]
        )
        log_audit(
            actor=user,
            action="dispatch_git",
            entity="Dispatch",
            entity_id=self.pk,
            details=f"Sent {self.reference} to GIT for {self.branch}",
        )
        return self

    @transaction.atomic
    def receive(self, user=None):
        """Stockist confirms delivery note → Received and branch stock updated."""
        if self.status == self.Status.APPROVED:
            raise ValidationError("Dispatch is already received.")
        if self.status == self.Status.CANCELLED:
            raise ValidationError("Cancelled dispatch cannot be received.")
        if self.status != self.Status.GIT:
            raise ValidationError(
                "Only GIT (good in transit) dispatches can be received."
            )
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
            action="receive",
            entity="Dispatch",
            entity_id=self.pk,
            details=f"Received {self.reference} at {self.branch}; stock updated",
        )
        return self

    def approve(self, user=None):
        """Backward-compatible alias for receive()."""
        return self.receive(user=user)


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
