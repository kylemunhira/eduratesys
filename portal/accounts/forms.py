from django import forms
from django.contrib.auth.models import User

from accounts.models import UserProfile
from accounts.roles import (
    ALL_BRANCHES_ROLES,
    BRANCH_SCOPED_ROLES,
    ROLE_CHOICES,
    ROLE_SALES,
    SYSTEM_ADMIN_USERNAME,
    is_reserved_username,
)
from catalog.models import Branch


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=ROLE_SALES)
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.select_related("customer"),
        required=False,
        label="Customer",
        widget=forms.CheckboxSelectMultiple,
        help_text="Assign branches for Sales Admin, Sales, and Stockist users.",
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if is_reserved_username(username):
            raise forms.ValidationError(
                f"Username '{SYSTEM_ADMIN_USERNAME}' is reserved for the system admin."
            )
        return username

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return p2

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        branches = cleaned.get("branches")
        if role in BRANCH_SCOPED_ROLES and not branches:
            self.add_error(
                "branches",
                "Sales Admin, Sales, and Stockist users must be assigned at least one branch.",
            )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = self.cleaned_data["role"]
            profile.save()
            if profile.role in ALL_BRANCHES_ROLES:
                profile.branches.clear()
            else:
                profile.branches.set(self.cleaned_data.get("branches") or [])
        return user


class UserEditForm(forms.ModelForm):
    password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput,
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput,
        required=False,
    )
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.select_related("customer"),
        required=False,
        label="Customer",
        widget=forms.CheckboxSelectMultiple,
        help_text="Assign branches for Sales Admin, Sales, and Stockist users.",
    )
    is_active = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        profile = getattr(self.instance, "profile", None)
        if profile:
            self.fields["role"].initial = profile.role
            self.fields["branches"].initial = profile.branches.all()
        self.fields["is_active"].initial = self.instance.is_active

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if is_reserved_username(username) and not is_reserved_username(
            self.instance.username
        ):
            raise forms.ValidationError(
                f"Username '{SYSTEM_ADMIN_USERNAME}' is reserved for the system admin."
            )
        return username

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 or p2:
            if p1 != p2:
                raise forms.ValidationError("Passwords do not match.")
        return p2

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        branches = cleaned.get("branches")
        if role in BRANCH_SCOPED_ROLES and not branches:
            self.add_error(
                "branches",
                "Sales Admin, Sales, and Stockist users must be assigned at least one branch.",
            )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        user.is_active = self.cleaned_data.get("is_active", True)
        if commit:
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = self.cleaned_data["role"]
            profile.save()
            if profile.role in ALL_BRANCHES_ROLES:
                profile.branches.clear()
            else:
                profile.branches.set(self.cleaned_data.get("branches") or [])
        return user
