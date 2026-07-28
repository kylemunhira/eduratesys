from django.db import models, transaction
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
import json

from accounts.models import log_audit
from catalog.models import Branch, Product
class Sale(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="sales")
    external_sale_id = models.CharField(max_length=100)
    sold_at = models.DateTimeField(default=timezone.now)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    received_at = models.DateTimeField(auto_now_add=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-sold_at"]
        unique_together = [("branch", "external_sale_id")]

    def __str__(self):
        return f"{self.branch} sale {self.external_sale_id}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.product.code} x {self.quantity}"


class SyncLog(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        DUPLICATE = "duplicate", "Duplicate"
        FAILED = "failed", "Failed"

    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="sync_logs"
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    message = models.TextField(blank=True)
    external_sale_id = models.CharField(max_length=100, blank=True)
    payload_meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.branch} {self.status} @ {self.created_at}"


def _json_safe(data: dict) -> dict:
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


@transaction.atomic
def ingest_sale(*, branch: Branch, data: dict) -> tuple[Sale | None, SyncLog, bool]:
    """
    Ingest a sale payload. Returns (sale_or_none, sync_log, created).
    Idempotent on (branch, external_sale_id).
    """
    external_sale_id = str(data.get("external_sale_id") or "").strip()
    if not external_sale_id:
        log = SyncLog.objects.create(
            branch=branch,
            status=SyncLog.Status.FAILED,
            message="Missing external_sale_id",
            payload_meta={"keys": list(data.keys())},
        )
        return None, log, False

    if Sale.objects.filter(branch=branch, external_sale_id=external_sale_id).exists():
        log = SyncLog.objects.create(
            branch=branch,
            status=SyncLog.Status.DUPLICATE,
            message="Sale already synced",
            external_sale_id=external_sale_id,
            payload_meta={"external_sale_id": external_sale_id},
        )
        branch.mark_synced()
        return (
            Sale.objects.get(branch=branch, external_sale_id=external_sale_id),
            log,
            False,
        )

    items = data.get("items") or []
    if not items:
        log = SyncLog.objects.create(
            branch=branch,
            status=SyncLog.Status.FAILED,
            message="Sale has no items",
            external_sale_id=external_sale_id,
        )
        return None, log, False

    resolved = []
    skipped = []
    for line in items:
        barcode = (line.get("barcode") or "").strip()
        if not barcode:
            skipped.append({"reason": "missing_barcode", "line": line})
            continue
        qty = int(line.get("quantity") or 0)
        if qty <= 0:
            skipped.append({"barcode": barcode, "reason": "invalid_quantity"})
            continue
        product = Product.objects.filter(
            barcode=barcode, status=Product.Status.ACTIVE
        ).first()
        if not product:
            skipped.append({"barcode": barcode, "reason": "no_portal_match"})
            continue
        resolved.append(
            (
                product,
                qty,
                line.get("unit_price", product.selling_price),
            )
        )

    sold_at = data.get("sold_at") or timezone.now()
    sale = Sale.objects.create(
        branch=branch,
        external_sale_id=external_sale_id,
        sold_at=sold_at,
        total_amount=data.get("total_amount") or 0,
        raw_payload=_json_safe(data),
    )
    total = 0
    for product, qty, unit_price in resolved:
        SaleItem.objects.create(
            sale=sale, product=product, quantity=qty, unit_price=unit_price
        )
        # Branch on-hand comes from POS remaining-qty stock snapshots, not sale deltas.
        total += float(unit_price) * qty
    if not data.get("total_amount"):
        sale.total_amount = total
        sale.save(update_fields=["total_amount"])

    branch.mark_synced()
    if resolved:
        message = "Sale ingested"
        if skipped:
            message = f"Sale ingested ({len(resolved)} items; {len(skipped)} skipped)"
    else:
        message = "No portal barcode matches; stock not updated"
    log = SyncLog.objects.create(
        branch=branch,
        status=SyncLog.Status.SUCCESS,
        message=message,
        external_sale_id=external_sale_id,
        payload_meta={
            "item_count": len(resolved),
            "skipped_count": len(skipped),
            "skipped": skipped,
        },
    )
    if resolved:
        log_audit(
            action="ingest",
            entity="Sale",
            entity_id=sale.pk,
            details=f"Synced {external_sale_id} from {branch}",
        )
    return sale, log, bool(resolved)
