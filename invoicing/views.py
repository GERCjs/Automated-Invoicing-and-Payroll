from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import role_required
from accounts.roles import ADMIN, FINANCE
from core.audit import get_client_ip, log_event

from .exports import generate_invoice_excel, generate_invoice_pdf
from .forms import InvoiceForm, InvoiceItemFormSet
from .models import Invoice
from .services import (
    apply_overdue_status,
    generate_invoice_number,
    mark_invoice_viewed,
    recalculate_invoice_totals,
    refresh_overdue_invoices,
    transition_invoice_status,
)


@login_required
@role_required(ADMIN, FINANCE)
def invoice_list(request):
    updated_count = refresh_overdue_invoices()
    invoices = Invoice.objects.select_related("customer").all()
    log_event(
        action="invoice.list.viewed",
        user=request.user,
        metadata={"path": request.path, "overdue_updates": updated_count},
        ip_address=get_client_ip(request),
    )
    return render(request, "invoicing/invoice_list.html", {"invoices": invoices})


@login_required
@role_required(ADMIN, FINANCE)
def invoice_create(request):
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        formset = InvoiceItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                invoice = form.save(commit=False)
                invoice.invoice_number = generate_invoice_number()
                invoice.status = Invoice.STATUS_DRAFT
                invoice.created_by = request.user
                invoice.save()

                formset.instance = invoice
                items = formset.save(commit=False)
                for item in items:
                    item.invoice = invoice
                    item.save()
                for deleted in formset.deleted_objects:
                    deleted.delete()
                recalculate_invoice_totals(invoice)

            log_event(
                action="invoice.created",
                user=request.user,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={"invoice_number": invoice.invoice_number},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Invoice created as Draft.")
            return redirect("invoice-detail", pk=invoice.pk)
    else:
        form = InvoiceForm()
        formset = InvoiceItemFormSet(prefix="items")

    return render(
        request,
        "invoicing/invoice_form.html",
        {
            "form": form,
            "formset": formset,
        },
    )


@login_required
@role_required(ADMIN, FINANCE)
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    log_event(
        action="invoice.detail.viewed",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={"invoice_number": invoice.invoice_number},
        ip_address=get_client_ip(request),
    )
    return render(
        request,
        "invoicing/invoice_detail.html",
        {
            "invoice": invoice,
            "items": invoice.items.all(),
            "status_choices": Invoice.STATUS_CHOICES,
        },
    )


@login_required
@role_required(ADMIN, FINANCE)
def invoice_status_update(request, pk):
    if request.method != "POST":
        raise Http404()

    invoice = get_object_or_404(Invoice, pk=pk)
    new_status = request.POST.get("status", "").strip()
    success, message = transition_invoice_status(invoice, new_status)
    if success:
        log_event(
            action="invoice.status.changed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={"invoice_number": invoice.invoice_number, "new_status": new_status},
            ip_address=get_client_ip(request),
        )
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect("invoice-detail", pk=invoice.pk)


def invoice_public_view(request, token):
    invoice = (
        Invoice.objects.select_related("customer")
        .filter(public_view_token=token)
        .order_by("id")
        .first()
    )
    if invoice is None:
        raise Http404()
    previous_status = invoice.status
    mark_invoice_viewed(invoice)
    invoice.refresh_from_db()
    log_event(
        action="invoice.public_viewed",
        user=None,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "status_before": previous_status,
            "status_after": invoice.status,
        },
        ip_address=get_client_ip(request),
    )
    return render(
        request,
        "invoicing/invoice_public_view.html",
        {"invoice": invoice, "items": invoice.items.all()},
    )


@login_required
@role_required(ADMIN, FINANCE)
def invoice_download_pdf(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    pdf_bytes = generate_invoice_pdf(invoice)
    log_event(
        action="invoice.pdf.downloaded",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={"invoice_number": invoice.invoice_number},
        ip_address=get_client_ip(request),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.pdf"'
    return response


@login_required
@role_required(ADMIN, FINANCE)
def invoice_download_excel(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    excel_bytes = generate_invoice_excel(invoice)
    log_event(
        action="invoice.excel.downloaded",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={"invoice_number": invoice.invoice_number},
        ip_address=get_client_ip(request),
    )
    response = HttpResponse(
        excel_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.xlsx"'
    return response
