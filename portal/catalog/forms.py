from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from catalog.models import Branch, Customer, Product
from inventory.models import Dispatch, DispatchItem, StockLoss


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ("name", "phone", "email", "address", "status", "notes")


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = (
            "customer",
            "name",
            "address",
            "manager",
            "machine_id",
            "gps_lat",
            "gps_lng",
        )


class BranchApiKeyRenewForm(forms.Form):
    valid_until = forms.DateTimeField(
        label="Valid until",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ],
    )

    def clean_valid_until(self):
        valid_until = self.cleaned_data["valid_until"]
        if timezone.is_naive(valid_until):
            valid_until = timezone.make_aware(
                valid_until, timezone.get_current_timezone()
            )
        if valid_until <= timezone.now():
            raise forms.ValidationError("Valid until must be in the future.")
        return valid_until


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = (
            "code",
            "barcode",
            "name",
            "category",
            "unit",
            "selling_price",
            "cost_price",
            "low_stock_threshold",
            "status",
        )


class DispatchForm(forms.ModelForm):
    class Meta:
        model = Dispatch
        fields = ("customer", "branch", "notes")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.roles import accessible_branches

        allowed = accessible_branches(user) if user else Branch.objects.select_related(
            "customer"
        )
        self.fields["branch"].queryset = allowed
        customer_ids = allowed.values_list("customer_id", flat=True).distinct()
        self.fields["customer"].queryset = Customer.objects.filter(pk__in=customer_ids)
        if "customer" in self.data:
            try:
                customer_id = int(self.data.get("customer"))
                self.fields["branch"].queryset = allowed.filter(customer_id=customer_id)
            except (TypeError, ValueError):
                pass
        elif self.instance.pk:
            self.fields["branch"].queryset = allowed.filter(
                customer=self.instance.customer
            )


DispatchItemFormSet = inlineformset_factory(
    Dispatch,
    DispatchItem,
    fields=("product", "quantity"),
    extra=10,
    can_delete=True,
)


class StockLossForm(forms.ModelForm):
    class Meta:
        model = StockLoss
        fields = ("branch", "product", "reason", "quantity", "notes", "recorded_at")
        widgets = {
            "recorded_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = Branch.objects.select_related("customer")
        self.fields["product"].queryset = Product.objects.filter(
            status=Product.Status.ACTIVE
        )
        self.fields["recorded_at"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
