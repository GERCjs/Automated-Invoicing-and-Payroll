from io import BytesIO
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from accounts.permissions import get_user_role, role_required
from accounts.roles import ADMIN, HR, STAFF, SUPERADMIN
from core.audit import get_client_ip, log_event
from notifications.models import EmailDeliveryLog

from .forms import EmployeeForm, PayrollRecordForm, PayrollUploadForm
from .models import Employee, PayrollRecord
from .services import (
    cpf_for_2026,
    parse_and_validate_payroll_excel,
)


def _generate_next_employee_code():
    latest_code = (
        Employee.objects.filter(employee_code__regex=r"^E[0-9]+$")
        .order_by("-employee_code")
        .values_list("employee_code", flat=True)
        .first()
    )
    if not latest_code:
        return "E001"

    number = int(latest_code[1:]) + 1
    return f"E{number:03d}"


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_cpf_preview(request):
    employee_id = (request.GET.get("employee_id") or "").strip()
    basic_salary_raw = (request.GET.get("basic_salary") or "").strip()
    physical_products_commission_raw = (request.GET.get("physical_products_commission") or "").strip()
    credit_commission_raw = (request.GET.get("credit_commission") or "").strip()
    services_commission_raw = (request.GET.get("services_commission") or "").strip()
    payment_date_raw = (request.GET.get("payment_date") or "").strip()

    if not employee_id or not basic_salary_raw or not payment_date_raw:
        return JsonResponse(
            {
                "ok": False,
                "cpf_contribution": "",
                "employer_cpf_contribution": "",
                "reason": "missing_inputs",
            }
        )

    try:
        basic_salary = Decimal(basic_salary_raw)
        physical_products_commission = Decimal(physical_products_commission_raw or "0")
        credit_commission = Decimal(credit_commission_raw or "0")
        services_commission = Decimal(services_commission_raw or "0")
    except (InvalidOperation, ValueError):
        return JsonResponse(
            {
                "ok": False,
                "cpf_contribution": "",
                "employer_cpf_contribution": "",
                "reason": "invalid_salary",
            }
        )

    try:
        payment_date = date.fromisoformat(payment_date_raw)
    except ValueError:
        return JsonResponse(
            {
                "ok": False,
                "cpf_contribution": "",
                "employer_cpf_contribution": "",
                "reason": "invalid_payment_date",
            }
        )

    employee = Employee.objects.filter(employee_code=employee_id).first()
    if employee is None:
        return JsonResponse(
            {
                "ok": False,
                "cpf_contribution": "",
                "employer_cpf_contribution": "",
                "reason": "employee_not_found",
            }
        )
    if employee.cpf_exempt:
        return JsonResponse(
            {
                "ok": True,
                "cpf_contribution": "0.00",
                "employer_cpf_contribution": "0.00",
                "reason": "cpf_exempt",
            }
        )
    if not employee.date_of_birth:
        return JsonResponse(
            {
                "ok": False,
                "cpf_contribution": "",
                "employer_cpf_contribution": "",
                "reason": "missing_dob",
            }
        )

    total_earnings = basic_salary + physical_products_commission + credit_commission + services_commission
    age = payment_date.year - employee.date_of_birth.year - (
        (payment_date.month, payment_date.day) < (employee.date_of_birth.month, employee.date_of_birth.day)
    )
    cpf = cpf_for_2026(total_earnings, age)
    return JsonResponse(
        {
            "ok": True,
            "cpf_contribution": str(cpf.employee_amount),
            "employer_cpf_contribution": str(cpf.employer_amount),
            "reason": "calculated",
        }
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_dashboard(request):
    total_records = PayrollRecord.objects.count()
    total_net_salary = sum(record.net_salary for record in PayrollRecord.objects.only("net_salary"))
    recent_payslip_records = PayrollRecord.objects.all()[:8]
    return render(
        request,
        "payroll/payroll_dashboard.html",
        {
            "total_records": total_records,
            "total_net_salary": total_net_salary,
            "recent_payslip_records": recent_payslip_records,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_list(request):
    search_query = request.GET.get("q", "").strip()
    payroll_month = request.GET.get("month", "").strip()
    payslip_records = PayrollRecord.objects.all()
    if search_query:
        payslip_records = payslip_records.filter(
            Q(employee_name__icontains=search_query)
            | Q(employee_id__icontains=search_query)
        )
    if payroll_month:
        try:
            selected_month = date.fromisoformat(f"{payroll_month}-01")
            payslip_records = payslip_records.filter(
                payment_date__year=selected_month.year,
                payment_date__month=selected_month.month,
            )
        except ValueError:
            messages.warning(request, "Invalid month filter. Please use YYYY-MM format.")
            payroll_month = ""

    return render(
        request,
        "payroll/payroll_list.html",
        {
            "payslip_records": payslip_records,
            "search_query": search_query,
            "payroll_month": payroll_month,
            "result_count": payslip_records.count(),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_create(request):
    if request.method == "POST":
        form = PayrollRecordForm(request.POST)
        if form.is_valid():
            payslip_record = form.save(commit=False)
            employee = Employee.objects.get(employee_code=payslip_record.employee_id)
            if not employee.date_of_birth:
                form.add_error(
                    "employee_id",
                    "Selected employee is missing Date of Birth. CPF cannot be auto-calculated.",
                )
                return render(
                    request,
                    "payroll/payroll_form.html",
                    {"form": form, "is_edit": False, "payslip_record": None},
                )
            payslip_record.employee_name = f"{employee.first_name} {employee.last_name}".strip()
            payslip_record.nric = (employee.nric or "")[:9]
            payslip_record.cpf_exempted = employee.cpf_exempt
            payslip_record.sdl_exempted = employee.sdl_exempt
            payslip_record.allowances = (
                (form.cleaned_data.get("physical_products_commission") or Decimal("0"))
                + (form.cleaned_data.get("credit_commission") or Decimal("0"))
                + (form.cleaned_data.get("services_commission") or Decimal("0"))
            )
            payslip_record.deductions = (
                (form.cleaned_data.get("loan_deduction") or Decimal("0"))
                + (form.cleaned_data.get("other_deductions") or Decimal("0"))
            )
            total_earnings = payslip_record.basic_salary + payslip_record.allowances
            if employee.cpf_exempt:
                employee_cpf = Decimal("0")
            else:
                age = payslip_record.payment_date.year - employee.date_of_birth.year - (
                    (payslip_record.payment_date.month, payslip_record.payment_date.day)
                    < (employee.date_of_birth.month, employee.date_of_birth.day)
                )
                employee_cpf = cpf_for_2026(total_earnings, age).employee_amount
            payslip_record.cpf_contribution = employee_cpf
            payslip_record.net_salary = (
                total_earnings
                - payslip_record.deductions
                - employee_cpf
            )
            payslip_record.created_by = request.user
            payslip_record.save()
            log_event(
                action="payroll.record.created",
                user=request.user,
                target_type="payroll_record",
                target_id=str(payslip_record.id),
                metadata={"employee_id": payslip_record.employee_id},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Payslip record created.")
            return redirect("payroll-detail", pk=payslip_record.pk)
    else:
        form = PayrollRecordForm()

    return render(
        request,
        "payroll/payroll_form.html",
        {"form": form, "is_edit": False, "payslip_record": None},
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_edit(request, pk):
    payslip_record = get_object_or_404(PayrollRecord, pk=pk)
    if request.method == "POST":
        form = PayrollRecordForm(request.POST, instance=payslip_record)
        if form.is_valid():
            payslip_record = form.save(commit=False)
            employee = Employee.objects.get(employee_code=payslip_record.employee_id)
            if not employee.date_of_birth:
                form.add_error(
                    "employee_id",
                    "Selected employee is missing Date of Birth. CPF cannot be auto-calculated.",
                )
                return render(
                    request,
                    "payroll/payroll_form.html",
                    {"form": form, "is_edit": True, "payslip_record": payslip_record},
                )
            payslip_record.employee_name = f"{employee.first_name} {employee.last_name}".strip()
            payslip_record.nric = (employee.nric or "")[:9]
            payslip_record.cpf_exempted = employee.cpf_exempt
            payslip_record.sdl_exempted = employee.sdl_exempt
            payslip_record.allowances = (
                (form.cleaned_data.get("physical_products_commission") or Decimal("0"))
                + (form.cleaned_data.get("credit_commission") or Decimal("0"))
                + (form.cleaned_data.get("services_commission") or Decimal("0"))
            )
            payslip_record.deductions = (
                (form.cleaned_data.get("loan_deduction") or Decimal("0"))
                + (form.cleaned_data.get("other_deductions") or Decimal("0"))
            )
            total_earnings = payslip_record.basic_salary + payslip_record.allowances
            if employee.cpf_exempt:
                employee_cpf = Decimal("0")
            else:
                age = payslip_record.payment_date.year - employee.date_of_birth.year - (
                    (payslip_record.payment_date.month, payslip_record.payment_date.day)
                    < (employee.date_of_birth.month, employee.date_of_birth.day)
                )
                employee_cpf = cpf_for_2026(total_earnings, age).employee_amount
            payslip_record.cpf_contribution = employee_cpf
            payslip_record.net_salary = (
                total_earnings
                - payslip_record.deductions
                - employee_cpf
            )
            payslip_record.save()
            log_event(
                action="payroll.record.updated",
                user=request.user,
                target_type="payroll_record",
                target_id=str(payslip_record.id),
                metadata={"employee_id": payslip_record.employee_id},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Payslip record updated.")
            return redirect("payroll-detail", pk=payslip_record.pk)
    else:
        form = PayrollRecordForm(instance=payslip_record)

    return render(
        request,
        "payroll/payroll_form.html",
        {"form": form, "is_edit": True, "payslip_record": payslip_record},
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_detail(request, pk):
    payslip_record = get_object_or_404(PayrollRecord, pk=pk)
    employer_cpf_contribution = _calculate_employer_cpf_for_record(payslip_record)
    return render(
        request,
        "payroll/payroll_detail.html",
        {
            "payslip_record": payslip_record,
            "employer_cpf_contribution": employer_cpf_contribution,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_upload_preview(request):
    form = PayrollUploadForm(request.POST or None, request.FILES or None)
    preview_rows = []
    invalid_rows = []
    total_rows = 0
    valid_count = 0
    invalid_count = 0

    if request.method == "POST" and form.is_valid():
        try:
            upload_result = parse_and_validate_payroll_excel(form.cleaned_data["payroll_file"])
            preview_rows = upload_result["valid_rows"]
            invalid_rows = upload_result["invalid_rows"]
            filtered_valid_rows = []
            for row in preview_rows:
                employee_code = str(row.get("employee_code") or "").strip()
                if not Employee.objects.filter(employee_code=employee_code).exists():
                    invalid_rows.append(
                        {
                            "row_number": row.get("row_number"),
                            "employee_code": employee_code,
                            "employee_name": row.get("employee_name", ""),
                            "errors": ["Employee code not found in employee records."],
                        }
                    )
                else:
                    filtered_valid_rows.append(row)
            preview_rows = filtered_valid_rows
            total_rows = upload_result["total_rows"]
            valid_count = len(preview_rows)
            invalid_count = len(invalid_rows)

            if not preview_rows and not invalid_rows:
                messages.warning(request, "No data rows were found in the uploaded file.")
            else:
                request.session["payroll_upload_preview"] = {
                    "payment_date": form.cleaned_data["payment_date"].isoformat(),
                    "rows": [_serialize_preview_row(r) for r in preview_rows],
                }
            log_event(
                action="payroll.upload.previewed",
                user=request.user,
                metadata={
                    "path": request.path,
                    "row_count": total_rows,
                    "valid_row_count": valid_count,
                    "invalid_row_count": invalid_count,
                    "source_file_name": form.cleaned_data["payroll_file"].name,
                },
                ip_address=get_client_ip(request),
            )
        except Exception as exc:
            log_event(
                action="payroll.upload.failed",
                user=request.user,
                metadata={"path": request.path, "error_message": str(exc)},
                ip_address=get_client_ip(request),
            )
            messages.error(request, f"Unable to process file: {exc}")
            return redirect("payroll-upload-preview")

    return render(
        request,
        "payroll/upload_preview.html",
        {
            "form": form,
            "preview_rows": preview_rows,
            "invalid_rows": invalid_rows,
            "total_rows": total_rows,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
        },
    )


def _serialize_preview_row(row):
    return {
        "employee_code": str(row["employee_code"]),
        "employee_name": str(row["employee_name"]),
        "basic_salary": str(row["basic_salary"]),
        "physical_products_commission": str(row["physical_products_commission"]),
        "credit_commission": str(row["credit_commission"]),
        "services_commission": str(row["services_commission"]),
        "loan_deduction": str(row["loan_deduction"]),
        "other_deductions": str(row["other_deductions"]),
        "cpf_employee_amount": str(row["cpf_employee_amount"]),
        "net_pay": str(row["net_pay"]),
    }


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_upload_confirm_save(request):
    if request.method != "POST":
        return redirect("payroll-upload-preview")

    upload_data = request.session.get("payroll_upload_preview") or {}
    payment_date_raw = upload_data.get("payment_date")
    serialized_rows = upload_data.get("rows") or []

    if not payment_date_raw or not serialized_rows:
        messages.error(request, "No valid upload preview found. Please upload and preview first.")
        return redirect("payroll-upload-preview")

    payment_date = date.fromisoformat(payment_date_raw)
    saved_count = 0
    skipped_count = 0

    with transaction.atomic():
        for row in serialized_rows:
            employee_code = str(row["employee_code"]).strip()
            if PayrollRecord.objects.filter(employee_id=employee_code, payment_date=payment_date).exists():
                skipped_count += 1
                continue

            allowances = (
                Decimal(row["physical_products_commission"])
                + Decimal(row["credit_commission"])
                + Decimal(row["services_commission"])
            )
            deductions = Decimal(row["loan_deduction"]) + Decimal(row["other_deductions"])
            record = PayrollRecord(
                employee_name=row["employee_name"],
                employee_id=employee_code,
                basic_salary=Decimal(row["basic_salary"]),
                allowances=allowances,
                deductions=deductions,
                cpf_contribution=Decimal(row["cpf_employee_amount"]),
                net_salary=Decimal(row["net_pay"]),
                payment_date=payment_date,
                created_by=request.user,
            )
            employee = Employee.objects.filter(employee_code=record.employee_id).first()
            if employee:
                record.nric = (employee.nric or "")[:9]
                record.cpf_exempted = employee.cpf_exempt
                record.sdl_exempted = employee.sdl_exempt
            record.save()
            saved_count += 1

    request.session.pop("payroll_upload_preview", None)
    log_event(
        action="payroll.upload.saved",
        user=request.user,
        metadata={
            "saved_count": saved_count,
            "skipped_duplicate_count": skipped_count,
            "payment_date": payment_date.isoformat(),
        },
        ip_address=get_client_ip(request),
    )
    if saved_count > 0 and skipped_count > 0:
        messages.success(
            request,
            f"Payroll upload saved successfully. {saved_count} record(s) created, {skipped_count} duplicate record(s) skipped.",
        )
    elif saved_count > 0:
        messages.success(request, f"Payroll upload saved successfully. {saved_count} record(s) created.")
    elif skipped_count > 0:
        messages.warning(
            request,
            f"No new payroll records were created. {skipped_count} duplicate record(s) were skipped.",
        )
    else:
        messages.warning(request, "No payroll records were created from this upload.")
    return redirect("payroll-list")


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_template_download(request):
    template_path = Path(__file__).resolve().parent / "payroll_upload_template.xlsx"
    response = FileResponse(
        template_path.open("rb"),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="payroll_upload_template.xlsx"'
    return response


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_list(request):
    search_query = request.GET.get("q", "").strip()
    employees = Employee.objects.all()
    if search_query:
        employees = employees.filter(
            Q(employee_code__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
        )
    return render(
        request,
        "payroll/employee_list.html",
        {"employees": employees, "search_query": search_query, "result_count": employees.count()},
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_create(request):
    if request.method == "POST":
        post_data = request.POST.copy()
        if not (post_data.get("employee_code") or "").strip():
            post_data["employee_code"] = _generate_next_employee_code()
        form = EmployeeForm(post_data)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.hire_date = employee.date_of_appointment or employee.hire_date
            employee.status = Employee.STATUS_ACTIVE
            employee.created_by = request.user
            employee.save()
            log_event(
                action="payroll.employee.created",
                user=request.user,
                target_type="employee",
                target_id=str(employee.id),
                metadata={"employee_code": employee.employee_code},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Employee saved successfully.")
            return redirect("employee-list")
    else:
        form = EmployeeForm(initial={"employee_code": _generate_next_employee_code()})
    return render(request, "payroll/employee_form.html", {"form": form, "is_edit": False})


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.hire_date = employee.date_of_appointment or employee.hire_date
            employee.save()
            log_event(
                action="payroll.employee.updated",
                user=request.user,
                target_type="employee",
                target_id=str(employee.id),
                metadata={"employee_code": employee.employee_code},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Employee updated successfully.")
            return redirect("employee-list")
    else:
        form = EmployeeForm(instance=employee)
    return render(request, "payroll/employee_form.html", {"form": form, "is_edit": True})


@login_required
@role_required(STAFF)
def my_payslips(request):
    employee = _resolve_staff_employee(request.user)

    if employee is None:
        messages.error(
            request,
            "Your account is not linked to an employee profile yet. Ask admin to link your account in Employees.",
        )
        return redirect("dashboard")

    payslip_records = PayrollRecord.objects.filter(employee_id=employee.employee_code)
    return render(
        request,
        "payroll/my_payslips.html",
        {
            "employee": employee,
            "payslip_records": payslip_records,
        },
    )


def _resolve_staff_employee(user):
    employee = getattr(user, "employee_profile", None)
    if employee is not None:
        return employee
    user_email = (user.email or "").strip()
    if not user_email:
        return None
    email_matches = Employee.objects.filter(email__iexact=user_email)
    if email_matches.count() != 1:
        return None
    employee = email_matches.first()
    employee.user = user
    employee.save(update_fields=["user", "updated_at"])
    return employee


def _build_payslip_pdf(payslip_record: PayrollRecord) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    pdf.setTitle(f"Payslip-{payslip_record.employee_id}-{payslip_record.payment_date}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(20 * mm, page_height - 20 * mm, "Payslip")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(20 * mm, page_height - 26 * mm, "Automated Invoicing and Payroll System")

    y = page_height - 40 * mm
    line_gap = 8 * mm

    employer_cpf_contribution = _calculate_employer_cpf_for_record(payslip_record)
    rows = [
        ("Employee Name", payslip_record.employee_name),
        ("Employee ID", payslip_record.employee_id),
        ("Payment Date", payslip_record.payment_date.isoformat()),
        ("NRIC", payslip_record.nric or "-"),
        ("Basic Salary", f"SGD {payslip_record.basic_salary:.2f}"),
        ("Allowances", f"SGD {payslip_record.allowances:.2f}"),
        ("Deductions", f"SGD {payslip_record.deductions:.2f}"),
        ("CPF Contribution", f"SGD {payslip_record.cpf_contribution:.2f}"),
        ("Employer CPF (separate)", f"SGD {employer_cpf_contribution:.2f}"),
        ("Net Salary", f"SGD {payslip_record.net_salary:.2f}"),
    ]

    for label, value in rows:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(20 * mm, y, f"{label}:")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(65 * mm, y, str(value))
        y -= line_gap

    pdf.line(20 * mm, y - 2 * mm, page_width - 20 * mm, y - 2 * mm)
    y -= 12 * mm
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(20 * mm, y, "This is a computer-generated payslip.")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


@login_required
@role_required(SUPERADMIN, ADMIN, HR, STAFF)
def payslip_pdf_download(request, pk):
    payslip_record = get_object_or_404(PayrollRecord, pk=pk)
    role = get_user_role(request.user)

    if role == STAFF:
        employee = _resolve_staff_employee(request.user)
        if employee is None or payslip_record.employee_id != employee.employee_code:
            log_event(
                action="payroll.pdf.permission_denied",
                user=request.user,
                target_type="payroll_record",
                target_id=str(payslip_record.id),
                metadata={"employee_id": payslip_record.employee_id},
                ip_address=get_client_ip(request),
            )
            raise PermissionDenied("You can only download your own payslips.")

    pdf_bytes = _build_payslip_pdf(payslip_record)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="payslip_{payslip_record.employee_id}_{payslip_record.payment_date}.pdf"'
    )
    log_event(
        action="payroll.pdf.downloaded",
        user=request.user,
        target_type="payroll_record",
        target_id=str(payslip_record.id),
        metadata={"employee_id": payslip_record.employee_id, "role": role},
        ip_address=get_client_ip(request),
    )
    return response


def _calculate_employer_cpf_for_record(payslip_record: PayrollRecord) -> Decimal:
    employee = Employee.objects.filter(employee_code=payslip_record.employee_id).first()
    if not employee or employee.cpf_exempt or not employee.date_of_birth:
        return Decimal("0.00")
    total_earnings = payslip_record.basic_salary + payslip_record.allowances
    age = payslip_record.payment_date.year - employee.date_of_birth.year - (
        (payslip_record.payment_date.month, payslip_record.payment_date.day)
        < (employee.date_of_birth.month, employee.date_of_birth.day)
    )
    return cpf_for_2026(total_earnings, age).employer_amount


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payslip_email_send(request, pk):
    if request.method != "POST":
        return redirect("payroll-detail", pk=pk)

    payslip_record = get_object_or_404(PayrollRecord, pk=pk)
    employee = Employee.objects.filter(employee_code=payslip_record.employee_id).first()
    recipient = ((employee.email if employee else "") or "").strip().lower()
    subject = f"Payslip for {payslip_record.payment_date:%Y-%m-%d} - {payslip_record.employee_id}"
    text_body = (
        f"Dear {payslip_record.employee_name},\n\n"
        f"Your payslip for {payslip_record.payment_date:%Y-%m-%d} is attached.\n"
        f"Net salary: SGD {payslip_record.net_salary:.2f}\n\n"
        "Regards,\nPayroll Team"
    )

    email_log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key="payroll_payslip_email_v1",
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="payroll_record",
        related_object_id=str(payslip_record.id),
        triggered_by=request.user,
        metadata={
            "employee_id": payslip_record.employee_id,
            "payment_date": payslip_record.payment_date.isoformat(),
        },
    )

    if not recipient:
        email_log.status = EmailDeliveryLog.STATUS_FAILED
        email_log.error_message = "Employee email is missing."
        email_log.save(update_fields=["status", "error_message"])
        log_event(
            action="payroll.email.failed",
            user=request.user,
            target_type="payroll_record",
            target_id=str(payslip_record.id),
            metadata={"reason": "missing_employee_email"},
            ip_address=get_client_ip(request),
        )
        messages.error(request, "Unable to send email: employee email is missing.")
        return redirect("payroll-detail", pk=pk)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach(
        filename=f"payslip_{payslip_record.employee_id}_{payslip_record.payment_date}.pdf",
        content=_build_payslip_pdf(payslip_record),
        mimetype="application/pdf",
    )
    try:
        sent_count = message.send()
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        email_log.status = EmailDeliveryLog.STATUS_FAILED
        email_log.error_message = str(exc)
        email_log.save(update_fields=["status", "error_message"])
        log_event(
            action="payroll.email.failed",
            user=request.user,
            target_type="payroll_record",
            target_id=str(payslip_record.id),
            metadata={"reason": str(exc)},
            ip_address=get_client_ip(request),
        )
        messages.error(request, f"Payslip email failed: {exc}")
        return redirect("payroll-detail", pk=pk)

    email_log.status = EmailDeliveryLog.STATUS_SENT
    email_log.sent_at = timezone.now()
    email_log.save(update_fields=["status", "sent_at"])
    log_event(
        action="payroll.email.sent",
        user=request.user,
        target_type="payroll_record",
        target_id=str(payslip_record.id),
        metadata={"recipient": recipient},
        ip_address=get_client_ip(request),
    )
    messages.success(request, f"Payslip email sent to {recipient}.")
    return redirect("payroll-detail", pk=pk)
