from django.urls import path

from . import views

urlpatterns = [
    path("invoice-customer/", views.invoice_customer_report, name="invoice-customer-report"),
    path("admin-security/", views.admin_security_report, name="admin-security-report"),
    path("payments/", views.payment_stripe_report, name="payment-stripe-report"),
    path("payroll/", views.payroll_report, name="payroll-report"),
]
