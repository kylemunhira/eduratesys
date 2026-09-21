from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.roles import (
    accessible_branches,
    filter_by_accessible_branches,
    require_dispatch_creator,
    require_dispatch_receiver,
    user_can_access_branch,
    user_can_create_dispatch,
    user_can_receive_dispatch,
)
from catalog.forms import (
    DispatchForm,
    DispatchItemFormSet,
    DispatchUploadForm,
)
from catalog.models import Branch, Product
from reports.period import parse_period_bounds
from reports.views import _tons, pack_size_kg, closing_stock_quantities

from .excel_dispatch import build_dispatch_template, parse_dispatch_excel
from .models import BranchStock, Dispatch, DispatchItem

UPLOAD_SESSION_KEY = "dispatch_upload_preview"


@login_required
def stock_list(request):
    """Closing branch stock as of date_to, with metric tons and stock value."""
    bounds = parse_period_bounds(request, default_period="today")
    branches = accessible_branches(request.user)
    branch_id = request.GET.get("branch") or ""
    bid = None
    if branch_id:
        if not user_can_access_branch(
            request.user, int(branch_id) if branch_id.isdigit() else None
        ):
            messages.error(request, "You do not have access to this branch.")
            return redirect("stock_list")
        bid = int(branch_id) if branch_id.isdigit() else None

    closing = closing_stock_quantities(request.user, bounds["end_dt"], branch_id=bid)
    product_ids = {pid for _, pid in closing}
    branch_ids = {b for b, _ in closing}
    products = {p.pk: p for p in Product.objects.filter(pk__in=product_ids)}
    branch_map = {
        b.pk: b
        for b in Branch.objects.select_related("customer").filter(pk__in=branch_ids)
    }

    live_updated = {
        (row.branch_id, row.product_id): row.updated_at
        for row in filter_by_accessible_branches(
            BranchStock.objects.filter(
                branch_id__in=branch_ids, product_id__in=product_ids
            ),
            request.user,
        )
    }

    money = Decimal("0.01")
    stocks = []
    for (b_id, p_id), qty in closing.items():
        product = products.get(p_id)
        branch = branch_map.get(b_id)
        if not product or not branch:
            continue
        unit_price = Decimal(product.selling_price or 0)
        stocks.append(
            {
                "customer_name": branch.customer.name,
                "branch_name": branch.name,
                "product_code": product.code,
                "product_name": product.name,
                "quantity": qty,
                "tons": _tons(qty, pack_size_kg(product.name)),
                "stock_value": (Decimal(qty) * unit_price).quantize(
                    money, rounding=ROUND_HALF_UP
                ),
                "updated_at": live_updated.get((b_id, p_id)),
            }
        )

    stocks.sort(
        key=lambda r: (
            r["customer_name"].lower(),
            r["branch_name"].lower(),
            r["product_code"] or "",
            r["product_name"].lower(),
        )
    )

    return render(
        request,
        "inventory/stock_list.html",
        {
            "stocks": stocks,
            "branches": branches,
            "selected_branch": branch_id,
            "period": bounds["period"],
            "date_from": bounds["date_from_iso"],
            "date_to": bounds["date_to_iso"],
            "date_to_display": bounds["date_to"],
        },
    )


@login_required
def dispatch_list(request):
    dispatches = filter_by_accessible_branches(
        Dispatch.objects.select_related("customer", "branch", "created_by"),
        request.user,
    )
    status = (request.GET.get("status") or "").lower()
    # Stockists default to in-transit deliveries awaiting receipt.
    if not status and user_can_receive_dispatch(request.user) and not user_can_create_dispatch(
        request.user
    ):
        status = Dispatch.Status.GIT
    if status in {c.value for c in Dispatch.Status}:
        dispatches = dispatches.filter(status=status)
    return render(
        request,
        "inventory/dispatch_list.html",
        {
            "dispatches": dispatches,
            "status": status,
            "status_choices": Dispatch.Status.choices,
            "can_create_dispatch": user_can_create_dispatch(request.user),
            "can_receive_dispatch": user_can_receive_dispatch(request.user),
        },
    )


