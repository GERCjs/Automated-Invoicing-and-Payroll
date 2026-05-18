from io import BytesIO
from decimal import Decimal, InvalidOperation
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from openpyxl import Workbook

from accounts.permissions import role_required
from accounts.roles import ADMIN, FINANCE, HR, SUPERADMIN

from .forms import EmployeeForm, PayrollRecordForm, PayrollUploadForm
from .models import Employee, PayrollRecord
from .services import (
    TEMPLATE_HEADERS,
    default_template_row,
    employee_cpf_contribution_2026_from_basic_salary,
    parse_payroll_excel,
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
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def payroll_cpf_preview(request):
    employee_id = (request.GET.get("employee_id") or "").strip()
    basic_salary_raw = (request.GET.get("basic_salary") or "").strip()
    payment_date_raw = (request.GET.get("payment_date") or "").strip()

    if not employee_id or not basic_salary_raw or not payment_date_raw:
        return JsonResponse({"ok": False, "cpf_contribution": "", "reason": "missing_inputs"})

    try:
        basic_salary = Decimal(basic_salary_raw)
    except (InvalidOperation, ValueError):
        return JsonResponse({"ok": False, "cpf_contribution": "", "reason": "invalid_salary"})

    try:
        payment_date = date.fromisoformat(payment_date_raw)
    except ValueError:
        return JsonResponse({"ok": False, "cpf_contribution": "", "reason": "invalid_payment_date"})

    employee = Employee.objects.filter(employee_code=employee_id).first()
    if employee is None:
        return JsonResponse({"ok": False, "cpf_contribution": "", "reason": "employee_not_found"})
    if employee.cpf_exempt:
        return JsonResponse({"ok": True, "cpf_contribution": "0.00", "reason": "cpf_exempt"})
    if not employee.date_of_birth:
        return JsonResponse({"ok": False, "cpf_contribution": "", "reason": "missing_dob"})

    cpf_amount = employee_cpf_contribution_2026_from_basic_salary(
        basic_salary=basic_salary,
        dob=employee.date_of_birth,
        payment_date=payment_date,
    )
    return JsonResponse({"ok": True, "cpf_contribution": str(cpf_amount), "reason": "calculated"})


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def payroll_list(request):
    search_query = request.GET.get("q", "").strip()
    payslip_records = PayrollRecord.objects.all()
    if search_query:
        payslip_records = payslip_records.filter(
            Q(employee_name__icontains=search_query)
            | Q(employee_id__icontains=search_query)
        )

    return render(
        request,
        "payroll/payroll_list.html",
        {
            "payslip_records": payslip_records,
            "search_query": search_query,
            "result_count": payslip_records.count(),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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
            if employee.cpf_exempt:
                payslip_record.cpf_contribution = 0
            else:
                payslip_record.cpf_contribution = employee_cpf_contribution_2026_from_basic_salary(
                    basic_salary=payslip_record.basic_salary,
                    dob=employee.date_of_birth,
                    payment_date=payslip_record.payment_date,
                )
            payslip_record.net_salary = (
                payslip_record.basic_salary
                + payslip_record.allowances
                - payslip_record.deductions
                - payslip_record.cpf_contribution
            )
            payslip_record.created_by = request.user
            payslip_record.save()
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
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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
            if employee.cpf_exempt:
                payslip_record.cpf_contribution = 0
            else:
                payslip_record.cpf_contribution = employee_cpf_contribution_2026_from_basic_salary(
                    basic_salary=payslip_record.basic_salary,
                    dob=employee.date_of_birth,
                    payment_date=payslip_record.payment_date,
                )
            payslip_record.net_salary = (
                payslip_record.basic_salary
                + payslip_record.allowances
                - payslip_record.deductions
                - payslip_record.cpf_contribution
            )
            payslip_record.save()
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
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def payroll_detail(request, pk):
    payslip_record = get_object_or_404(PayrollRecord, pk=pk)
    return render(
        request,
        "payroll/payroll_detail.html",
        {"payslip_record": payslip_record},
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
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
            messages.success(request, "Employee saved successfully.")
            return redirect("employee-list")
    else:
        form = EmployeeForm(initial={"employee_code": _generate_next_employee_code()})
    return render(request, "payroll/employee_form.html", {"form": form, "is_edit": False})


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.hire_date = employee.date_of_appointment or employee.hire_date
            employee.save()
            messages.success(request, "Employee updated successfully.")
            return redirect("employee-list")
    else:
        form = EmployeeForm(instance=employee)
    return render(request, "payroll/employee_form.html", {"form": form, "is_edit": True})


@login_required
def my_payslips(request):
    employee = getattr(request.user, "employee_profile", None)
    if employee is None:
        user_email = (request.user.email or "").strip()
        if user_email:
            email_matches = Employee.objects.filter(email__iexact=user_email)
            if email_matches.count() == 1:
                employee = email_matches.first()
                employee.user = request.user
                employee.save(update_fields=["user", "updated_at"])

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
