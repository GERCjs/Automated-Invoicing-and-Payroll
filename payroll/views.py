from io import BytesIO
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from pathlib import Path
import re

from openpyxl import Workbook
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

from .forms import EmployeeForm, EmployeeUploadForm, PayrollRecordForm, PayrollUploadForm
from .models import Employee, PayrollRecord
from .services import (
    cpf_for_2026,
    parse_and_validate_payroll_excel,
)

EMPLOYEE_CODE_PATTERN = re.compile(r"^STF-[0-9]{6}$")


def _generate_next_employee_code():
    latest_code = (
        Employee.objects.filter(employee_code__regex=r"^STF-[0-9]{6}$")
        .order_by("-id")
        .values_list("employee_code", flat=True)
        .first()
    )
    if not latest_code:
        return "STF-000001"

    number = int(latest_code.split("-")[1]) + 1
    return f"STF-{number:06d}"


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_employee_lookup(request):
    employee_id = (request.GET.get("employee_id") or "").strip()
    if not employee_id:
        return JsonResponse({"ok": False, "employee_name": "", "reason": "missing_employee_id"})

    employee = Employee.objects.filter(employee_code=employee_id).first()
    if employee is None:
        return JsonResponse({"ok": False, "employee_name": "", "reason": "employee_not_found"})

    employee_name = f"{employee.first_name} {employee.last_name}".strip()
    return JsonResponse({"ok": True, "employee_name": employee_name, "reason": "found"})


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

    payment_date = _parse_date_ddmmyyyy(payment_date_raw)
    if payment_date is None:
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
    recent_payslip_records = PayrollRecord.objects.only(
        "employee_name",
        "employee_id",
        "payment_date",
        "net_salary",
        "created_at",
    )[:8]
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
            payslip_record.physical_products_commission = (
                form.cleaned_data.get("physical_products_commission") or Decimal("0")
            )
            payslip_record.credit_commission = form.cleaned_data.get("credit_commission") or Decimal("0")
            payslip_record.services_commission = form.cleaned_data.get("services_commission") or Decimal("0")
            payslip_record.loan_deduction = form.cleaned_data.get("loan_deduction") or Decimal("0")
            payslip_record.other_deductions = form.cleaned_data.get("other_deductions") or Decimal("0")
            payslip_record.allowances = (
                payslip_record.physical_products_commission
                + payslip_record.credit_commission
                + payslip_record.services_commission
            )
            payslip_record.deductions = payslip_record.loan_deduction + payslip_record.other_deductions
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
            payslip_record.physical_products_commission = (
                form.cleaned_data.get("physical_products_commission") or Decimal("0")
            )
            payslip_record.credit_commission = form.cleaned_data.get("credit_commission") or Decimal("0")
            payslip_record.services_commission = form.cleaned_data.get("services_commission") or Decimal("0")
            payslip_record.loan_deduction = form.cleaned_data.get("loan_deduction") or Decimal("0")
            payslip_record.other_deductions = form.cleaned_data.get("other_deductions") or Decimal("0")
            payslip_record.allowances = (
                payslip_record.physical_products_commission
                + payslip_record.credit_commission
                + payslip_record.services_commission
            )
            payslip_record.deductions = payslip_record.loan_deduction + payslip_record.other_deductions
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
def payroll_delete(request, pk):
    if request.method != "POST":
        return redirect("payroll-detail", pk=pk)

    payslip_record = get_object_or_404(PayrollRecord, pk=pk)
    record_id = payslip_record.id
    employee_id = payslip_record.employee_id
    payslip_record.delete()
    log_event(
        action="payroll.record.deleted",
        user=request.user,
        target_type="payroll_record",
        target_id=str(record_id),
        metadata={"employee_id": employee_id},
        ip_address=get_client_ip(request),
    )
    messages.success(request, "Payslip record deleted permanently.")
    return redirect("payroll-list")


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
            upload_result = parse_and_validate_payroll_excel(
                form.cleaned_data["payroll_file"],
                form.cleaned_data["payment_date"],
            )
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
    invalid_format_count = 0

    with transaction.atomic():
        for row in serialized_rows:
            employee_code = str(row["employee_code"]).strip()
            if not EMPLOYEE_CODE_PATTERN.fullmatch(employee_code):
                invalid_format_count += 1
                continue
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
                physical_products_commission=Decimal(row["physical_products_commission"]),
                credit_commission=Decimal(row["credit_commission"]),
                services_commission=Decimal(row["services_commission"]),
                allowances=allowances,
                loan_deduction=Decimal(row["loan_deduction"]),
                other_deductions=Decimal(row["other_deductions"]),
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
    if invalid_format_count:
        messages.warning(
            request,
            f"{invalid_format_count} row(s) were skipped because employee code was not in STF-000000 format.",
        )
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


