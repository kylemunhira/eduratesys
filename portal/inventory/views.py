from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.forms import DispatchForm, DispatchItemFormSet
from catalog.models import Branch

from .models import BranchStock, Dispatch


@login_required
def stock_list(request):
    stocks = BranchStock.objects.select_related(
        "branch", "branch__customer", "product"
    )
    branch_id = request.GET.get("branch")
    if branch_id:
        stocks = stocks.filter(branch_id=branch_id)
    return render(
        request,
        "inventory/stock_list.html",
        {
            "stocks": stocks,
            "branches": Branch.objects.select_related("customer"),
            "selected_branch": branch_id,
        },
    )


@login_required
def dispatch_list(request):
    dispatches = Dispatch.objects.select_related("customer", "branch")
    return render(request, "inventory/dispatch_list.html", {"dispatches": dispatches})


@login_required
def dispatch_detail(request, pk):
    dispatch = get_object_or_404(
        Dispatch.objects.select_related("customer", "branch").prefetch_related(
            "items__product"
        ),
        pk=pk,
    )
    return render(request, "inventory/dispatch_detail.html", {"dispatch": dispatch})


@login_required
@transaction.atomic
def dispatch_create(request):
    form = DispatchForm(request.POST or None)
    formset = DispatchItemFormSet(request.POST or None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        dispatch = form.save(commit=False)
        dispatch.created_by = request.user
        dispatch.save()
        formset.instance = dispatch
        formset.save()
        messages.success(request, f"Draft dispatch {dispatch.reference} created.")
        return redirect("dispatch_detail", pk=dispatch.pk)
    return render(
        request,
        "inventory/dispatch_form.html",
        {"form": form, "formset": formset, "title": "New dispatch"},
    )


@login_required
@require_POST
def dispatch_approve(request, pk):
    dispatch = get_object_or_404(Dispatch, pk=pk)
    try:
        dispatch.approve(user=request.user)
        messages.success(request, f"{dispatch.reference} approved; stock updated.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    return redirect("dispatch_detail", pk=dispatch.pk)
