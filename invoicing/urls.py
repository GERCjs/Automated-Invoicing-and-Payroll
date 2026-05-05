from django.urls import path

from . import views

urlpatterns = [
    path("", views.invoice_list, name="invoice-list"),
    path("create/", views.invoice_create, name="invoice-create"),
    path("<int:pk>/", views.invoice_detail, name="invoice-detail"),
    path("<int:pk>/edit/", views.invoice_edit, name="invoice-edit"),
    path("<int:pk>/send-email/", views.invoice_send_email, name="invoice-send-email"),
    path("<int:pk>/status/", views.invoice_status_update, name="invoice-status-update"),
    path("<int:pk>/download/pdf/", views.invoice_download_pdf, name="invoice-download-pdf"),
    path("<int:pk>/download/excel/", views.invoice_download_excel, name="invoice-download-excel"),
    path("view/<str:token>/", views.invoice_public_view, name="invoice-public-view"),
]
