from django.contrib import admin

from .models import Employee, PayrollBatch, PayrollEntry, PayrollRecord, PayslipRecord


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "first_name", "last_name", "email", "status")
    list_filter = ("status", "department", "created_at")
    search_fields = ("employee_code", "first_name", "last_name", "email")


@admin.register(PayrollBatch)
class PayrollBatchAdmin(admin.ModelAdmin):
    list_display = ("batch_reference", "period_start", "period_end", "payout_date", "status")
    list_filter = ("status", "payout_date")
    search_fields = ("batch_reference",)


@admin.register(PayrollEntry)
class PayrollEntryAdmin(admin.ModelAdmin):
    list_display = ("batch", "employee", "gross_pay", "deductions", "net_pay", "status")
    list_filter = ("status", "batch")
    search_fields = ("batch__batch_reference", "employee__employee_code")


@admin.register(PayslipRecord)
class PayslipRecordAdmin(admin.ModelAdmin):
    list_display = ("payslip_number", "payroll_entry", "status", "issued_at")
    list_filter = ("status", "issued_at")
    search_fields = ("payslip_number", "payroll_entry__batch__batch_reference")


@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = ("employee_name", "employee_id", "payment_date", "basic_salary", "net_salary")
    list_filter = ("payment_date",)
    search_fields = ("employee_name", "employee_id")
