from django.urls import path

from . import views

urlpatterns = [
    path("payments/", views.payment_stripe_report, name="payment-stripe-report"),
]
