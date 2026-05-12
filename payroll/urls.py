from django.urls import path

from . import views

urlpatterns = [
    path("upload-preview/", views.payroll_upload_preview, name="payroll-upload-preview"),
    path("template/", views.payroll_template_download, name="payroll-template-download"),
]