@login_required
def dispatch_detail(request, pk):
    dispatch = get_object_or_404(
        Dispatch.objects.select_related(
            "customer", "branch", "created_by", "dispatched_by", "approved_by"
        ).prefetch_related("items__product"),
        pk=pk,
    )
    if not user_can_access_branch(request.user, dispatch.branch):
        messages.error(request, "You do not have access to this dispatch.")
        return redirect("dispatch_list")
    can_edit = (
        user_can_create_dispatch(request.user)
        and dispatch.status == Dispatch.Status.DRAFT
    )
    can_send_git = can_edit and dispatch.items.exists()
    can_receive = (
        user_can_receive_dispatch(request.user)
        and dispatch.status == Dispatch.Status.GIT
    )
    return render(
        request,
        "inventory/dispatch_detail.html",
        {
            "dispatch": dispatch,
            "can_edit": can_edit,
            "can_send_git": can_send_git,
            "can_receive": can_receive,
        },
    )


@login_required
@require_dispatch_creator
@transaction.atomic
def dispatch_create(request):
    form = DispatchForm(request.POST or None, user=request.user)
    formset = DispatchItemFormSet(request.POST or None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        dispatch = form.save(commit=False)
        if not user_can_access_branch(request.user, dispatch.branch):
            messages.error(request, "You do not have access to that branch.")
            return redirect("dispatch_list")
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
@require_dispatch_creator
@transaction.atomic
def dispatch_edit(request, pk):
    dispatch = get_object_or_404(Dispatch, pk=pk)
    if not user_can_access_branch(request.user, dispatch.branch):
        messages.error(request, "You do not have access to this dispatch.")
        return redirect("dispatch_list")
    if dispatch.status != Dispatch.Status.DRAFT:
        messages.error(request, "Only draft dispatches can be edited.")
        return redirect("dispatch_detail", pk=dispatch.pk)

    form = DispatchForm(request.POST or None, instance=dispatch, user=request.user)
    formset = DispatchItemFormSet(request.POST or None, instance=dispatch)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        updated = form.save(commit=False)
        if not user_can_access_branch(request.user, updated.branch):
            messages.error(request, "You do not have access to that branch.")
            return redirect("dispatch_list")
        updated.save()
        formset.save()
        messages.success(request, f"Draft {dispatch.reference} updated.")
        return redirect("dispatch_detail", pk=dispatch.pk)
    return render(
        request,
        "inventory/dispatch_form.html",
        {
            "form": form,
            "formset": formset,
            "title": f"Edit {dispatch.reference}",
            "dispatch": dispatch,
        },
    )


@login_required
@require_dispatch_creator
def dispatch_upload_template(request):
    """Download the Excel template used for dispatch product uploads."""
    content = build_dispatch_template()
    resp = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="dispatch_upload_template.xlsx"'
    return resp


@login_required
@require_dispatch_creator
def dispatch_upload(request):
    form = DispatchUploadForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        branch = form.cleaned_data["branch"]
        if not user_can_access_branch(request.user, branch):
            messages.error(request, "You do not have access to that branch.")
            return redirect("dispatch_list")
        try:
            parsed = parse_dispatch_excel(form.cleaned_data["excel_file"])
        except Exception as exc:
            messages.error(request, f"Could not read Excel file: {exc}")
            return render(
                request,
                "inventory/dispatch_upload.html",
                {"form": form},
            )
        if parsed["matched_count"] == 0:
            messages.error(
                request,
                "No matching products found. Check product codes and try again.",
            )
            return render(
                request,
                "inventory/dispatch_upload.html",
                {"form": form, "parse_errors": parsed["lines"]},
            )
        request.session[UPLOAD_SESSION_KEY] = {
            "customer_id": form.cleaned_data["customer"].pk,
            "branch_id": branch.pk,
            "notes": form.cleaned_data.get("notes") or "",
            "lines": parsed["lines"],
        }
        return redirect("dispatch_upload_preview")
    return render(request, "inventory/dispatch_upload.html", {"form": form})


@login_required
@require_dispatch_creator
def dispatch_upload_preview(request):
    payload = request.session.get(UPLOAD_SESSION_KEY)
    if not payload:
        messages.error(request, "Upload an Excel file first.")
        return redirect("dispatch_upload")

    branch = get_object_or_404(
        Branch.objects.select_related("customer"), pk=payload["branch_id"]
    )
    if not user_can_access_branch(request.user, branch):
        messages.error(request, "You do not have access to that branch.")
        request.session.pop(UPLOAD_SESSION_KEY, None)
        return redirect("dispatch_list")

    lines = payload.get("lines") or []
    matched = [line for line in lines if line.get("matched")]
    errors = [line for line in lines if not line.get("matched")]

    if request.method == "POST":
        action = request.POST.get("action") or "create"
        if action == "cancel":
            request.session.pop(UPLOAD_SESSION_KEY, None)
            return redirect("dispatch_upload")

        # Allow qty edits from preview form before creating the draft.
        updated_matched = []
        for line in matched:
            pid = str(line["product_id"])
            raw = request.POST.get(f"qty_{pid}", str(line["quantity"]))
            try:
                qty = int(raw)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            updated = dict(line)
            updated["quantity"] = qty
            updated_matched.append(updated)

        if not updated_matched:
            messages.error(request, "Add at least one product with quantity > 0.")
            return render(
                request,
                "inventory/dispatch_upload_preview.html",
                {
                    "branch": branch,
                    "customer": branch.customer,
                    "notes": payload.get("notes") or "",
                    "matched": matched,
                    "errors": errors,
                },
            )

        with transaction.atomic():
            dispatch = Dispatch.objects.create(
                customer=branch.customer,
                branch=branch,
                notes=payload.get("notes") or "",
                created_by=request.user,
            )
            DispatchItem.objects.bulk_create(
                [
                    DispatchItem(
                        dispatch=dispatch,
                        product_id=line["product_id"],
                        quantity=line["quantity"],
                    )
                    for line in updated_matched
                ]
            )
        request.session.pop(UPLOAD_SESSION_KEY, None)
        messages.success(
            request,
            f"Draft {dispatch.reference} created with {len(updated_matched)} products. "
            "Review details, then send to GIT when ready.",
        )
        return redirect("dispatch_detail", pk=dispatch.pk)

    return render(
        request,
        "inventory/dispatch_upload_preview.html",
        {
            "branch": branch,
            "customer": branch.customer,
            "notes": payload.get("notes") or "",
            "matched": matched,
            "errors": errors,
        },
    )


@login_required
@require_dispatch_creator
@require_POST
def dispatch_send_git(request, pk):
    dispatch = get_object_or_404(Dispatch, pk=pk)
    if not user_can_access_branch(request.user, dispatch.branch):
        messages.error(request, "You do not have access to this dispatch.")
        return redirect("dispatch_list")
    try:
        dispatch.send_to_git(user=request.user)
        messages.success(
            request,
            f"{dispatch.reference} is now GIT (good in transit). "
            "Stockists can confirm receipt for this branch.",
        )
    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
        )
    return redirect("dispatch_detail", pk=dispatch.pk)


@login_required
@require_dispatch_receiver
@require_POST
def dispatch_receive(request, pk):
    dispatch = get_object_or_404(Dispatch, pk=pk)
    if not user_can_access_branch(request.user, dispatch.branch):
        messages.error(request, "You do not have access to this dispatch.")
        return redirect("dispatch_list")
    try:
        dispatch.receive(user=request.user)
        messages.success(
            request,
            f"{dispatch.reference} received; branch stock updated. "
            "It now appears in Dispatch warehouse.",
        )
    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
        )
    return redirect("dispatch_detail", pk=dispatch.pk)


# Legacy URL name used by older templates / bookmarks.
@login_required
@require_POST
def dispatch_approve(request, pk):
    """Redirect legacy approve URL to the receive action."""
    return dispatch_receive(request, pk)
