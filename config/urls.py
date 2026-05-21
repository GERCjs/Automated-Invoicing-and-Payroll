from django.contrib import admin
from django.urls import include, path


def superadmin_only_admin_access(request):
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = superadmin_only_admin_access

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("invoices/", include("invoicing.urls")),
    path("payments/", include("payments.urls")),
    path("payroll/", include("payroll.urls")),
    path("reports/", include("reports.urls")),
    path("", include("core.urls")),
]
