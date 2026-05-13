from django.urls import path

from . import views

urlpatterns = [
    path("checkout/public/<str:token>/", views.checkout_public_invoice, name="payment-checkout-public"),
    path("checkout/customer/<int:pk>/", views.checkout_customer_invoice, name="payment-checkout-customer"),
    path("checkout/success/", views.checkout_success, name="payment-checkout-success"),
    path("checkout/cancel/", views.checkout_cancel, name="payment-checkout-cancel"),
    path("webhooks/stripe/", views.stripe_webhook, name="payment-stripe-webhook"),
]
