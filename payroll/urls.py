from django.urls import path

from . import views

urlpatterns = [
    path("", views.payroll_list, name="payroll-list"),
    path("dashboard/", views.payroll_dashboard, name="payroll-dashboard"),
    path("template-settings/", views.payroll_template_settings, name="payroll-template-settings"),
    path("employees/dashboard/", views.employee_dashboard, name="employee-dashboard"),
    path("employees/", views.employee_list, name="employee-list"),
    path("employees/create/", views.employee_create, name="employee-create"),
    path("employees/upload-preview/", views.employee_upload_preview, name="employee-upload-preview"),
    path("employees/upload-confirm-save/", views.employee_upload_confirm_save, name="employee-upload-confirm-save"),
    path("employees/template/", views.employee_template_download, name="employee-template-download"),
    path("employees/<int:pk>/", views.employee_detail, name="employee-detail"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee-edit"),
    path("employees/<int:pk>/delete/", views.employee_delete, name="employee-delete"),
    path("my-payslips/", views.my_payslips, name="my-payslips"),
    path("employee-lookup/", views.payroll_employee_lookup, name="payroll-employee-lookup"),
    path("cpf-preview/", views.payroll_cpf_preview, name="payroll-cpf-preview"),
    path("create/", views.payroll_create, name="payroll-create"),
    path("<int:pk>/", views.payroll_detail, name="payroll-detail"),
    path("<int:pk>/edit/", views.payroll_edit, name="payroll-edit"),
    path("<int:pk>/delete/", views.payroll_delete, name="payroll-delete"),
    path("<int:pk>/pdf/", views.payslip_pdf_download, name="payslip-pdf-download"),
    path("<int:pk>/email-send/", views.payslip_email_send, name="payslip-email-send"),
    path("upload-preview/", views.payroll_upload_preview, name="payroll-upload-preview"),
    path("upload-preview/invalid-rows/download/", views.download_invalid_rows, name="payroll-download-invalid-rows"),
    path("upload-confirm-save/", views.payroll_upload_confirm_save, name="payroll-upload-confirm-save"),
    path("template/", views.payroll_template_download, name="payroll-template-download"),
]
