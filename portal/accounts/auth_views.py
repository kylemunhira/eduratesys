from django.contrib.auth import views as auth_views
from django.urls import reverse

from accounts.roles import is_system_admin_user


class PortalLoginView(auth_views.LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        if is_system_admin_user(self.request.user):
            return reverse("customer_list")
        return super().get_success_url()
