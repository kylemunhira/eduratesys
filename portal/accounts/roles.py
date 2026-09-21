"""Role helpers and branch scoping for SSMS portal users."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import Group
from django.shortcuts import redirect

ROLE_IT = "IT"
ROLE_BUSINESS_HEAD = "Business Head"
ROLE_COMMERCIAL_MANAGER = "Commercial Manager"
ROLE_SALES_ADMIN = "Sales Admin"
ROLE_SALES = "Sales"
ROLE_STOCKIST = "Stockist"

# Hidden system admin: full portal access including Customers; never listed under Users.
SYSTEM_ADMIN_USERNAME = "ZImhope"
SYSTEM_ADMIN_LABEL = "System Admin"

ROLE_CHOICES = (
    (ROLE_IT, "IT"),
    (ROLE_BUSINESS_HEAD, "Business Head"),
    (ROLE_COMMERCIAL_MANAGER, "Commercial Manager"),
    (ROLE_SALES_ADMIN, "Sales Admin"),
    (ROLE_SALES, "Sales"),
    (ROLE_STOCKIST, "Stockist"),
)

# Org-wide: see every branch. Sales / Stockist roles are limited to assigned branches.
ALL_BRANCHES_ROLES = frozenset(
    {ROLE_IT, ROLE_BUSINESS_HEAD, ROLE_COMMERCIAL_MANAGER}
)

# Branch-scoped portal operators (must be assigned at least one branch).
BRANCH_SCOPED_ROLES = frozenset(
    {ROLE_SALES_ADMIN, ROLE_SALES, ROLE_STOCKIST}
)

# Legacy group names kept for seed / existing DBs.
ADMIN_GROUP = "Admin"
SUPPLIER_GROUP = "Supplier"


def ensure_role_groups():
    for name, _ in ROLE_CHOICES:
        Group.objects.get_or_create(name=name)
    Group.objects.get_or_create(name=ADMIN_GROUP)
    Group.objects.get_or_create(name=SUPPLIER_GROUP)


def get_profile(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "profile", None)


def is_system_admin_user(user) -> bool:
    """ZImhope: full portal access including Customers; hidden from the Users list."""
    if not user or not user.is_authenticated:
        return False
    return (user.username or "").lower() == SYSTEM_ADMIN_USERNAME.lower()


def is_reserved_username(username: str) -> bool:
    return (username or "").lower() == SYSTEM_ADMIN_USERNAME.lower()


def user_role(user) -> str | None:
    if is_system_admin_user(user):
        return SYSTEM_ADMIN_LABEL
    profile = get_profile(user)
    if profile and profile.role:
        return profile.role
    if user and user.is_authenticated and user.is_superuser:
        return ROLE_IT
    return None


def user_can_manage_users(user) -> bool:
    """IT, superuser, or ZImhope may create and edit portal users."""
    if not user or not user.is_authenticated:
        return False
    if is_system_admin_user(user):
        return True
    if user.is_superuser:
        return True
    return user_role(user) == ROLE_IT


def user_can_manage_customers(user) -> bool:
    """Only ZImhope (system admin) may manage customers.

    IT and other portal roles must not see or access the Customers menu/pages.
    """
    return is_system_admin_user(user)


def user_can_create_branches(user) -> bool:
    """Only ZImhope (system admin) may create branches."""
    return is_system_admin_user(user)


def user_can_view_branch_api_keys(user) -> bool:
    """Only ZImhope (system admin) may view, renew, or regenerate branch API keys."""
    return is_system_admin_user(user)


def user_can_delete_branches(user) -> bool:
    """Only ZImhope (system admin) may delete branches."""
    return is_system_admin_user(user)


def user_is_admin(user) -> bool:
    """Full catalog admin (branch/product create & edit). Maps to IT / ZImhope."""
    if not user or not user.is_authenticated:
        return False
    if is_system_admin_user(user):
        return True
    if user.is_superuser:
        return True
    role = user_role(user)
    if role == ROLE_IT:
        return True
    # Legacy: Admin Django group without a profile yet
    return user.groups.filter(name=ADMIN_GROUP).exists()


def user_sees_all_branches(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if is_system_admin_user(user):
        return True
    if user.is_superuser or user_is_admin(user):
        return True
    role = user_role(user)
    if role in ALL_BRANCHES_ROLES:
        return True
    # Legacy Supplier group without profile → treat as scoped (no all-access)
    return False


def user_is_supplier(user) -> bool:
    """Any authenticated portal operator (non-anonymous)."""
    if not user or not user.is_authenticated:
        return False
    if user_is_admin(user):
        return True
    role = user_role(user)
    if role:
        return True
    return user.groups.filter(name=SUPPLIER_GROUP).exists()


def accessible_branches(user):
    from catalog.models import Branch

    if not user or not user.is_authenticated:
        return Branch.objects.none()
    qs = Branch.objects.select_related("customer")
    if user_sees_all_branches(user):
        return qs
    profile = get_profile(user)
    if not profile:
        return Branch.objects.none()
    return qs.filter(pk__in=profile.branches.values_list("pk", flat=True))


def accessible_branch_ids(user):
    if user_sees_all_branches(user):
        return None  # None = no filter (all)
    return list(accessible_branches(user).values_list("pk", flat=True))


def user_can_access_branch(user, branch) -> bool:
    if user_sees_all_branches(user):
        return True
    if branch is None:
        return False
    branch_id = branch.pk if hasattr(branch, "pk") else branch
    ids = accessible_branch_ids(user)
    return ids is not None and branch_id in ids


def filter_by_accessible_branches(qs, user, field="branch_id"):
    """Restrict a queryset to the user's branches. No-op when user sees all."""
    ids = accessible_branch_ids(user)
    if ids is None:
        return qs
    return qs.filter(**{f"{field}__in": ids})


