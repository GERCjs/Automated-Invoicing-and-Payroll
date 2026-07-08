from accounts.permissions import get_user_role
from accounts.roles import CUSTOMER, STAFF


def support_chat_options(request):
    if not request.user.is_authenticated:
        return {}

    role = get_user_role(request.user)
    if role == CUSTOMER:
        return {"support_chat_options": _customer_support_chat_options(request.user)}
    if role == STAFF:
        return {"support_chat_options": _staff_support_chat_options(request.user)}
    return {}


def _customer_support_chat_options(user):
    from invoicing.models import Customer, Invoice

    references = []
    customer = Customer.objects.filter(email__iexact=(user.email or "").strip()).first()
    if customer is not None:
        invoices = (
            Invoice.objects.filter(customer=customer)
            .order_by("-issue_date", "-created_at")
            .only("invoice_number", "status", "currency", "total_amount", "issue_date")[:6]
        )
        references = [
            {
                "id": invoice.id,
                "label": f"{invoice.invoice_number} - {invoice.currency} {invoice.total_amount}",
                "value": invoice.invoice_number,
                "meta": invoice.get_status_display(),
            }
            for invoice in invoices
        ]

    return {
        "role": CUSTOMER,
        "references": references,
        "options": [
            {
                "label": "Invoice amount is wrong",
                "category": "invoice",
                "referenceKind": "invoice",
                "prompt": "Which invoice has the issue?",
                "detailPrompt": "Tell us what looks wrong on that invoice.",
            },
            {
                "label": "I have a payment issue",
                "category": "payment",
                "referenceKind": "invoice",
                "prompt": "Which invoice or payment is this about?",
                "detailPrompt": "Tell us what happened with the payment.",
            },
            {
                "label": "I need account help",
                "category": "account",
                "referenceKind": "",
                "prompt": "Tell us what account issue you are facing.",
                "detailPrompt": "Type the account issue here.",
            },
            {
                "label": "Something else",
                "category": "other",
                "referenceKind": "",
                "prompt": "Tell us what you need help with.",
                "detailPrompt": "Type your message here.",
            },
        ],
    }


def _staff_support_chat_options(user):
    from payroll.models import Employee, PayrollRecord

    employee = getattr(user, "employee_profile", None)
    if employee is None:
        user_email = (user.email or "").strip()
        email_matches = Employee.objects.filter(email__iexact=user_email) if user_email else Employee.objects.none()
        employee = email_matches.first() if email_matches.count() == 1 else None

    references = []
    if employee is not None:
        payslips = (
            PayrollRecord.objects.filter(employee_id=employee.employee_code)
            .order_by("-payment_date", "-created_at")
            .only("employee_id", "payment_date", "net_salary")[:6]
        )
        references = [
            {
                "label": f"{payslip.payment_date:%Y-%m-%d} - SGD {payslip.net_salary}",
                "value": f"{payslip.employee_id} / {payslip.payment_date:%Y-%m-%d}",
                "meta": "Payslip",
            }
            for payslip in payslips
        ]

    return {
        "role": STAFF,
        "references": references,
        "options": [
            {
                "label": "My payslip looks wrong",
                "category": "payroll",
                "referenceKind": "payroll",
                "prompt": "Which payslip should HR check?",
                "detailPrompt": "Tell us what looks wrong on that payslip.",
            },
            {
                "label": "I did not receive my pay",
                "category": "payroll",
                "referenceKind": "payroll",
                "prompt": "Which pay period is this about?",
                "detailPrompt": "Tell us what happened with the payment.",
            },
            {
                "label": "I need account help",
                "category": "account",
                "referenceKind": "",
                "prompt": "Tell us what account issue you are facing.",
                "detailPrompt": "Type the account issue here.",
            },
            {
                "label": "Something else",
                "category": "other",
                "referenceKind": "",
                "prompt": "Tell us what you need help with.",
                "detailPrompt": "Type your message here.",
            },
        ],
    }