def _serialize_employee_preview_row(row):
    return {
        "row_number": row["row_number"],
        "employee_code": row["employee_code"],
        "nric": row["nric"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "date_of_birth": row["date_of_birth"],
        "date_of_appointment": row["date_of_appointment"],
        "legal_status": row["legal_status"],
        "gender": row["gender"],
        "race": row["race"],
        "religion": row["religion"],
        "sdl_exempt": row["sdl_exempt"],
        "cpf_exempt": row["cpf_exempt"],
        "job_title": row["job_title"],
        "email": row["email"],
        "payment_method": row["payment_method"],
        "bank_name": row["bank_name"],
        "bank_account_number": row["bank_account_number"],
        "bank_branch_code": row["bank_branch_code"],
    }


def _deserialize_employee_preview_row(row):
    parsed = dict(row)
    for key in ("date_of_birth", "date_of_appointment"):
        if parsed.get(key):
            parsed_date = _parse_date_ddmmyyyy(parsed[key])
            parsed[key] = parsed_date if parsed_date else None
    return parsed


def _parse_date_ddmmyyyy(raw_value):
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, datetime):
        return raw_value.date()
    if isinstance(raw_value, date):
        return raw_value
    raw_text = str(raw_value).strip()
    if not raw_text:
        return None
    try:
        return datetime.strptime(raw_text, "%d-%m-%Y").date()
    except ValueError:
        return None


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_upload_preview(request):
    form = EmployeeUploadForm(request.POST or None, request.FILES or None)
    preview_rows = []
    invalid_rows = []
    total_rows = 0
    valid_count = 0
    invalid_count = 0

    if request.method == "POST" and form.is_valid():
        try:
            uploaded_file = form.cleaned_data["employee_file"]
            from openpyxl import load_workbook

            worksheet = load_workbook(uploaded_file, data_only=True).active
            raw_rows = list(worksheet.iter_rows(values_only=True))
            if not raw_rows:
                raise ValueError("Uploaded file is empty.")

            headers = [str(h).strip().lower() if h is not None else "" for h in raw_rows[0]]
            required_headers = [
                "employee_code",
                "nric",
                "first_name",
                "last_name",
                "date_of_birth",
                "date_of_appointment",
                "legal_status",
                "gender",
                "race",
                "religion",
                "sdl_exempt",
                "cpf_exempt",
                "job_title",
                "email",
                "payment_method",
                "bank_name",
                "bank_account_number",
                "bank_branch_code",
            ]
            missing = sorted(set(required_headers) - set(headers))
            if missing:
                raise ValueError(f"Missing required columns: {', '.join(missing)}")

            index_map = {name: headers.index(name) for name in required_headers}
            valid_legal_status = {choice[0] for choice in Employee.LEGAL_STATUS_CHOICES}
            valid_gender = {choice[0] for choice in Employee.GENDER_CHOICES}
            valid_payment_method = {choice[0] for choice in Employee.PAYMENT_METHOD_CHOICES}
            used_employee_codes = set(Employee.objects.values_list("employee_code", flat=True))

            for row_number, row in enumerate(raw_rows[1:], start=2):
                if row is None or all(value in (None, "") for value in row):
                    continue

                row_errors = []
                parsed_row = {}
                for header in required_headers:
                    value = row[index_map[header]]
                    if header in ("date_of_birth", "date_of_appointment"):
                        parsed_row[header] = value
                    else:
                        parsed_row[header] = str(value).strip() if value is not None else ""

                if not parsed_row["first_name"]:
                    row_errors.append("First name is required.")
                if not parsed_row["last_name"]:
                    row_errors.append("Last name is required.")
                if not parsed_row["email"]:
                    row_errors.append("Email is required.")

                for date_field in ("date_of_birth", "date_of_appointment"):
                    raw_value = parsed_row[date_field]
                    if not raw_value:
                        if date_field == "date_of_appointment":
                            row_errors.append("Date of appointment is required.")
                        parsed_row[date_field] = ""
                        continue
                    parsed_date = _parse_date_ddmmyyyy(raw_value)
                    if parsed_date is None:
                        row_errors.append(f"{date_field.replace('_', ' ').title()} must be DD-MM-YYYY.")
                    else:
                        parsed_row[date_field] = parsed_date.strftime("%d-%m-%Y")

                for bool_field in ("sdl_exempt", "cpf_exempt"):
                    raw_value = parsed_row[bool_field].lower()
                    if raw_value in ("true", "1", "yes", "y"):
                        parsed_row[bool_field] = True
                    elif raw_value in ("false", "0", "no", "n", ""):
                        parsed_row[bool_field] = False
                    else:
                        row_errors.append(f"{bool_field} must be TRUE/FALSE.")

                if parsed_row["legal_status"] and parsed_row["legal_status"] not in valid_legal_status:
                    row_errors.append("Invalid legal_status value.")
                if parsed_row["gender"] and parsed_row["gender"] not in valid_gender:
                    row_errors.append("Invalid gender value.")
                if parsed_row["payment_method"] and parsed_row["payment_method"] not in valid_payment_method:
                    row_errors.append("Invalid payment_method value.")
                if parsed_row["employee_code"] and not EMPLOYEE_CODE_PATTERN.fullmatch(parsed_row["employee_code"]):
                    row_errors.append("Employee code must follow STF-000000 format.")
                if parsed_row["employee_code"] and Employee.objects.filter(employee_code=parsed_row["employee_code"]).exists():
                    row_errors.append("Employee code already exists.")
                if Employee.objects.filter(email__iexact=parsed_row["email"]).exists():
                    row_errors.append("Email already exists in employee records.")

                if row_errors:
                    invalid_rows.append(
                        {
                            "row_number": row_number,
                            "employee_code": parsed_row["employee_code"],
                            "employee_name": f"{parsed_row['first_name']} {parsed_row['last_name']}".strip(),
                            "errors": row_errors,
                        }
                    )
                    continue

                parsed_row["row_number"] = row_number
                if not parsed_row["employee_code"]:
                    generated_code = _generate_next_employee_code()
                    while generated_code in used_employee_codes:
                        numeric_part = int(generated_code.split("-")[1]) + 1
                        generated_code = f"STF-{numeric_part:06d}"
                    parsed_row["employee_code"] = generated_code
                used_employee_codes.add(parsed_row["employee_code"])
                preview_rows.append(parsed_row)

            total_rows = len(preview_rows) + len(invalid_rows)
            valid_count = len(preview_rows)
            invalid_count = len(invalid_rows)

            if preview_rows:
                request.session["employee_upload_preview"] = {
                    "rows": [_serialize_employee_preview_row(r) for r in preview_rows],
                }

            log_event(
                action="employee.upload.previewed",
                user=request.user,
                metadata={
                    "path": request.path,
                    "row_count": total_rows,
                    "valid_row_count": valid_count,
                    "invalid_row_count": invalid_count,
                    "source_file_name": uploaded_file.name,
                },
                ip_address=get_client_ip(request),
            )
        except Exception as exc:
            log_event(
                action="employee.upload.failed",
                user=request.user,
                metadata={"path": request.path, "error_message": str(exc)},
                ip_address=get_client_ip(request),
            )
            messages.error(request, f"Unable to process file: {exc}")
            return redirect("employee-upload-preview")

    return render(
        request,
        "payroll/employee_upload_preview.html",
        {
            "form": form,
            "preview_rows": preview_rows,
            "invalid_rows": invalid_rows,
            "total_rows": total_rows,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_upload_confirm_save(request):
    if request.method != "POST":
        return redirect("employee-upload-preview")

    upload_data = request.session.get("employee_upload_preview") or {}
    serialized_rows = upload_data.get("rows") or []
    if not serialized_rows:
        messages.error(request, "No valid employee upload preview found. Please upload and preview first.")
        return redirect("employee-upload-preview")

    saved_count = 0
    skipped_count = 0
    invalid_format_count = 0
    with transaction.atomic():
        for row in serialized_rows:
            parsed = _deserialize_employee_preview_row(row)
            if not EMPLOYEE_CODE_PATTERN.fullmatch(parsed["employee_code"]):
                invalid_format_count += 1
                continue
            if Employee.objects.filter(employee_code=parsed["employee_code"]).exists() or Employee.objects.filter(
                email__iexact=parsed["email"]
            ).exists():
                skipped_count += 1
                continue

            employee = Employee(
                employee_code=parsed["employee_code"],
                nric=parsed["nric"],
                first_name=parsed["first_name"],
                last_name=parsed["last_name"],
                date_of_birth=parsed["date_of_birth"] or None,
                date_of_appointment=parsed["date_of_appointment"],
                legal_status=parsed["legal_status"],
                gender=parsed["gender"],
                race=parsed["race"],
                religion=parsed["religion"],
                sdl_exempt=parsed["sdl_exempt"],
                cpf_exempt=parsed["cpf_exempt"],
                job_title=parsed["job_title"],
                email=parsed["email"],
                payment_method=parsed["payment_method"],
                bank_name=parsed["bank_name"],
                bank_account_number=parsed["bank_account_number"],
                bank_branch_code=parsed["bank_branch_code"],
                hire_date=parsed["date_of_appointment"],
                status=Employee.STATUS_ACTIVE,
                created_by=request.user,
            )
            employee.save()
            saved_count += 1

    request.session.pop("employee_upload_preview", None)
    if invalid_format_count:
        messages.warning(
            request,
            f"{invalid_format_count} row(s) were skipped because employee code was not in STF-000000 format.",
        )
    messages.success(
        request,
        f"Employee upload saved. {saved_count} created, {skipped_count} skipped due to duplicates."
        if skipped_count
        else f"Employee upload saved. {saved_count} employee record(s) created.",
    )
    return redirect("employee-list")


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_template_download(request):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Employee Template"
    headers = [
        "employee_code",
        "nric",
        "first_name",
        "last_name",
        "date_of_birth",
        "date_of_appointment",
        "legal_status",
        "gender",
        "race",
        "religion",
        "sdl_exempt",
        "cpf_exempt",
        "job_title",
        "email",
        "payment_method",
        "bank_name",
        "bank_account_number",
        "bank_branch_code",
    ]
    worksheet.append(headers)
    worksheet.append(
        [
            "STF-000001",
            "S1234567A",
            "Alex",
            "Tan",
            "01-01-1990",
            "10-01-2024",
            "citizen",
            "male",
            "Chinese",
            "Buddhist",
            "FALSE",
            "FALSE",
            "Therapist",
            "alex.tan@example.com",
            "giro",
            "DBS Bank",
            "123-456-789",
            "001",
        ]
    )
    # Keep date columns explicitly in DD-MM-YYYY format.
    worksheet["E2"].number_format = "DD-MM-YYYY"
    worksheet["F2"].number_format = "DD-MM-YYYY"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="employee_upload_template.xlsx"'
    return response


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    return render(request, "payroll/employee_detail.html", {"employee": employee})


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
    return render(request, "payroll/employee_form.html", {"form": form, "is_edit": True, "employee": employee})


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def employee_delete(request, pk):
    if request.method != "POST":
        return redirect("employee-list")

    employee = get_object_or_404(Employee, pk=pk)
    employee_id = employee.id
    employee_code = employee.employee_code
    employee.delete()
    log_event(
        action="payroll.employee.deleted",
        user=request.user,
        target_type="employee",
        target_id=str(employee_id),
        metadata={"employee_code": employee_code},
        ip_address=get_client_ip(request),
    )
    messages.success(request, "Employee deleted permanently.")
    return redirect("employee-list")


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
    margin = 12 * mm
    width = page_width - (2 * margin)
    top = page_height - margin

    basic_salary = payslip_record.basic_salary or Decimal("0.00")
    physical_products_commission = payslip_record.physical_products_commission or Decimal("0.00")
    credit_commission = payslip_record.credit_commission or Decimal("0.00")
    services_commission = payslip_record.services_commission or Decimal("0.00")
    allowances = payslip_record.allowances or Decimal("0.00")
    loan_deduction = payslip_record.loan_deduction or Decimal("0.00")
    other_deductions = payslip_record.other_deductions or Decimal("0.00")
    deductions = payslip_record.deductions or Decimal("0.00")
    cpf_contribution = payslip_record.cpf_contribution or Decimal("0.00")
    net_salary = payslip_record.net_salary or Decimal("0.00")

    if (
        physical_products_commission == Decimal("0.00")
        and credit_commission == Decimal("0.00")
        and services_commission == Decimal("0.00")
    ):
        services_commission = allowances
    if loan_deduction == Decimal("0.00") and other_deductions == Decimal("0.00"):
        other_deductions = deductions

    total_earnings = basic_salary + allowances
    total_deductions = deductions + cpf_contribution
    employer_cpf_contribution = _calculate_employer_cpf_for_record(payslip_record)
    month_year = payslip_record.payment_date.strftime("%B %Y")

    # Header
    header_h = 34 * mm
    pdf.rect(margin, top - header_h, width, header_h)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(margin + 4 * mm, top - 10 * mm, "Vaniday Pte Ltd - Payslip")
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin + 4 * mm, top - 21 * mm, f"{payslip_record.employee_name} for {month_year}")

    # Employee block
    info_top = top - header_h
    info_h = 24 * mm
    pdf.rect(margin, info_top - info_h, width, info_h)
    label_x = margin + 4 * mm
    value_x = margin + 32 * mm
    employee_y = info_top - 8.5 * mm
    payment_y = info_top - 17 * mm
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(label_x, employee_y, "Employee:")
    pdf.drawString(label_x, payment_y, "Payment Date:")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(value_x, employee_y, payslip_record.employee_name)
    pdf.drawString(value_x, payment_y, payslip_record.payment_date.strftime("%d-%m-%Y"))

    # Table
    table_top = info_top - info_h - 4 * mm
    table_h = 86 * mm
    half = width / 2
    pdf.rect(margin, table_top - table_h, width, table_h)
    pdf.line(margin + half, table_top, margin + half, table_top - table_h)
    head_h = 14 * mm
    pdf.line(margin, table_top - head_h, margin + width, table_top - head_h)

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin + 3 * mm, table_top - 9 * mm, "Earnings")
    pdf.drawString(margin + half - 28 * mm, table_top - 9 * mm, "Amount")
    pdf.drawString(margin + half + 3 * mm, table_top - 9 * mm, "Deductions")
    pdf.drawString(margin + width - 28 * mm, table_top - 9 * mm, "Amount")

    left_amount_x = margin + half - 4 * mm
    right_amount_x = margin + width - 4 * mm
    y = table_top - head_h - 7 * mm
    pdf.setFont("Helvetica", 10.5)

    pdf.drawString(margin + 3 * mm, y, "Basic salary")
    pdf.drawRightString(left_amount_x, y, f"$ {basic_salary:.2f}")
    y -= 8 * mm
    pdf.drawString(margin + 3 * mm, y, "Physical products commission")
    pdf.drawRightString(left_amount_x, y, f"$ {physical_products_commission:.2f}")
    y -= 8 * mm
    pdf.drawString(margin + 3 * mm, y, "Credit commission")
    pdf.drawRightString(left_amount_x, y, f"$ {credit_commission:.2f}")
    y -= 8 * mm
    pdf.drawString(margin + 3 * mm, y, "Services commission")
    pdf.drawRightString(left_amount_x, y, f"$ {services_commission:.2f}")

    y2 = table_top - head_h - 7 * mm
    pdf.drawString(margin + half + 3 * mm, y2, "Loan deduction")
    pdf.drawRightString(right_amount_x, y2, f"$ {loan_deduction:.2f}")
    y2 -= 8 * mm
    pdf.drawString(margin + half + 3 * mm, y2, "Other deductions")
    pdf.drawRightString(right_amount_x, y2, f"$ {other_deductions:.2f}")
    y2 -= 8 * mm
    pdf.drawString(margin + half + 3 * mm, y2, "Employee CPF")
    pdf.drawRightString(right_amount_x, y2, f"$ {cpf_contribution:.2f}")

    totals_line = table_top - table_h + 14 * mm
    pdf.line(margin, totals_line, margin + width, totals_line)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin + 3 * mm, totals_line - 9 * mm, "Total earnings:")
    pdf.drawRightString(left_amount_x, totals_line - 9 * mm, f"$ {total_earnings:.2f}")
    pdf.drawString(margin + half + 3 * mm, totals_line - 9 * mm, "Total deductions:")
    pdf.drawRightString(right_amount_x, totals_line - 9 * mm, f"$ {total_deductions:.2f}")

    # Net pay block
    net_top = table_top - table_h
    net_h = 20 * mm
    pdf.rect(margin, net_top - net_h, width, net_h)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin + half + 3 * mm, net_top - 8 * mm, "Net pay")
    pdf.setFont("Helvetica", 12)
    pdf.drawRightString(right_amount_x, net_top - 8 * mm, f"$ {net_salary:.2f}")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(right_amount_x, net_top - 15 * mm, f"Employer CPF: $ {employer_cpf_contribution:.2f}")

    # Note
    note_top = net_top - net_h
    note_h = 11 * mm
    pdf.rect(margin, note_top - note_h, width, note_h)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(margin + 3 * mm, note_top - 7 * mm, "Note:")

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
