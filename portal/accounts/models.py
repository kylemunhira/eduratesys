from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    entity = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=64, blank=True)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.entity} #{self.entity_id}"


def log_audit(*, actor=None, action: str, entity: str, entity_id="", details=""):
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        details=details,
    )
