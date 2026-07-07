from django.conf import settings
from django.conf.urls.static import static
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