def sync_user_groups(user, role: str):
    """Keep Django Groups aligned with the profile role."""
    ensure_role_groups()
    role_names = {name for name, _ in ROLE_CHOICES}
    existing = list(
        user.groups.filter(name__in=role_names | {ADMIN_GROUP, SUPPLIER_GROUP})
    )
    if existing:
        user.groups.remove(*existing)
    group = Group.objects.get(name=role)
    user.groups.add(group)
    if role == ROLE_IT:
        user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        if not user.is_staff:
            type(user).objects.filter(pk=user.pk).update(is_staff=True)
    elif role in (ROLE_SALES_ADMIN, ROLE_SALES, ROLE_STOCKIST):
        user.groups.add(Group.objects.get(name=SUPPLIER_GROUP))


def user_can_create_dispatch(user) -> bool:
    """Sales Admin (supra) may upload / create / edit drafts and send to GIT."""
    if not user or not user.is_authenticated:
        return False
    if is_system_admin_user(user) or user.is_superuser:
        return True
    return user_role(user) == ROLE_SALES_ADMIN


def user_can_receive_dispatch(user) -> bool:
    """Stockists confirm receipt (GIT → received) for their branch."""
    if not user or not user.is_authenticated:
        return False
    if is_system_admin_user(user) or user.is_superuser:
        return True
    return user_role(user) == ROLE_STOCKIST


def require_dispatch_creator(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_can_create_dispatch(request.user):
            messages.error(
                request, "Only Sales Admin can create or dispatch products."
            )
            return redirect("dispatch_list")
        return view_func(request, *args, **kwargs)

    return _wrapped


def require_dispatch_receiver(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_can_receive_dispatch(request.user):
            messages.error(
                request, "Only Stockists can confirm receipt of dispatches."
            )
            return redirect("dispatch_list")
        return view_func(request, *args, **kwargs)

    return _wrapped


def require_user_manager(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_can_manage_users(request.user):
            messages.error(request, "Only IT can manage users.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)

    return _wrapped
