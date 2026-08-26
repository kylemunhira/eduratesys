from rest_framework import authentication, exceptions

from catalog.models import Branch


class BranchAPIKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate sync agents via X-API-Key header matching Branch.api_key."""

    header = "HTTP_X_API_KEY"

    def authenticate(self, request):
        key = request.META.get(self.header, "").strip()
        if not key:
            return None
        try:
            branch = Branch.objects.select_related("customer").get(api_key=key)
        except Branch.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed("Invalid API key") from exc
        if branch.is_api_key_expired:
            branch.mark_api_key_expired()
            raise exceptions.AuthenticationFailed("API key expired") from None
        request.branch = branch
        return (None, branch)
