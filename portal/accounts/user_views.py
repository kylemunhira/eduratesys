from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts.forms import UserCreateForm, UserEditForm
from accounts.models import log_audit
from accounts.roles import SYSTEM_ADMIN_USERNAME, is_system_admin_user, require_user_manager


@login_required
@require_user_manager
def user_list(request):
    users = (
        User.objects.select_related("profile")
        .prefetch_related("profile__branches", "profile__branches__customer")
        .exclude(username__iexact=SYSTEM_ADMIN_USERNAME)
        .order_by("username")
    )
    return render(request, "accounts/user_list.html", {"users": users})


@login_required
@require_user_manager
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        log_audit(
            actor=request.user,
            action="create",
            entity="User",
            entity_id=user.pk,
            details=f"Created user {user.username} as {user.profile.role}",
        )
        messages.success(request, f"User {user.username} created.")
        return redirect("user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "title": "New user"},
    )


@login_required
@require_user_manager
def user_edit(request, pk):
    user = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    if is_system_admin_user(user):
        raise Http404()
    form = UserEditForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(
            actor=request.user,
            action="update",
            entity="User",
            entity_id=user.pk,
            details=f"Updated user {user.username}",
        )
        messages.success(request, f"User {user.username} updated.")
        return redirect("user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "title": f"Edit {user.username}", "edit_user": user},
    )
