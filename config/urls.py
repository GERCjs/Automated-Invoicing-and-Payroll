from django.urls import include, path


urlpatterns = [
    path("accounts/", include("accounts.urls")),
    path("invoices/", include("invoicing.urls")),
    path("payments/", include("payments.urls")),
    path("payroll/", include("payroll.urls")),
    path("reports/", include("reports.urls")),
    path("support/", include("support.urls")),
    path("", include("core.urls")),
]
