from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF

from .models import SupportTicket


class SupportTicketFlowTests(TestCase):
    def _make_user(self, username, role):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="Password12345!",
        )
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def test_admin_can_create_ticket_from_full_page_form(self):
        admin = self._make_user("ticket_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("support-ticket-create"),
            data={
                "category": SupportTicket.CATEGORY_INVOICE,
                "subject": "Invoice amount looks wrong",
                "related_reference": "INV-1001",
                "message": "The invoice total does not match my records.",
            },
        )

        ticket = SupportTicket.objects.get(subject="Invoice amount looks wrong")
        self.assertRedirects(response, reverse("support-ticket-detail", args=[ticket.id]))
        self.assertEqual(ticket.created_by, admin)
        self.assertEqual(ticket.status, SupportTicket.STATUS_OPEN)

    def test_customer_can_create_ticket_from_chat_message(self):
        customer = self._make_user("chat_customer", CUSTOMER)
        self.client.force_login(customer)

        response = self.client.post(
            reverse("support-ticket-chat-create"),
            data={
                "message": "Can I get a payment receipt for PAY-2002?",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        ticket = SupportTicket.objects.get(subject="Can I get a payment receipt for PAY-2002?")
        self.assertEqual(ticket.created_by, customer)
        self.assertEqual(ticket.category, SupportTicket.CATEGORY_PAYMENT)
        self.assertEqual(ticket.message, "Can I get a payment receipt for PAY-2002?")

    def test_customer_invoice_page_shows_chat_widget_without_support_tab(self):
        customer = self._make_user("widget_customer", CUSTOMER)
        self.client.force_login(customer)

        response = self.client.get(reverse("customer-invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "support-chat-widget")
        self.assertContains(response, "Vaniday Support")
        self.assertNotContains(response, 'class="portal-nav-link" href="/support/"')

    def test_customer_cannot_view_support_ticket_history(self):
        customer = self._make_user("history_customer", CUSTOMER)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_INVOICE,
            subject="Invoice help",
            message="Please check this invoice.",
            created_by=customer,
        )
        self.client.force_login(customer)

        list_response = self.client.get(reverse("support-ticket-list"))
        detail_response = self.client.get(reverse("support-ticket-detail", args=[ticket.id]))

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(detail_response.status_code, 403)

    def test_finance_can_view_invoice_ticket_but_hr_cannot(self):
        customer = self._make_user("invoice_customer", CUSTOMER)
        finance = self._make_user("finance_handler", FINANCE)
        hr = self._make_user("hr_handler", HR)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_INVOICE,
            subject="Invoice help",
            message="Please check this invoice.",
            created_by=customer,
        )

        self.client.force_login(finance)
        finance_response = self.client.get(reverse("support-ticket-detail", args=[ticket.id]))
        self.assertEqual(finance_response.status_code, 200)

        self.client.force_login(hr)
        hr_response = self.client.get(reverse("support-ticket-detail", args=[ticket.id]))
        self.assertEqual(hr_response.status_code, 404)

    def test_admin_can_assign_ticket(self):
        admin = self._make_user("support_admin", ADMIN)
        finance = self._make_user("assign_finance", FINANCE)
        staff = self._make_user("support_staff", STAFF)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_PAYMENT,
            subject="Payment failed",
            message="Stripe payment failed.",
            created_by=staff,
        )

        self.client.force_login(admin)
        response = self.client.post(
            reverse("support-ticket-detail", args=[ticket.id]),
            data={
                "status": SupportTicket.STATUS_IN_PROGRESS,
                "priority": SupportTicket.PRIORITY_HIGH,
                "assigned_to": finance.id,
                "resolution_note": "Finance is checking this payment.",
            },
        )

        ticket.refresh_from_db()
        self.assertRedirects(response, reverse("support-ticket-detail", args=[ticket.id]))
        self.assertEqual(ticket.assigned_to, finance)
        self.assertEqual(ticket.status, SupportTicket.STATUS_IN_PROGRESS)
