from django.urls import path

from . import views

# Each path connects a browser/API URL to a function in payments/views.py.
urlpatterns = [
    # Admin users edit the current company bank transfer details here.
    path("bank-transfer/settings/", views.bank_transfer_settings, name="payment-bank-transfer-settings"),
    # Public invoice page posts here to start Stripe Checkout.
    path("checkout/public/<str:token>/", views.checkout_public_invoice, name="payment-checkout-public"),
    # Logged-in customer invoice page posts here to start Stripe Checkout.
    path("checkout/customer/<int:pk>/", views.checkout_customer_invoice, name="payment-checkout-customer"),
    # Finance/admin users post here to refund an invoice payment.
    path("refund/invoice/<int:pk>/", views.refund_invoice_payment, name="payment-refund-invoice"),
    # Finance/admin users post here after verifying a bank transfer reference externally.
    path(
        "bank-transfer/confirm/invoice/<int:pk>/",
        views.confirm_bank_transfer_payment_for_invoice,
        name="payment-bank-transfer-confirm",
    ),
    # Customers submit a bank transfer notice here after transferring money externally.
    path(
        "bank-transfer/notice/customer/<int:pk>/",
        views.submit_customer_bank_transfer_notice,
        name="payment-bank-transfer-notice-customer",
    ),
    # Public invoice viewers submit a bank transfer notice here after transferring money externally.
    path(
        "bank-transfer/notice/public/<str:token>/",
        views.submit_public_bank_transfer_notice,
        name="payment-bank-transfer-notice-public",
    ),
    # Stripe sends the customer back here after checkout succeeds.
    path("checkout/success/", views.checkout_success, name="payment-checkout-success"),
    # Stripe sends the customer back here if checkout is cancelled.
    path("checkout/cancel/", views.checkout_cancel, name="payment-checkout-cancel"),
    # Stripe sends webhook events here in the background.
    path("webhooks/stripe/", views.stripe_webhook, name="payment-stripe-webhook"),
]
