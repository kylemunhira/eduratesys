"""Role helpers for SSMS portal users."""

from django.contrib.auth.models import Group

ADMIN_GROUP = "Admin"
SUPPLIER_GROUP = "Supplier"


def ensure_role_groups():
    Group.objects.get_or_create(name=ADMIN_GROUP)
    Group.objects.get_or_create(name=SUPPLIER_GROUP)


def user_is_admin(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=ADMIN_GROUP).exists()


def user_is_supplier(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user_is_admin(user) or user.groups.filter(name=SUPPLIER_GROUP).exists()
