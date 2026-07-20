from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("customer-entry/", views.customer_entry, name="customer-entry"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("ceo-dashboard/", views.ceo_dashboard, name="ceo-dashboard"),
    path("dashboard/audit-logs/", views.audit_log_list, name="dashboard-audit-logs"),
    path("dashboard/validation-errors/", views.validation_error_list, name="dashboard-validation-errors"),
    path("finance-console/", views.finance_console, name="finance-console"),
]
