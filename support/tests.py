from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF
from invoicing.models import Customer, Invoice
from payroll.models import Employee, PayrollRecord

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
        self.assertEqual(ticket.priority, SupportTicket.PRIORITY_HIGH)
        self.assertEqual(ticket.message, "Can I get a payment receipt for PAY-2002?")

    def test_customer_chat_message_saves_guided_reference(self):
        customer = self._make_user("guided_customer", CUSTOMER)
        self.client.force_login(customer)

        response = self.client.post(
            reverse("support-ticket-chat-create"),
            data={
                "category": SupportTicket.CATEGORY_INVOICE,
                "issue_label": "Invoice amount is wrong",
                "related_reference": "INV-CHAT-001",
                "message": "The total amount looks too high.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        ticket = SupportTicket.objects.get(related_reference="INV-CHAT-001")
        self.assertEqual(ticket.subject, "Invoice amount is wrong - INV-CHAT-001")
        self.assertEqual(ticket.category, SupportTicket.CATEGORY_INVOICE)
        self.assertEqual(ticket.created_by, customer)

    def test_customer_invoice_page_shows_chat_widget_without_support_tab(self):
        customer = self._make_user("widget_customer", CUSTOMER)
        invoice_customer = Customer.objects.create(name="Widget Customer", email=customer.email)
        Invoice.objects.create(
            invoice_number="INV-CHAT-001",
            customer=invoice_customer,
            status=Invoice.STATUS_SENT,
            issue_date=date(2026, 6, 1),
            due_date=date(2026, 6, 30),
            total_amount="139.00",
        )
        self.client.force_login(customer)

        response = self.client.get(reverse("customer-invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "support-chat-widget")
        self.assertContains(response, "Vaniday Support")
        self.assertContains(response, "Invoice amount is wrong")
        self.assertContains(response, "INV-CHAT-001")
        self.assertNotContains(response, 'class="portal-nav-link" href="/support/"')

    def test_staff_payslip_page_shows_payroll_chat_reference(self):
        staff = self._make_user("widget_staff", STAFF)
        Employee.objects.create(
            user=staff,
            employee_code="STF-000777",
            first_name="Widget",
            last_name="Staff",
            email=staff.email,
            hire_date=date(2026, 1, 1),
            base_salary="3000.00",
        )
        PayrollRecord.objects.create(
            employee_name="Widget Staff",
            employee_id="STF-000777",
            basic_salary="3000.00",
            net_salary="2800.00",
            payment_date=date(2026, 6, 30),
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("my-payslips"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "support-chat-widget")
        self.assertContains(response, "My payslip looks wrong")
        self.assertContains(response, "STF-000777 / 2026-06-30")

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

    def test_admin_ticket_list_highlights_sla_breached_ticket(self):
        admin = self._make_user("sla_admin", ADMIN)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_INVOICE,
            subject="Old invoice issue",
            message="This has been open too long.",
            created_by=admin,
        )
        ticket.created_at = timezone.now() - timedelta(days=settings.SUPPORT_TICKET_SLA_DAYS + 1)
        ticket.save(update_fields=["created_at"])
        self.client.force_login(admin)

        response = self.client.get(reverse("support-ticket-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "support-ticket-overdue")
        self.assertContains(response, "support-ticket-row")
        self.assertContains(response, "Response Target")
        self.assertNotContains(response, "New Support Request")
        self.assertContains(response, f"reached the {settings.SUPPORT_TICKET_SLA_DAYS} day response target")

    def test_all_breached_tickets_get_overdue_row_class(self):
        admin = self._make_user("all_sla_admin", ADMIN)
        old_created_at = timezone.now() - timedelta(days=settings.SUPPORT_TICKET_SLA_DAYS + 1)
        for index in range(5):
            ticket = SupportTicket.objects.create(
                category=SupportTicket.CATEGORY_INVOICE,
                subject=f"Old issue {index}",
                message="This has been open too long.",
                created_by=admin,
            )
            ticket.created_at = old_created_at
            ticket.save(update_fields=["created_at"])
        self.client.force_login(admin)

        response = self.client.get(reverse("support-ticket-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("support-ticket-overdue"), 5)
        self.assertContains(response, "5 unresolved support tickets reached the 3 day response target.")

    def test_response_target_uses_calendar_days(self):
        admin = self._make_user("calendar_sla_admin", ADMIN)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_INVOICE,
            subject="Calendar day issue",
            message="This should use calendar days.",
            created_by=admin,
        )
        created_date = timezone.localdate() - timedelta(days=settings.SUPPORT_TICKET_SLA_DAYS)
        ticket.created_at = timezone.make_aware(datetime.combine(created_date, time(23, 59, 59)))
        ticket.save(update_fields=["created_at"])

        self.assertEqual(ticket.unresolved_age_days, settings.SUPPORT_TICKET_SLA_DAYS)
        self.assertTrue(ticket.is_sla_breached)

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

    def test_admin_can_assign_ticket_to_role(self):
        admin = self._make_user("support_admin", ADMIN)
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
                "assigned_role": SupportTicket.ASSIGNED_ROLE_FINANCE,
                "resolution_note": "Finance is checking this payment.",
            },
        )

        ticket.refresh_from_db()
        self.assertRedirects(response, reverse("support-ticket-detail", args=[ticket.id]))
        self.assertEqual(ticket.assigned_role, SupportTicket.ASSIGNED_ROLE_FINANCE)
        self.assertIsNone(ticket.assigned_to)
        self.assertEqual(ticket.status, SupportTicket.STATUS_IN_PROGRESS)

    def test_assignment_dropdown_uses_roles_not_users(self):
        admin = self._make_user("role_admin", ADMIN)
        self._make_user("role_finance_user", FINANCE)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_PAYMENT,
            subject="Payment failed",
            message="Stripe payment failed.",
            created_by=admin,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("support-ticket-detail", args=[ticket.id]))

        self.assertContains(response, "Finance")
        self.assertContains(response, "Payroll")
        self.assertContains(response, "Admin")
        self.assertNotContains(response, "role_finance_user")

    def test_closed_status_is_not_available(self):
        admin = self._make_user("status_admin", ADMIN)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_INVOICE,
            subject="Status options",
            message="Check status choices.",
            created_by=admin,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("support-ticket-detail", args=[ticket.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resolved")
        self.assertNotContains(response, "Closed")

    def test_resolved_ticket_is_not_overdue(self):
        admin = self._make_user("resolved_admin", ADMIN)
        ticket = SupportTicket.objects.create(
            category=SupportTicket.CATEGORY_INVOICE,
            subject="Resolved old issue",
            message="This was handled.",
            created_by=admin,
            status=SupportTicket.STATUS_RESOLVED,
        )
        ticket.created_at = timezone.now() - timedelta(days=settings.SUPPORT_TICKET_SLA_DAYS + 1)
        ticket.save(update_fields=["created_at"])

        self.assertTrue(ticket.is_resolved)
        self.assertEqual(ticket.unresolved_age_days, 0)
        self.assertFalse(ticket.is_sla_breached)
