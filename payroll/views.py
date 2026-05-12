from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from openpyxl import Workbook

from accounts.permissions import role_required
from accounts.roles import ADMIN, FINANCE, SUPERADMIN

from .forms import PayrollUploadForm
from .services import TEMPLATE_HEADERS, default_template_row, parse_payroll_excel


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def payroll_upload_preview(request):
    form = PayrollUploadForm(request.POST or None, request.FILES or None)
    preview_rows = []

    if request.method == "POST" and form.is_valid():
        try:
            preview_rows = parse_payroll_excel(form.cleaned_data["payroll_file"])
            if not preview_rows:
                messages.warning(request, "No data rows were found in the uploaded file.")
        except Exception as exc:
            messages.error(request, f"Unable to process file: {exc}")
            return redirect("payroll-upload-preview")

    return render(
        request,
        "payroll/upload_preview.html",
        {"form": form, "preview_rows": preview_rows},
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def payroll_template_download(request):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Payroll"
    sheet.append(TEMPLATE_HEADERS)
    sheet.append(default_template_row())

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="payroll_upload_template.xlsx"'
    return response
