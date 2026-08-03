from django.shortcuts import redirect

from accounts.roles import is_system_admin_user


class SystemAdminCustomersOnlyMiddleware:
    """Restrict ZImhope to customer pages (plus auth/static)."""

    ALLOWED_PREFIXES = (
        "/customers",
        "/login",
        "/logout",
        "/static",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and is_system_admin_user(user):
            path = request.path
            allowed = any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in self.ALLOWED_PREFIXES
            )
            if not allowed:
                return redirect("customer_list")
        return self.get_response(request)
