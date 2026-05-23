from django.urls import path

from . import views

urlpatterns = [
    path("", views.payroll_list, name="payroll-list"),
    path("dashboard/", views.payroll_dashboard, name="payroll-dashboard"),
    path("employees/", views.employee_list, name="employee-list"),
    path("employees/create/", views.employee_create, name="employee-create"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee-edit"),
    path("my-payslips/", views.my_payslips, name="my-payslips"),
    path("cpf-preview/", views.payroll_cpf_preview, name="payroll-cpf-preview"),
    path("create/", views.payroll_create, name="payroll-create"),
    path("<int:pk>/", views.payroll_detail, name="payroll-detail"),
    path("<int:pk>/edit/", views.payroll_edit, name="payroll-edit"),
    path("<int:pk>/pdf/", views.payslip_pdf_download, name="payslip-pdf-download"),
    path("<int:pk>/email-send/", views.payslip_email_send, name="payslip-email-send"),
    path("upload-preview/", views.payroll_upload_preview, name="payroll-upload-preview"),
    path("upload-confirm-save/", views.payroll_upload_confirm_save, name="payroll-upload-confirm-save"),
    path("template/", views.payroll_template_download, name="payroll-template-download"),
]
