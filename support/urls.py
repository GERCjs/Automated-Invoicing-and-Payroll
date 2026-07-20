from django.urls import path

from .views import (
    customer_invoice_support_ticket_create,
    customer_support_ticket_detail,
    customer_support_ticket_list,
    finance_support_ticket_list,
    staff_payslip_support_ticket_create,
    support_ticket_chat_create,
    support_ticket_create,
    support_ticket_detail,
    support_ticket_list,
    support_ticket_settings_update,
)


urlpatterns = [
    path("finance/", finance_support_ticket_list, name="finance-support-ticket-list"),
    path("settings/", support_ticket_settings_update, name="support-ticket-settings-update"),
    path("my/", customer_support_ticket_list, name="customer-support-ticket-list"),
    path("my/invoices/<int:invoice_id>/new/", customer_invoice_support_ticket_create, name="customer-invoice-support-ticket-create"),
    path("my/payslips/<int:payslip_id>/new/", staff_payslip_support_ticket_create, name="staff-payslip-support-ticket-create"),
    path("my/<int:ticket_id>/", customer_support_ticket_detail, name="customer-support-ticket-detail"),
    path("", support_ticket_list, name="support-ticket-list"),
    path("new/", support_ticket_create, name="support-ticket-create"),
    path("chat/new/", support_ticket_chat_create, name="support-ticket-chat-create"),
    path("<int:ticket_id>/", support_ticket_detail, name="support-ticket-detail"),
]
