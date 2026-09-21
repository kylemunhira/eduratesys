from django.conf import settings
from django.db import models

from accounts.roles import ROLE_CHOICES, ROLE_IT, ROLE_SALES, sync_user_groups


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


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(max_length=40, choices=ROLE_CHOICES, default=ROLE_SALES)
    branches = models.ManyToManyField(
        "catalog.Branch",
        blank=True,
        related_name="portal_users",
        help_text="Required for Sales Admin, Sales, and Stockist. Ignored for org-wide roles.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        sync_user_groups(self.user, self.role)
        if self.role == ROLE_IT and not self.user.is_staff:
            type(self.user).objects.filter(pk=self.user_id).update(is_staff=True)
