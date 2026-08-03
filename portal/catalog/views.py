from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import log_audit
from accounts.roles import (
    accessible_branches,
    user_can_access_branch,
    user_can_manage_customers,
    user_is_admin,
)

from .forms import BranchForm, CustomerForm, ProductForm
from .models import Branch, Customer, Product


def _require_admin(request, message="Only IT can perform this action."):
    if not user_is_admin(request.user):
        messages.error(request, message)
        return False
    return True


def _require_customer_admin(request, message="Only the system admin can manage customers."):
    if not user_can_manage_customers(request.user):
        messages.error(request, message)
        return False
    return True


def _require_branch_access(request, branch, message="You do not have access to this branch."):
    if not user_can_access_branch(request.user, branch):
        messages.error(request, message)
        return False
    return True


@login_required
def customer_list(request):
    if not _require_customer_admin(request):
        return redirect("dashboard")
    customers = Customer.objects.all()
    return render(request, "catalog/customer_list.html", {"customers": customers})


@login_required
def customer_create(request):
    if not _require_customer_admin(request):
        return redirect("dashboard")
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Customer created.")
        return redirect("customer_list")
    return render(request, "catalog/customer_form.html", {"form": form, "title": "New customer"})


@login_required
def customer_edit(request, pk):
    if not _require_customer_admin(request):
        return redirect("dashboard")
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Customer updated.")
        return redirect("customer_list")
    return render(
        request,
        "catalog/customer_form.html",
        {"form": form, "title": f"Edit {customer.name}"},
    )


@login_required
def customer_detail(request, pk):
    if not _require_customer_admin(request):
        return redirect("dashboard")
    customer = get_object_or_404(Customer.objects.prefetch_related("branches"), pk=pk)
    return render(request, "catalog/customer_detail.html", {"customer": customer})


@login_required
@require_POST
def customer_delete(request, pk):
    if not _require_customer_admin(request, "Only the system admin can delete customers."):
        return redirect("dashboard")
    customer = get_object_or_404(Customer, pk=pk)
    name = customer.name
    try:
        customer.delete()
    except ProtectedError:
        messages.error(
            request,
            f"Cannot delete {name}: one or more branches still have dispatches or sales. "
            "Remove those records first, or delete branches individually after clearing history.",
        )
        return redirect("customer_detail", pk=pk)
    log_audit(
        actor=request.user,
        action="delete",
        entity="Customer",
        entity_id=pk,
        details=f"Deleted customer {name}",
    )
    messages.success(request, f"Customer {name} deleted.")
    return redirect("customer_list")


@login_required
def branch_list(request):
    branches = accessible_branches(request.user)
    return render(request, "catalog/branch_list.html", {"branches": branches})


@login_required
def branch_create(request):
    if not _require_admin(request, "Only IT can create branches."):
        return redirect("branch_list")
    form = BranchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Branch created.")
        return redirect("branch_list")
    return render(request, "catalog/branch_form.html", {"form": form, "title": "New branch"})


@login_required
def branch_detail(request, pk):
    branch = get_object_or_404(Branch.objects.select_related("customer"), pk=pk)
    if not _require_branch_access(request, branch):
        return redirect("branch_list")
    return render(request, "catalog/branch_detail.html", {"branch": branch})


@login_required
def branch_edit(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if not _require_branch_access(request, branch):
        return redirect("branch_list")
    if not user_is_admin(request.user):
        messages.error(request, "Only IT can edit branch details.")
        return redirect("branch_detail", pk=branch.pk)
    form = BranchForm(request.POST or None, instance=branch)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Branch updated.")
        return redirect("branch_detail", pk=branch.pk)
    return render(
        request,
        "catalog/branch_form.html",
        {"form": form, "title": f"Edit {branch.name}"},
    )


@login_required
@require_POST
def branch_regenerate_key(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if not _require_branch_access(request, branch):
        return redirect("branch_list")
    if not user_is_admin(request.user):
        messages.error(request, "Only IT can regenerate API keys.")
        return redirect("branch_detail", pk=branch.pk)
    branch.regenerate_api_key()
    messages.success(request, "API key regenerated.")
    return redirect("branch_detail", pk=branch.pk)


@login_required
@require_POST
def branch_delete(request, pk):
    if not _require_admin(request):
        return redirect("branch_detail", pk=pk)
    branch = get_object_or_404(Branch.objects.select_related("customer"), pk=pk)
    label = f"{branch.customer.name} / {branch.name}"
    try:
        branch.delete()
    except ProtectedError:
        messages.error(
            request,
            f"Cannot delete {label}: related dispatches or sales still exist. "
            "Remove those records first.",
        )
        return redirect("branch_detail", pk=pk)
    log_audit(
        actor=request.user,
        action="delete",
        entity="Branch",
        entity_id=pk,
        details=f"Deleted branch {label}",
    )
    messages.success(request, f"Branch {label} deleted.")
    return redirect("branch_list")


@login_required
def product_list(request):
    products = Product.objects.all()
    return render(request, "catalog/product_list.html", {"products": products})


@login_required
def product_create(request):
    if not _require_admin(request, "Only IT can create products."):
        return redirect("product_list")
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Product created.")
        return redirect("product_list")
    return render(request, "catalog/product_form.html", {"form": form, "title": "New product"})


@login_required
def product_edit(request, pk):
    if not _require_admin(request, "Only IT can edit products."):
        return redirect("product_list")
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Product updated.")
        return redirect("product_list")
    return render(
        request,
        "catalog/product_form.html",
        {"form": form, "title": f"Edit {product.name}"},
    )
